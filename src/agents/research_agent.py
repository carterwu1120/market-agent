"""Research Agent — LangGraph native ReAct loop for complex/comparative queries.

適合處理開放式問題，例如：
- 「比較半導體和航運哪個現在更值得投資？」
- 「幫我找機器人題材中技術面最強的股票」
- 「今天哪個類股表現最好？」

LLM 自主決定呼叫哪些工具、呼叫幾次，直到有足夠資訊回答。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.prebuilt import ToolNode
from loguru import logger

from src.agents.state import AgentState
from src.llm import get_langchain_llm
from src.tools.sector_data import get_sector_symbols
from src.tools.theme_search import search_theme_stocks
from src.tools.stock_data import get_technical_indicators, get_fundamental_data
from src.tools.chip_data import get_institutional_trading, get_margin_trading
from src.tools.company_insight import get_company_insights
from src.tools.discord_tools import send_channel_message, send_dm as _discord_send_dm
from src.tools.gmail_tools import create_draft as _gmail_create_draft, send_email as _gmail_send_email

MAX_ITERATIONS = 6  # 防止無限循環


# ── Tool 定義 ────────────────────────────────────────────────────────────────

@tool
async def sector_lookup(keyword: str) -> str:
    """查詢 TWSE 官方產業類股的成份股。適用於半導體、航運、金融、鋼鐵等官方產業關鍵字。"""
    result = await get_sector_symbols(keyword, max_symbols=8)
    symbols = result.get("symbols", [])
    names = result.get("sector_names", [])
    if not symbols:
        return f"找不到「{keyword}」相關的官方產業類股"
    return f"產業：{', '.join(names)} | 代表股：{', '.join(symbols)}"


@tool
async def theme_lookup(keyword: str) -> str:
    """查詢市場主題/概念股。適用於機器人、元宇宙、低軌衛星、AI、電動車等題材關鍵字。"""
    result = await search_theme_stocks(keyword, max_symbols=8)
    symbols = result.get("symbols", [])
    matched = result.get("matched_concept", keyword)
    if not symbols:
        return f"找不到「{keyword}」相關概念股"
    return f"概念：{matched} | 個股：{', '.join(symbols)}"


@tool
async def technical_analysis(symbol: str) -> str:
    """查詢個股技術面指標：現價、RSI、MACD、均線、乖離率、布林帶。symbol 格式：2330.TW"""
    from src.tools.stock_data import get_stock_price
    ind, price = await asyncio.gather(
        get_technical_indicators(symbol),
        get_stock_price(symbol),
        return_exceptions=True,
    )
    if isinstance(ind, Exception) or (isinstance(ind, dict) and ind.get("error")):
        return f"{symbol} 技術面資料取得失敗：{ind}"
    # Persist to DB only when price fetch also succeeded to avoid NULLing existing close/volume fields
    price_ok = isinstance(price, dict) and not price.get("error")
    if price_ok:
        from src.memory.stock_store import upsert_daily_price
        asyncio.ensure_future(upsert_daily_price([{
            "symbol": symbol,
            "price": price,
            "indicators": ind,
        }]))
    return (
        f"{symbol} | 現價: {ind.get('close')} | RSI: {ind.get('rsi_14')} | "
        f"MACD: {ind.get('macd')} | MA20: {ind.get('sma_20')} | MA60: {ind.get('sma_60')} | "
        f"乖離率(20): {ind.get('bias_20')}% | 乖離率(60): {ind.get('bias_60')}% | "
        f"布林上軌: {ind.get('bb_upper')} | 下軌: {ind.get('bb_lower')}"
    )


@tool
async def fundamental_analysis(symbol: str) -> str:
    """查詢個股基本面：本益比、股價淨值比、EPS、ROE、營收成長、分析師評等。symbol 格式：2330.TW"""
    data = await get_fundamental_data(symbol)
    if data.get("error"):
        return f"{symbol} 基本面資料取得失敗：{data['error']}"
    # Persist to DB (fire-and-forget)
    from src.memory.stock_store import upsert_daily_fundamental
    asyncio.ensure_future(upsert_daily_fundamental([data]))
    return (
        f"{symbol} {data.get('company_name', '')} | "
        f"PE: {data.get('pe_ratio')} | PB: {data.get('pb_ratio')} | "
        f"EPS: {data.get('eps_ttm')} | ROE: {data.get('roe')} | "
        f"營收成長: {data.get('revenue_growth')} | 毛利率: {data.get('gross_margin')} | "
        f"目標價: {data.get('analyst_target')} | 評等: {data.get('analyst_recommendation')}"
    )


@tool
async def company_news(symbol: str) -> str:
    """查詢個股法說會、技術突破、產品新聞。symbol 格式：2330.TW"""
    result = await get_company_insights(symbol, max_articles=5)
    articles = result.get("articles", [])
    if not articles:
        return f"{symbol} 暫無相關法說會或技術新聞"
    lines = [f"{result.get('company_name', symbol)} 最新消息："]
    for a in articles:
        lines.append(f"- {a['title']}")
    return "\n".join(lines)


@tool
async def chip_analysis(symbol: str) -> str:
    """查詢個股即時籌碼面：三大法人買賣超（外資/投信/自營商）、融資融券餘額。symbol 格式：2330.TW"""
    inst, margin = await asyncio.gather(
        get_institutional_trading(symbol),
        get_margin_trading(symbol),
        return_exceptions=True,
    )
    inst_ok = not isinstance(inst, Exception) and not (isinstance(inst, dict) and inst.get("error"))
    margin_ok = not isinstance(margin, Exception) and not (isinstance(margin, dict) and margin.get("error"))

    # Persist only when both fetches succeeded to avoid NULLing existing margin balance fields
    if inst_ok and margin_ok:
        from src.memory.stock_store import upsert_daily_chip
        asyncio.ensure_future(upsert_daily_chip([{
            "symbol": symbol,
            "institutional": inst,
            "margin": margin,
        }]))

    parts = [f"{symbol} 籌碼面："]
    if not inst_ok:
        parts.append("  三大法人：資料取得失敗")
    else:
        parts.append(
            f"  [{inst.get('date', 'N/A')}] 外資:{inst.get('foreign_net')} "
            f"投信:{inst.get('trust_net')} 自營:{inst.get('dealer_net')} "
            f"合計:{inst.get('total_3_institutions')}"
        )
    if not margin_ok:
        parts.append("  融資融券：資料取得失敗")
    else:
        parts.append(
            f"  融資餘額:{margin.get('margin_buy_balance')} "
            f"融券餘額:{margin.get('short_sell_balance')}"
        )
    return "\n".join(parts)


@tool
async def stock_history(symbol: str, days: int = 7) -> str:
    """查詢個股歷史快照（收盤價/均線/法人動向），資料來自本系統每日儲存的 DB 記錄。
    若 DB 無資料，說明原因。symbol 格式：2330.TW"""
    days = max(1, min(days, 90))
    from src.memory.stock_store import query_stock_history
    data = await query_stock_history(symbol, days=days)
    price = data.get("price_history", [])
    chip = data.get("chip_history", [])
    fund = data.get("fundamental_history", [])
    if not price and not chip and not fund:
        return f"{symbol} DB 尚無歷史記錄（每日 brief 會自動建立；若需即時數據請用 technical_analysis 或 chip_analysis）"
    lines = [f"{symbol} 最近 {days} 個日曆天內的交易日快照："]
    for r in price:
        lines.append(f"  {r['date']} 收盤:{r.get('close')} MA20:{r.get('sma_20')} RSI:{r.get('rsi_14')}")
    for r in chip:
        lines.append(f"  {r['date']} 外資:{r.get('foreign_net')} 投信:{r.get('trust_net')} 三大:{r.get('total_3_institutions')}")
    for r in fund:
        lines.append(f"  {r['date']} PE:{r.get('pe_ratio')} PB:{r.get('pb_ratio')} EPS:{r.get('eps_ttm')} ROE:{r.get('roe')}")
    return "\n".join(lines)


@tool
async def discord_message(channel_id: str, message: str, mention_user_ids: str = "") -> str:
    """傳訊息到 Discord 頻道。channel_id 為頻道 ID（數字）。mention_user_ids 用逗號分隔多個 Discord user ID，留空則不 @。"""
    mentions = [uid.strip() for uid in mention_user_ids.split(",") if uid.strip()]
    result = await send_channel_message(channel_id, message, mention_user_ids=mentions or None)
    if result.get("error"):
        return f"Discord 傳送失敗：{result['error']}"
    return f"訊息已傳送至頻道 {channel_id}（message_id: {result.get('message_id')}）"


@tool
async def discord_dm(user_id: str, message: str) -> str:
    """私訊 Discord 用戶。user_id 填對方的 Discord user ID；若要私訊主人（bot 擁有者），填 'owner'。"""
    result = await _discord_send_dm(user_id, message)
    if result.get("error"):
        return f"Discord DM 失敗：{result['error']}"
    return f"私訊已送出（user_id: {result.get('user_id')}，message_id: {result.get('message_id')}）"


@tool
async def gmail_draft(to: str, subject: str, body: str) -> str:
    """建立 Gmail 草稿。回傳草稿內容供用戶確認，確認後再呼叫 gmail_send 寄出。
    to: 收件人 email。subject: 主旨。body: 信件內文。"""
    result = await _gmail_create_draft(to, subject, body)
    if result.get("error"):
        return f"草稿建立失敗：{result['error']}"
    return (
        f"草稿已建立（draft_id: {result['draft_id']}）\n"
        f"收件人：{to}\n主旨：{subject}\n\n{body}\n\n"
        f"請確認內容，說「寄出」後我會呼叫 gmail_send 寄送。"
    )


@tool
async def gmail_send(to: str, subject: str, body: str) -> str:
    """直接寄送 Email。通常在用戶確認草稿後才呼叫。
    to: 收件人 email。subject: 主旨。body: 信件內文。"""
    result = await _gmail_send_email(to, subject, body)
    if result.get("error"):
        return f"寄送失敗：{result['error']}"
    return f"Email 已寄出至 {to}（message_id: {result.get('message_id')}）"


# ── ReAct 系統提示 ──────────────────────────────────────────────────────────

REACT_SYSTEM = """你是一個台股研究分析師兼個人助理，可以使用以下工具：

