"""MCP server exposing market-agent's data/messaging tools to Claude Code.

Run standalone (stdio transport) so `claude -p --mcp-config ...` or the
Claude Desktop / Claude Code client can discover and call these tools
via real MCP tool-calling — no hand-rolled JSON-action protocol.

Tool bodies are unchanged from src/agents/research_agent.py; only the
registration mechanism (LangChain @tool -> MCP @mcp.tool) differs.
"""

from __future__ import annotations

import asyncio
import os

from loguru import logger
from mcp.server.mcpserver import MCPServer

from src.tools import paper_trading_actions
from src.tools.chip_data import (
    get_institutional_streak,
    get_institutional_trading,
    get_margin_trading,
)
from src.tools.company_insight import get_company_insights
from src.tools.company_moat import get_company_moat_evidence
from src.tools.discord_tools import send_channel_message
from src.tools.discord_tools import send_dm as _discord_send_dm
from src.tools.gmail_tools import create_draft as _gmail_create_draft
from src.tools.gmail_tools import send_email as _gmail_send_email
from src.tools.mops_data import (
    get_financial_summary,
    get_material_info,
    get_recent_material_info,
)
from src.tools.sector_data import get_sector_symbols
from src.tools.stock_data import get_fundamental_data, get_stock_price, get_technical_indicators
from src.tools.theme_search import search_theme_stocks
from src.tools.web_search import search_web

mcp = MCPServer("market-agent-tools")

_allowed_raw = os.getenv("MARKET_AGENT_MCP_ALLOWED_TOOLS", "")
_ALLOWED_TOOLS = {name for name in _allowed_raw.split(",") if name}

_REQUIRED_BUY_CHECKS = {
    "technical_analysis",
    "fundamental_analysis",
    "chip_analysis",
    "company_announcements",
}
_completed_research_checks: dict[str, set[str]] = {}


def _normalized_symbol(symbol: str) -> str:
    normalized = symbol.strip().upper()
    return f"{normalized}.TW" if normalized.isdigit() else normalized


def _mark_research_check(symbol: str, check: str) -> None:
    normalized = _normalized_symbol(symbol)
    _completed_research_checks.setdefault(normalized, set()).add(check)


def _tool(name: str):
    """Register a tool unless this subprocess was started with a whitelist."""
    def decorator(func):
        if not _ALLOWED_TOOLS or name in _ALLOWED_TOOLS:
            return mcp.tool()(func)
        return func

    return decorator


def _fire_and_forget(coro) -> None:
    """asyncio.ensure_future without an awaiter drops exceptions as silent
    'unretrieved task exception' log noise. Wrap so a failed background
    upsert (e.g. this subprocess racing discord_bot.py's startup
    init_storage() on a fresh checkout) is at least logged, not lost."""
    async def _run():
        try:
            await coro
        except Exception as exc:
            logger.warning(f"mcp_server: background task failed: {exc}")

    asyncio.ensure_future(_run())


# ── 資料查詢 ──────────────────────────────────────────────────────────────

@_tool("sector_lookup")
async def sector_lookup(keyword: str) -> str:
    """查詢 TWSE 官方產業類股的成份股。適用於半導體、航運、金融、鋼鐵等官方產業關鍵字。"""
    result = await get_sector_symbols(keyword, max_symbols=8)
    symbols = result.get("symbols", [])
    names = result.get("sector_names", [])
    if not symbols:
        return f"找不到「{keyword}」相關的官方產業類股"
    return f"產業：{', '.join(names)} | 代表股：{', '.join(symbols)}"


@_tool("theme_lookup")
async def theme_lookup(keyword: str) -> str:
    """查詢市場主題/概念股。適用於機器人、元宇宙、低軌衛星、AI、電動車等題材關鍵字。"""
    result = await search_theme_stocks(keyword, max_symbols=8)
    symbols = result.get("symbols", [])
    matched = result.get("matched_concept", keyword)
    if not symbols:
        return f"找不到「{keyword}」相關概念股"
    return f"概念：{matched} | 個股：{', '.join(symbols)}"


