"""Research Agent — hands off to Claude Code's native ReAct loop for complex/comparative queries.

適合處理開放式問題，例如：
- 「比較半導體和航運哪個現在更值得投資？」
- 「幫我找機器人題材中技術面最強的股票」
- 「今天哪個類股表現最好？」

實際的工具呼叫迴圈由 `claude -p --mcp-config` 執行（見 src/mcp_server.py 暴露的工具），
這裡只負責組裝對話歷史、呼叫 claude_code_research，並從結果中解析出用到的股票代號。
"""

from __future__ import annotations

import re

from loguru import logger

from src.agents.report_utils import extract_conclusion
from src.llm_claude_code import USER_FACING_TOOL_NAMES, claude_code_research
from src.tools.knowledge_base import read_knowledge_base
from src.tools.news_fetcher import _TICKER_NAMES

# Deterministic company-name -> ticker hints for react's system prompt.
# Without this, ticker resolution for a named company relies entirely on
# Claude's own world knowledge with no safety net against a wrong or stale
# code (the old orchestrator had a dedicated lookup dict for this).
_COMPANY_CODE_HINTS = "\n".join(
    f"- {code}.TW：{'、'.join(names)}" for code, names in _TICKER_NAMES.items()
)

REACT_SYSTEM = f"""你是一個台股研究分析師兼個人助理，可以使用以下工具：

【常見公司代號對照】（優先使用；不在表中的公司才依你自己的知識判斷，
且務必用 sector_lookup/theme_lookup 或其他工具的回傳結果核對，不可憑空生成代號）
{_COMPANY_CODE_HINTS}

【資料查詢】
- sector_lookup(keyword): 查 TWSE 官方產業類股（半導體、航運、金融…）
- theme_lookup(keyword): 查市場主題/概念股（機器人、AI、電動車…）
- technical_analysis(symbol): 查個股技術面（RSI、MACD、均線、乖離率…）
- fundamental_analysis(symbol): 查個股基本面（PE、ROE、營收成長…）
- company_news(symbol): 查個股法說會與技術新聞
- chip_analysis(symbol): 查個股即時籌碼面（三大法人買賣超、融資融券）
- stock_history(symbol, days): 查個股歷史快照（本系統 DB 記錄，有資料才有）
- web_search(query, max_results): 開放網頁搜尋，自己下關鍵字，補充固定工具沒涵蓋的新聞事件/市場氛圍
- company_announcements(symbol): 查公司「今天」的重大訊息公告（TWSE 官方 MOPS，只有今天）
- company_financial_summary(symbol): 查公司「最新一期」公開財報（TWSE 官方 MOPS，只有最新一季）

【訊息發送】
- discord_message(channel_id, message, mention_user_ids): 傳訊息到 Discord 頻道，可 @ 指定用戶
- discord_dm(user_id, message): 私訊 Discord 用戶；user_id='owner' 為主人
- gmail_draft(to, subject, body): 建立 Gmail 草稿並顯示內容供確認
- gmail_send(to, subject, body): 寄送 Email（用戶確認草稿後才呼叫）

【紙上交易】（模擬帳戶，非真實下單，不牽涉真實資金，起始本金與可用現金見 paper_trade_status）
- paper_trade_status(): 查詢目前持倉狀況（持有中部位的浮動損益、短線/長期分類、已平倉紀錄、
  股數）與模擬帳戶目前可用現金/總資產/累計報酬。下單前先查這個確認額度。
- paper_trade_buy(symbol, reason, horizon, allocation_pct): 買進，價格一律用系統即時股價，
  不接受自行指定。horizon 必須明確判斷並指定：short_term（短線操作，之後每輪都會被緊盯、
  由你自己決定出場時機）或 long_term（長期持有，之後只會用較低頻率的資訊追蹤，不會每輪都
  問你要不要出場）。allocation_pct 是這筆要用模擬本金的百分之多少去買，依信心程度自行判斷，
  系統會自動夾在允許範圍內，不會整筆失敗；模擬現金不足時交易會被拒絕。
  買進後會自動從觀察名單移除。短線與長期部位各自有同時持有檔數上限，達上限時會回傳
  錯誤訊息，須先賣出既有部位才能再買進，不要重複嘗試。
- paper_trade_sell(symbol, reason, exit_reason): 賣出，exit_reason 只能是
  take_profit（停利）、stop_loss（停損）、llm_signal（其他判斷）三選一
- watchlist_drop(symbol, reason): 把一支股票從觀察名單移除，代表你判斷不用再追蹤了
  （決定不交易，或已經處理完畢）。這是移除觀察名單的主要方式，不要放著等自動過期。

策略：
1. 股票查詢：先用 sector_lookup 或 theme_lookup 找代碼，再分析數據
2. 收集足夠資料後，直接輸出分析結論，不要再呼叫工具
3. Email 流程：先呼叫 gmail_draft 讓用戶確認，用戶說「寄出」後才呼叫 gmail_send
4. 回答使用繁體中文，每個判斷都要引用工具回傳的數據
5. 嚴禁使用工具之外的自身知識補充數字或技術描述
6. 【重要】對話歷史中的數字僅供理解問題脈絡，不可直接引用為當前數據。
   若需要某支股票的數據，必須在本輪呼叫工具重新取得，不得使用歷史對話中的舊數字。
7. 回答結尾必須包含 CONCLUSION_SUMMARY: ... END_CONCLUSION 區塊，用 2-3 句繁體中文總結這次分析的結論。
8. web_search 的結果僅供參考背景與事件脈絡，股價/財報/籌碼數字一律以其他固定工具
   （technical_analysis/fundamental_analysis/chip_analysis/company_financial_summary）為準；
   company_announcements 與 company_financial_summary 只有「今天/最新一期」的資料，不能拿來回答歷史問題。
9. 【紙上交易決策紀律】呼叫 paper_trade_buy 或 paper_trade_sell 之前，務必先呼叫
   technical_analysis 與 company_announcements 確認該股當下技術面與有無重大訊息；
   時間許可時也應查 fundamental_analysis 與 chip_analysis。不可只憑新聞標題或片段資訊
   就倉促下單。若手上已有其他持倉，先呼叫 paper_trade_status 掌握現況再決定。
10. 【觀察名單管理】針對觀察名單中的股票，若你判斷不值得追蹤了（技術面轉弱、
    消息面利空、或任何理由），請主動呼叫 watchlist_drop 移除，不要放著不管。
    這是你主動管理名單的責任，不是系統自動做的事。
"""