【資料查詢】
- sector_lookup(keyword): 查 TWSE 官方產業類股（半導體、航運、金融…）
- theme_lookup(keyword): 查市場主題/概念股（機器人、AI、電動車…）
- technical_analysis(symbol): 查個股技術面（RSI、MACD、均線、乖離率…）
- fundamental_analysis(symbol): 查個股基本面（PE、ROE、營收成長…）
- company_news(symbol): 查個股法說會與技術新聞
- chip_analysis(symbol): 查個股即時籌碼面（三大法人買賣超、融資融券）
- stock_history(symbol, days): 查個股歷史快照（本系統 DB 記錄，有資料才有）

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
"""


# ── Agent Node ───────────────────────────────────────────────────────────────

_TOOLS = [
    sector_lookup, theme_lookup, technical_analysis, fundamental_analysis,
    chip_analysis, company_news, stock_history,
    discord_message, discord_dm, gmail_draft, gmail_send,
]
_tool_node = ToolNode(_TOOLS)


def _extract_text(content: Any) -> str:
    """Normalize LLM response content to plain string.
    Gemini returns list of content blocks: [{'type': 'text', 'text': '...'}]
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(p for p in parts if p)
    return str(content)


async def research_agent_node(state: AgentState) -> dict:
    """ReAct loop：LLM 自主決定呼叫哪些工具直到得出結論。"""
    logger.info("ResearchAgent: starting ReAct loop")

    llm = get_langchain_llm().bind_tools(_TOOLS)

    # Inject conversation history so LLM can do context-aware research
    history_messages = []
    for m in (state.conversation_history or [])[-6:]:
        if m.get("role") not in ("user", "assistant"):
            continue
        content = m.get("content", "")
        meta = m.get("meta") or {}
        symbols = meta.get("symbols", [])
        if symbols and m["role"] == "assistant":
            content = f"[分析標的: {', '.join(symbols)}]\n{content}"
        if m["role"] == "user":
            history_messages.append(HumanMessage(content=content))
        else:
            history_messages.append(AIMessage(content=content))

    messages = [
        SystemMessage(content=REACT_SYSTEM),
        *history_messages,
        HumanMessage(content=state.user_message),
    ]

    iterations = 0
    used_symbols: list[str] = []

    while iterations < MAX_ITERATIONS:
        iterations += 1
        response: AIMessage = await llm.ainvoke(messages)
        messages.append(response)

        # Collect symbols from tool calls for state persistence
        for tc in (response.tool_calls or []):
            sym = tc.get("args", {}).get("symbol")
            if sym and sym not in used_symbols:
                used_symbols.append(sym)

        # 沒有 tool_calls → LLM 完成推理，輸出最終答案
        if not response.tool_calls:
            logger.info(f"ResearchAgent: done after {iterations} iterations, symbols={used_symbols}")
            return {
                "final_report": _extract_text(response.content),
                "conclusion": _extract_text(response.content)[-600:],
                "sources": [],
                "target_symbols": used_symbols,
            }

        # 執行 tool calls
        logger.info(f"ResearchAgent iter {iterations}: calling {[tc['name'] for tc in response.tool_calls]}")
        tool_results = await _tool_node.ainvoke({"messages": messages})
        messages.extend(tool_results["messages"])

    # 超過最大迭代次數，強制用目前資訊生成報告
    logger.warning("ResearchAgent: max iterations reached, forcing conclusion")
    final = await llm.ainvoke([
        *messages,
        HumanMessage(content="根據以上收集到的資料，請直接給出最終分析結論。"),
    ])
    text = _extract_text(final.content)
    return {"final_report": text, "conclusion": text[-600:], "sources": [], "target_symbols": used_symbols}