@_tool("technical_analysis")
async def technical_analysis(symbol: str) -> str:
    """查詢個股技術面指標：現價、RSI、MACD、均線(5/10/20/60日)、KD、量比、乖離率、布林帶。
    symbol 格式：2330.TW"""
    ind, price = await asyncio.gather(
        get_technical_indicators(symbol),
        get_stock_price(symbol),
        return_exceptions=True,
    )
    if isinstance(ind, Exception) or (isinstance(ind, dict) and ind.get("error")):
        return f"{symbol} 技術面資料取得失敗：{ind}"
    _mark_research_check(symbol, "technical_analysis")
    price_ok = isinstance(price, dict) and not price.get("error")
    if price_ok:
        from src.memory.stock_store import upsert_daily_price
        _fire_and_forget(upsert_daily_price([{
            "symbol": symbol,
            "price": price,
            "indicators": ind,
        }]))
    return (
        f"{symbol} | 現價: {ind.get('close')} | RSI: {ind.get('rsi_14')} | "
        f"MACD: {ind.get('macd')} | "
        f"MA5: {ind.get('sma_5')} | MA10: {ind.get('sma_10')} | "
        f"MA20: {ind.get('sma_20')} | MA60: {ind.get('sma_60')} | "
        f"KD(K/D): {ind.get('kd_k')}/{ind.get('kd_d')} | 量比(5日): {ind.get('volume_ratio')} | "
        f"乖離率(20): {ind.get('bias_20')}% | 乖離率(60): {ind.get('bias_60')}% | "
        f"布林上軌: {ind.get('bb_upper')} | 下軌: {ind.get('bb_lower')}"
    )


@_tool("fundamental_analysis")
async def fundamental_analysis(symbol: str) -> str:
    """查詢個股基本面：本益比、股價淨值比、EPS、ROE、營收成長、分析師評等。symbol 格式：2330.TW"""
    data = await get_fundamental_data(symbol)
    if data.get("error"):
        return f"{symbol} 基本面資料取得失敗：{data['error']}"
    _mark_research_check(symbol, "fundamental_analysis")
    from src.memory.stock_store import upsert_daily_fundamental
    _fire_and_forget(upsert_daily_fundamental([data]))
    return (
        f"{symbol} {data.get('company_name', '')} | "
        f"PE: {data.get('pe_ratio')} | PB: {data.get('pb_ratio')} | "
        f"EPS: {data.get('eps_ttm')} | ROE: {data.get('roe')} | "
        f"營收成長: {data.get('revenue_growth')} | 毛利率: {data.get('gross_margin')} | "
        f"目標價: {data.get('analyst_target')} | 評等: {data.get('analyst_recommendation')}"
    )


@_tool("company_news")
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