_SYMBOL_RE = re.compile(r"\b\d{4}\.TW\b")


async def run_research(
    user_message: str,
    conversation_history: list[dict],
    tool_names: list[str] = USER_FACING_TOOL_NAMES,
) -> dict:
    """ReAct loop：Claude Code 自主決定呼叫哪些 MCP 工具直到得出結論。

    tool_names 預設不含紙上交易工具——一般使用者觸發的問答不該有能力下單。
    只有 paper_trading_loop.py 會明確傳入包含交易工具的完整清單。
    """
    logger.info("ResearchAgent: starting ReAct loop (claude_code MCP backend)")

    history = []
    for m in (conversation_history or [])[-6:]:
        if m.get("role") not in ("user", "assistant"):
            continue
        content = m.get("content", "")
        meta = m.get("meta") or {}
        symbols = meta.get("symbols", [])
        if symbols and m["role"] == "assistant":
            content = f"[分析標的: {', '.join(symbols)}]\n{content}"
        history.append({"role": m["role"], "content": content})

    system = REACT_SYSTEM
    # Injected unconditionally (not gated by tool_names) -- these notes are
    # the user's own judgment criteria, not optional flavor text. Scoping
    # this to only trading calls was tried and reverted: it risks the agent
    # silently missing the user's own strategy on a plain /stock judgment
    # question ("該不該買？"), which defeats the entire point of having
    # this file. The token cost (~3-4K) is accepted as the price of that
    # guarantee, same reasoning as why daily_brief/paper_trading_loop don't
    # let the LLM opt out of other required data sources.
    kb_context = read_knowledge_base()
    if kb_context:
        system += (
            "\n\n【個人策略筆記】（使用者提供的知識庫，判斷/決策時應納入考量，"
            "與工具查到的即時數據互相對照，不可取代即時數據）\n" + kb_context
        )

    payload = await claude_code_research(system, history, user_message, tool_names=tool_names)
    report, conclusion = extract_conclusion(payload["result"])
    used_symbols = list(dict.fromkeys(_SYMBOL_RE.findall(report)))
    logger.info(f"ResearchAgent: done, symbols={used_symbols}, cost=${payload['cost_usd']:.4f}")
    return {
        "final_report": report,
        "conclusion": conclusion,
        "sources": [],
        "target_symbols": used_symbols,
        "cost_usd": payload["cost_usd"],
    }
