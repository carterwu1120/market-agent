"""MCP server exposing market-agent's data/messaging tools to Claude Code.

Run standalone (stdio transport) so `claude -p --mcp-config ...` or the
Claude Desktop / Claude Code client can discover and call these tools
via real MCP tool-calling — no hand-rolled JSON-action protocol.

Tool bodies are unchanged from src/agents/research_agent.py; only the
registration mechanism (LangChain @tool -> MCP @mcp.tool) differs.
"""

from __future__ import annotations

import asyncio

from mcp.server.mcpserver import MCPServer

from src.tools.sector_data import get_sector_symbols
from src.tools.theme_search import search_theme_stocks
from src.tools.stock_data import get_technical_indicators, get_fundamental_data, get_stock_price
from src.tools.chip_data import get_institutional_trading, get_margin_trading
from src.tools.company_insight import get_company_insights
from src.tools.discord_tools import send_channel_message, send_dm as _discord_send_dm
from src.tools.gmail_tools import create_draft as _gmail_create_draft, send_email as _gmail_send_email

mcp = MCPServer("market-agent-tools")


# ── 資料查詢 ──────────────────────────────────────────────────────────────

@mcp.tool()
async def sector_lookup(keyword: str) -> str:
    """查詢 TWSE 官方產業類股的成份股。適用於半導體、航運、金融、鋼鐵等官方產業關鍵字。"""
    result = await get_sector_symbols(keyword, max_symbols=8)
    symbols = result.get("symbols", [])
    names = result.get("sector_names", [])
    if not symbols:
        return f"找不到「{keyword}」相關的官方產業類股"
    return f"產業：{', '.join(names)} | 代表股：{', '.join(symbols)}"


@mcp.tool()
async def theme_lookup(keyword: str) -> str:
    """查詢市場主題/概念股。適用於機器人、元宇宙、低軌衛星、AI、電動車等題材關鍵字。"""
    result = await search_theme_stocks(keyword, max_symbols=8)
    symbols = result.get("symbols", [])
    matched = result.get("matched_concept", keyword)
    if not symbols:
        return f"找不到「{keyword}」相關概念股"
    return f"概念：{matched} | 個股：{', '.join(symbols)}"


@mcp.tool()
async def technical_analysis(symbol: str) -> str:
    """查詢個股技術面指標：現價、RSI、MACD、均線、乖離率、布林帶。symbol 格式：2330.TW"""
    ind, price = await asyncio.gather(
        get_technical_indicators(symbol),
        get_stock_price(symbol),
        return_exceptions=True,
    )
    if isinstance(ind, Exception) or (isinstance(ind, dict) and ind.get("error")):
        return f"{symbol} 技術面資料取得失敗：{ind}"
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


@mcp.tool()
async def fundamental_analysis(symbol: str) -> str:
    """查詢個股基本面：本益比、股價淨值比、EPS、ROE、營收成長、分析師評等。symbol 格式：2330.TW"""
    data = await get_fundamental_data(symbol)
    if data.get("error"):
        return f"{symbol} 基本面資料取得失敗：{data['error']}"
    from src.memory.stock_store import upsert_daily_fundamental
    asyncio.ensure_future(upsert_daily_fundamental([data]))
    return (
        f"{symbol} {data.get('company_name', '')} | "
        f"PE: {data.get('pe_ratio')} | PB: {data.get('pb_ratio')} | "
        f"EPS: {data.get('eps_ttm')} | ROE: {data.get('roe')} | "
        f"營收成長: {data.get('revenue_growth')} | 毛利率: {data.get('gross_margin')} | "
        f"目標價: {data.get('analyst_target')} | 評等: {data.get('analyst_recommendation')}"
    )


@mcp.tool()
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


@mcp.tool()
async def chip_analysis(symbol: str) -> str:
    """查詢個股即時籌碼面：三大法人買賣超（外資/投信/自營商）、融資融券餘額。symbol 格式：2330.TW"""
    inst, margin = await asyncio.gather(
        get_institutional_trading(symbol),
        get_margin_trading(symbol),
        return_exceptions=True,
    )
    inst_ok = not isinstance(inst, Exception) and not (isinstance(inst, dict) and inst.get("error"))
    margin_ok = not isinstance(margin, Exception) and not (isinstance(margin, dict) and margin.get("error"))

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


@mcp.tool()
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


# ── 訊息發送 ──────────────────────────────────────────────────────────────

@mcp.tool()
async def discord_message(channel_id: str, message: str, mention_user_ids: str = "") -> str:
    """傳訊息到 Discord 頻道。channel_id 為頻道 ID（數字）。mention_user_ids 用逗號分隔多個 Discord user ID，留空則不 @。"""
    mentions = [uid.strip() for uid in mention_user_ids.split(",") if uid.strip()]
    result = await send_channel_message(channel_id, message, mention_user_ids=mentions or None)
    if result.get("error"):
        return f"Discord 傳送失敗：{result['error']}"
    return f"訊息已傳送至頻道 {channel_id}（message_id: {result.get('message_id')}）"


@mcp.tool()
async def discord_dm(user_id: str, message: str) -> str:
    """私訊 Discord 用戶。user_id 填對方的 Discord user ID；若要私訊主人（bot 擁有者），填 'owner'。"""
    result = await _discord_send_dm(user_id, message)
    if result.get("error"):
        return f"Discord DM 失敗：{result['error']}"
    return f"私訊已送出（user_id: {result.get('user_id')}，message_id: {result.get('message_id')}）"


@mcp.tool()
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


@mcp.tool()
async def gmail_send(to: str, subject: str, body: str) -> str:
    """直接寄送 Email。通常在用戶確認草稿後才呼叫。
    to: 收件人 email。subject: 主旨。body: 信件內文。"""
    result = await _gmail_send_email(to, subject, body)
    if result.get("error"):
        return f"寄送失敗：{result['error']}"
    return f"Email 已寄出至 {to}（message_id: {result.get('message_id')}）"


if __name__ == "__main__":
    mcp.run(transport="stdio")
