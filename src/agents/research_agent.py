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

from src.llm_claude_code import claude_code_research
from src.agents.report_utils import extract_conclusion

REACT_SYSTEM = """你是一個台股研究分析師兼個人助理，可以使用以下工具：

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
"""


_SYMBOL_RE = re.compile(r"\b\d{4}\.TW\b")


async def run_research(user_message: str, conversation_history: list[dict]) -> dict:
    """ReAct loop：Claude Code 自主決定呼叫哪些 MCP 工具直到得出結論。"""
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

    text = await claude_code_research(REACT_SYSTEM, history, user_message)
    report, conclusion = extract_conclusion(text)
    used_symbols = list(dict.fromkeys(_SYMBOL_RE.findall(report)))
    logger.info(f"ResearchAgent: done, symbols={used_symbols}")
    return {"final_report": report, "conclusion": conclusion, "sources": [], "target_symbols": used_symbols}