@_tool("chip_analysis")
async def chip_analysis(symbol: str) -> str:
    """查詢個股即時籌碼面：三大法人買賣超（外資/投信/自營商）、投信/外資連續買超天數、
    融資融券餘額。symbol 格式：2330.TW"""
    inst, margin, streak = await asyncio.gather(
        get_institutional_trading(symbol),
        get_margin_trading(symbol),
        get_institutional_streak(symbol),
        return_exceptions=True,
    )
    inst_ok = not isinstance(inst, Exception) and not (isinstance(inst, dict) and inst.get("error"))
    margin_ok = not isinstance(margin, Exception) and not (isinstance(margin, dict) and margin.get("error"))

    if inst_ok and margin_ok:
        from src.memory.stock_store import upsert_daily_chip
        _fire_and_forget(upsert_daily_chip([{
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
    streak_ok = not isinstance(streak, Exception) and not (
        isinstance(streak, dict) and streak.get("error")
    )
    if streak_ok:
        parts.append(
            f"  投信連續買超:{streak.get('trust_streak_days')}天 "
            f"外資連續買超:{streak.get('foreign_streak_days')}天"
        )
    if inst_ok and margin_ok:
        _mark_research_check(symbol, "chip_analysis")
    return "\n".join(parts)


@_tool("stock_history")
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


# ── 開放搜尋／官方揭露 ────────────────────────────────────────────────────

@_tool("web_search")
async def web_search(query: str, max_results: int = 5) -> str:
    """開放網頁搜尋（DuckDuckGo），自己下關鍵字查詢固定 API 沒有涵蓋的資訊（新聞事件、市場氛圍等）。
    嚴禁把搜尋結果當成股價/財報/籌碼等數字的來源——這類數字一律要用 technical_analysis、
    fundamental_analysis、chip_analysis、company_financial_summary 查證。"""
    results = await search_web(query, max_results=max_results)
    if not results:
        return f"「{query}」沒有找到搜尋結果"
    lines = [f"搜尋「{query}」結果（僅供參考背景，非結構化資料，數字仍須用其他工具查證）："]
    for r in results:
        lines.append(f"- {r['title']}\n  {r['snippet']}\n  來源: {r['url']}")
    return "\n".join(lines)


@_tool("company_announcements")
async def company_announcements(symbol: str) -> str:
    """查詢公司「今天」的重大訊息公告（TWSE 官方 MOPS 資料）。只有今天，沒有歷史。symbol 格式：2330.TW"""
    result = await get_material_info(symbol)
    if result.get("error"):
        return f"{symbol} 重大訊息查詢失敗：{result['error']}"
    _mark_research_check(symbol, "company_announcements")
    items = result.get("items", [])
    if not items:
        return (
            f"{symbol} 今日 MOPS 快照沒有重大訊息；"
            "這不代表最近幾日沒有公告，歷史事件請改查 company_announcements_recent。"
        )
    lines = [f"{symbol} 今日重大訊息（來源：{result['source']}）："]
    for it in items:
        lines.append(f"- [{it['date']} {it['time']}] {it['subject']}")
    return "\n".join(lines)


@_tool("company_moat_analysis")
async def company_moat_analysis(symbol: str, refresh: bool = False) -> str:
    """蒐集公司的技術、量產、供應鏈地位與競爭風險證據。適合長期競爭力問題；純報價或短線技術分析不需呼叫。"""
    result = await get_company_moat_evidence(symbol, refresh=refresh)
    labels = {
        "technology": "技術／產品",
        "commercialization": "量產／商業化",
        "supply_chain": "供應鏈地位",
        "competition_risk": "競爭／替代風險",
    }
    lines = [
        f"{result.get('company_name') or symbol} 公司競爭力證據"
        + ("（快取）" if result.get("cached") else "（新查詢）")
    ]
    for group, label in labels.items():
        lines.append(f"\n【{label}】")
        items = result.get("evidence", {}).get(group, [])
        if not items:
            lines.append("- 尚未找到足夠線索")
            continue
        for item in items:
            lines.append(f"- {item['title']}｜{item['url']}")
    lines.append("\n注意：以上是待查證證據，不代表技術獨有、供應鏈不可替代，亦不直接代表目前估值值得買進。")
    return "\n".join(lines)


@_tool("company_announcements_recent")
async def company_announcements_recent(symbol: str, days: int = 30) -> str:
    """查詢本系統已保存的近期 MOPS 重大訊息。days 可設 1–365；資料從系統開始每日保存後累積。"""
    result = await get_recent_material_info(symbol, days)
    if result.get("error"):
        return f"{symbol} 近期重大訊息查詢失敗：{result['error']}"
    items = result.get("items", [])
    if not items:
        refresh_note = "今日更新失敗；" if result.get("today_refresh") == "failed" else ""
        return (
            f"{symbol} {refresh_note}本機近 {result['days']} 日公告封存沒有資料。"
            "封存只涵蓋本系統開始收集後的日期，不能據此推論期間內從未公告。"
        )
    lines = [f"{symbol} 本機近 {result['days']} 日 MOPS 重大訊息封存："]
    for item in items:
        lines.append(f"- {item['date']} {item['time']}｜{item['subject']}")
    lines.append(f"來源：{result['source']}")
    return "\n".join(lines)


@_tool("company_financial_summary")
async def company_financial_summary(symbol: str) -> str:
    """查詢公司「最新一期」公開財報摘要（TWSE 官方 MOPS 綜合損益表）。只有最新一季，沒有歷史。symbol 格式：2330.TW"""
    result = await get_financial_summary(symbol)
    if result.get("error"):
        return f"{symbol} 財報查詢失敗：{result['error']}"
    fields = result["fields"]
    skip = {"公司代號", "公司名稱", "出表日期"}
    lines = [f"{symbol} {fields.get('公司名稱', '')} 最新一期公開財報（來源：{result['source']}）："]
    for k, v in fields.items():
        if k in skip:
            continue
        lines.append(f"  {k}: {v}")
    return "\n".join(lines)


# ── 訊息發送 ──────────────────────────────────────────────────────────────

@_tool("discord_message")
async def discord_message(channel_id: str, message: str, mention_user_ids: str = "") -> str:
    """傳訊息到 Discord 頻道。channel_id 為頻道 ID（數字）。mention_user_ids 用逗號分隔多個 Discord user ID，留空則不 @。"""
    mentions = [uid.strip() for uid in mention_user_ids.split(",") if uid.strip()]
    result = await send_channel_message(channel_id, message, mention_user_ids=mentions or None)
    if result.get("error"):
        return f"Discord 傳送失敗：{result['error']}"
    return f"訊息已傳送至頻道 {channel_id}（message_id: {result.get('message_id')}）"


@_tool("discord_dm")
async def discord_dm(user_id: str, message: str) -> str:
    """私訊 Discord 用戶。user_id 填對方的 Discord user ID；若要私訊主人（bot 擁有者），填 'owner'。"""
    result = await _discord_send_dm(user_id, message)
    if result.get("error"):
        return f"Discord DM 失敗：{result['error']}"
    return f"私訊已送出（user_id: {result.get('user_id')}，message_id: {result.get('message_id')}）"


@_tool("gmail_draft")
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


@_tool("gmail_send")
async def gmail_send(to: str, subject: str, body: str) -> str:
    """直接寄送 Email。通常在用戶確認草稿後才呼叫。
    to: 收件人 email。subject: 主旨。body: 信件內文。"""
    result = await _gmail_send_email(to, subject, body)
    if result.get("error"):
        return f"寄送失敗：{result['error']}"
    return f"Email 已寄出至 {to}（message_id: {result.get('message_id')}）"


# ── 紙上交易（模擬帳戶，非真實下單）───────────────────────────────────────
# 邏輯本體在 src/tools/paper_trading_actions.py（同其他工具的慣例）；
# 進出場價格一律由那裡即時查真實股價，不接受 LLM 自行指定價格數字。

@_tool("paper_trade_status")
async def paper_trade_status() -> str:
    """查詢目前紙上交易的持倉狀況：有哪些部位持有中（含浮動損益、短線/長期分類）、
    最近平倉的紀錄、目前還有效的條件單（見 paper_trade_set_condition），
    以及模擬帳戶目前的可用現金（下單前用這個確認額度夠不夠）。"""
    result = await paper_trading_actions.get_status()
    eq = result["equity"]
    equity_line = (
        f"[模擬帳戶] 可用現金 {eq['current_cash']:.0f} / 起始本金 {eq['starting_capital']:.0f}，"
        f"目前總資產 {eq['current_equity']:.0f}（累計報酬 {eq['total_return_pct']}%）"
        if eq['current_equity'] is not None else
        f"估值不完整（缺報價：{', '.join(eq['missing_quotes'])}）；總報酬暫不計算"
    )
    lines = []
    if not result["positions"]:
        lines.append("目前沒有任何紙上交易部位")
    for p in result["positions"]:
        horizon_label = "長期持有" if p.get("horizon") == "long_term" else "短線操作"
        if p["status"] == "open":
            lines.append(
                f"{p['symbol']}：持有中（{horizon_label}），{p['shares']} 股，"
                f"進場 {p['entry_price']}（{p['entry_date']}），浮動損益 {p['pnl_pct']}%"
            )
        else:
            lines.append(
                f"{p['symbol']}：已平倉（{horizon_label}），{p['shares']} 股，"
                f"進場 {p['entry_price']} → 出場 {p['exit_price']}"
                f"（{p['exit_reason']}），實現損益 {p['pnl_pct']}%"
            )
    lines.append(equity_line)
    if result["conditions"]:
        lines.append("[有效條件單]")
        for c in result["conditions"]:
            lines.append(
                f"id={c['id']}：{c['symbol']} {c['indicator']} {c['operator']} "
                f"{c['threshold']} → {c['action']}"
            )
    return "\n".join(lines)


@_tool("paper_trade_buy")
async def paper_trade_buy(
    symbol: str, reason: str, horizon: str = "short_term", allocation_pct: float = 10.0
) -> str:
    """對指定股票開一筆紙上交易買進部位（模擬，非真實下單）。
    symbol 格式：2330.TW。reason：買進理由，會被記錄下來。
    horizon 只能是 short_term（短線，會持續被緊盯、需要你自己判斷出場時機）或
    long_term（長期持有，之後只會用較低頻率的資訊追蹤，不會每次緊盯都問你）。
    allocation_pct：這筆要用模擬本金的百分之多少去買，依你的信心程度自行判斷；
    系統會自動夾在允許的上下限之間，超出範圍不會整筆失敗。現金不足時交易會被拒絕，
    可先呼叫 paper_trade_status 查可用現金。
    價格一律用系統即時查到的真實股價，不接受自行指定價格。
    同一支股票若已有持有中部位，不可重複買進，請先用 paper_trade_status 確認。
    買進後會自動從觀察名單移除，不需要另外呼叫 watchlist_drop。"""
    normalized = _normalized_symbol(symbol)
    completed = _completed_research_checks.get(normalized, set())
    missing = sorted(_REQUIRED_BUY_CHECKS - completed)
    if missing:
        return (
            f"拒絕買入 {normalized}：本輪尚未成功完成必要研究："
            f"{', '.join(missing)}。請先完成後再呼叫 paper_trade_buy。"
        )

    result = await paper_trading_actions.buy(normalized, reason, horizon, allocation_pct)
    if result.get("error"):
        return result["error"]
    _completed_research_checks.pop(normalized, None)
    return (
        f"已買進 {result['symbol']} @ {result['price']} x {result['shares']} 股"
        f"（約 {result['allocation_amount']:.0f} 元，id={result['position_id']}）"
    )


@_tool("paper_trade_sell")
async def paper_trade_sell(symbol: str, reason: str, exit_reason: str = "llm_signal") -> str:
    """對指定股票持有中的部位平倉（模擬，非真實下單）。
    exit_reason 只能是 take_profit（停利）、stop_loss（停損）、llm_signal（其他判斷）三選一。
    價格一律用系統即時查到的真實股價，不接受自行指定價格。"""
    result = await paper_trading_actions.sell(symbol, reason, exit_reason)
    if result.get("error"):
        return result["error"]
    return (
        f"已賣出 {result['symbol']} @ {result['price']}"
        f"（{result['exit_reason']}，損益 {result['pnl_pct']}%）"
    )


@_tool("watchlist_drop")
async def watchlist_drop(symbol: str, reason: str) -> str:
    """把指定股票從觀察名單移除，代表你判斷這支不用再繼續追蹤了
    （不論是決定不交易它，還是已經透過 paper_trade_buy 處理完畢）。
    這是移除觀察名單的主要方式；系統另外有一個很長的機械式過期時間作為保險，
    但正常情況下應該由你主動呼叫這個工具來管理名單，而不是等它自動過期。"""
    result = await paper_trading_actions.drop_watchlist(symbol, reason)
    if result.get("error"):
        return result["error"]
    return f"已將 {result['symbol']} 從觀察名單移除"


@_tool("paper_trade_set_condition")
async def paper_trade_set_condition(
    symbol: str,
    indicator: str,
    operator: str,
    threshold: float,
    action: str,
    reason: str = "",
    horizon: str = "short_term",
    allocation_pct: float = 10.0,
    exit_reason: str = "llm_signal",
) -> str:
    """設定一筆條件單：系統會每分鐘自動用真實數據檢查這個條件，一旦成立就直接
    執行 paper_trade_buy 或 paper_trade_sell，不會再另外問你一次。適合你已經分析
    過一支股票、只是在等特定價位或指標出現的情況，不用每輪緊盯都重新問一次。

    indicator 只能是：close（即時股價）、sma_20、sma_60、rsi_14、macd、
    macd_signal、macd_hist、bb_upper、bb_lower、ema_12、bias_20、bias_60
    （跟 technical_analysis 回傳的欄位一致）。
    operator 只能是：lt（小於）、gt（大於）、lte（小於等於）、gte（大於等於）。
    action 只能是 buy 或 sell。action=buy 時 horizon/allocation_pct 才有意義
    （用法同 paper_trade_buy）；action=sell 時 exit_reason 才有意義
    （用法同 paper_trade_sell）。
    條件成立後只會觸發一次，不會重複觸發；若之後想取消，用
    paper_trade_cancel_condition。"""
    result = await paper_trading_actions.set_condition(
        symbol, indicator, operator, threshold, action, reason, horizon,
        allocation_pct, exit_reason,
    )
    if result.get("error"):
        return result["error"]
    return (
        f"已設定條件單 id={result['condition_id']}："
        f"{symbol} {indicator} {operator} {threshold} → {action}"
    )


@_tool("paper_trade_cancel_condition")
async def paper_trade_cancel_condition(condition_id: int) -> str:
    """取消一筆還沒觸發的條件單（用 paper_trade_status 查詢目前有效的條件單 id）。"""
    result = await paper_trading_actions.cancel_watch_condition(condition_id)
    if result.get("error"):
        return result["error"]
    return f"已取消條件單 id={result['condition_id']}"


def _init_storage_before_serving() -> None:
    """This subprocess is spawned fresh per react call and races
    discord_bot.py's own startup init_storage() — without this, a background
    upsert here could hit a SQLite file with no tables yet on a first run."""
    from src.memory.store import init_storage

    asyncio.run(init_storage())


if __name__ == "__main__":
    _init_storage_before_serving()
    mcp.run(transport="stdio")
