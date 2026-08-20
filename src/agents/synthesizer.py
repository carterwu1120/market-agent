"""Report Synthesizer Agent — integrates all data into a cited investment report.

This is the only agent that calls the LLM with actual data.
Every claim in the output must reference a source from the collected data.
"""

from datetime import datetime, timedelta, timezone

from loguru import logger

from src.agents.report_utils import extract_conclusion
from src.llm_claude_code import claude_code_chat

_TW_TZ = timezone(timedelta(hours=8))

def _tw_now() -> datetime:
    return datetime.now(_TW_TZ)

def _is_trading_day(dt: datetime) -> bool:
    """Return True if dt is a Taiwan stock market trading day (Mon–Fri, not checking holidays)."""
    return dt.weekday() < 5  # 0=Mon ... 4=Fri

SYNTHESIS_SYSTEM = """你是一個專業的財經分析師 AI，負責整合多方數據撰寫投資分析報告。

【核心限制：嚴禁使用訓練知識補充數據】
所有市場數據、股價、指數點位、法人動向、新聞事件，必須 100% 來自本次提供的資料區塊。
若某項資料區塊為空或無相關內容，直接寫「資料暫時無法取得」，絕對不可用訓練記憶中的歷史數據填補。

【技術面數據使用規則】
技術面數據區塊中標注「系統實測數值」的每個數字（收盤價、MA20、MA60、RSI 等）是由程式即時從 Yahoo Finance 抓取的真實數值。
報告中引用這些數字時必須原封不動照抄，絕對不可自行修改、四捨五入至不同位數、或用訓練記憶中的股價替換。
公司名稱也以資料區塊中標注的為準，不可自行更改。

規則：
1. 每一個分析判斷必須引用提供的數據來源，不可憑空生成數字
2. 明確標注數據來源（例如：[來源: TWSE] 或 [來源: Yahoo Finance]）
3. 技術面、基本面、籌碼面分開呈現
4. 提供明確的風險提示
5. 語氣專業但易懂，使用繁體中文
6. 如果某項數據缺失，直接說明「資料暫時無法取得」，不要猜測，不要用訓練資料補充
7. 【新聞日期原則】只能引用「最新新聞」區塊中實際提供的文章。
   每則引用必須標注該新聞的實際發布日期（如：[2026-05-27]）。
   嚴禁引用或提及任何未出現在新聞區塊中的事件、股價走勢、或市場動態。
   若新聞區塊為空，市場摘要必須寫「目前無新聞資料可供分析」，不得自行填充內容。
8. 【RAG 知識庫限制】RAG 區塊只能用於補充投資策略方法論或產業背景知識。
   嚴禁將 RAG 中任何日期、股價、指數數字、公司事件引用為近期市場動態。
9. 【法說會與技術亮點】只能引用「法說會與技術新聞」區塊中實際存在的內容。
   若該區塊為空，必須寫「暫無法說會或技術公告資料」，嚴禁用訓練知識補充公司技術描述。
10. 【時間標注】報告中已提供今日日期。
    - 禁止將非今日日期的新聞描述為「今日」；應使用新聞的實際發布日期。
    - 若今日為非交易日，股價須標注「截至最近交易日收盤」，嚴禁寫成「今日股價」。
11. 【MOPS 官方揭露限制】「今日重大訊息公告」與「最新一期財報」區塊來自 TWSE MOPS 官方 OpenAPI，
    只有「今天」與「最新一季」的資料，沒有歷史。不可用來回答歷史問題，也不可推論成其他日期的公告。
    若區塊為空，直接寫「今日無重大訊息公告」或「本季查無財報資料」，不得自行填充。
12. 報告末尾必須包含 CONCLUSION_SUMMARY: ... END_CONCLUSION 區塊，用 3-5 句繁體中文總結。
"""

SYNTHESIS_PROMPT = """以下是從各數據源收集到的最新資訊。數字表格已由系統程式直接產生（正確無誤），請根據這些資訊撰寫**文字分析**。

【重要】你的任務是寫分析文字，不是重新產生數字。
- 數字表格已附在報告結構中，你不需要在文字裡重複列出所有數字
- 分析文字中若需引用數字，必須直接從下方表格中引用，不可自行生成或修改
- 公司名稱以表格中標注的為準

=== 時間資訊 ===
今日日期：{today}
今日為交易日：{is_trading_day}

=== 使用者問題 ===
{user_message}

=== 查詢類型 ===
{query_context}

=== 最新新聞（{news_count} 則，附發布日期）===
{news_summary}

=== 大盤指數（程式產生，數字已驗證）===
{market_indices_summary}

=== 技術面數據表（程式產生，數字已驗證）===
{technical_summary}

=== 基本面數據表（程式產生，數字已驗證）===
{fundamental_summary}

=== 籌碼面數據表（程式產生，數字已驗證）===
{chip_summary}

=== 今日重大訊息公告（TWSE MOPS 官方，只有今天）===
{announcement_summary}

=== 最新一期財報（TWSE MOPS 官方，只有最新一季）===
{mops_financial_summary}

=== 法說會與技術新聞 ===
{insight_summary}

=== 社群訊號 ===
{social_summary}

=== RAG 知識庫（策略背景，禁止引用為近期市場數據）===
{rag_context}

---
請依以下結構撰寫分析報告：

## 市場摘要
根據新聞與大盤指數數據，描述近期市場重要事件（標注實際日期，不可用「今日」描述非今日新聞）

## 個股分析
針對技術面/基本面/籌碼面表格中的每支個股，撰寫 3-5 句解讀文字：
- 技術面：現價相對均線位置、動能研判、關鍵支撐壓力
- 基本面：估值與獲利能力簡評
- 籌碼面：法人動向解讀
- 官方揭露：今日有無重大訊息公告、最新一期財報重點（若有資料才寫）

## 社群輿情與獨家技術亮點
整合 PTT 與 CMoney 觀點，提煉投資人關注的核心議題

## 投資建議
需標注風險等級（低/中/中高/高），建議操作方向

## 新聞來源
只列出分析中實際引用的新聞，格式：來源名稱 – 標題 – URL

---
CONCLUSION_SUMMARY:
（3-5 句繁體中文總結：分析了哪些標的、最重要發現、投資建議方向及風險等級）
END_CONCLUSION
"""


def _summarize_news(news: list[dict], symbols: list[str] | None = None, max_items: int = 10) -> str:
    if not news:
        return "無最新新聞資料"

    # Filter by symbol relevance for stock queries
    filtered = news

    lines = []
    for n in filtered[:max_items]:
        pub = n.get("published_at") or n.get("publishedAt") or ""
        if hasattr(pub, "strftime"):
            pub = pub.strftime("%Y-%m-%d")
        date_str = f" ({str(pub)[:10]})" if pub else ""
        lines.append(f"- [{n.get('source_name', '')}]{date_str} {n.get('title', '')} | {n.get('source_url', '')}")
    return "\n".join(lines)


def _summarize_technical(data: list[dict]) -> str:
    if not data:
        return "無技術面數據"
    rows = ["| 代號 | 公司名稱 | 收盤價 | 漲跌% | MA20(月線) | MA60(季線) | RSI(14) | MACD | 布林上軌 | 布林下軌 |",
            "|------|----------|--------|-------|------------|------------|---------|------|----------|----------|"]
    for item in data:
        sym = item.get("symbol", "")
        price = item.get("price", {})
        ind = item.get("indicators", {})
        company = price.get("company_name") or ""
        rows.append(
            f"| {sym} | {company} "
            f"| {price.get('last_price', 'N/A')} "
            f"| {price.get('change_pct', 'N/A')} "
            f"| {ind.get('sma_20', 'N/A')} "
            f"| {ind.get('sma_60', 'N/A')} "
            f"| {ind.get('rsi_14', 'N/A')} "
            f"| {ind.get('macd', 'N/A')} "
            f"| {ind.get('bb_upper', 'N/A')} "
            f"| {ind.get('bb_lower', 'N/A')} |"
        )
    return "\n".join(rows)


def _summarize_fundamental(data: list[dict]) -> str:
    if not data:
        return "無基本面數據"
    rows = ["| 代號 | 公司名稱 | 本益比 | 股價淨值比 | EPS(TTM) | ROE | 毛利率 | 營收成長 | 分析師目標價 | 評等 |",
            "|------|----------|--------|------------|----------|-----|--------|----------|--------------|------|"]
    for item in data:
        rows.append(
            f"| {item.get('symbol')} "
            f"| {item.get('company_name', '')} "
            f"| {item.get('pe_ratio', 'N/A')} "
            f"| {item.get('pb_ratio', 'N/A')} "
            f"| {item.get('eps_ttm', 'N/A')} "
            f"| {item.get('roe', 'N/A')} "
            f"| {item.get('gross_margin', 'N/A')} "
            f"| {item.get('revenue_growth', 'N/A')} "
            f"| {item.get('analyst_target', 'N/A')} "
            f"| {item.get('analyst_recommendation', 'N/A')} |"
        )
    return "\n".join(rows)


def _summarize_chip(data: list[dict]) -> str:
    if not data:
        return "無籌碼面數據"
    rows = ["| 代號 | 資料日期 | 外資淨買超(張) | 投信淨買超(張) | 自營商淨買超(張) | 三大法人合計(張) | 融資餘額 | 融券餘額 |",
            "|------|----------|----------------|----------------|------------------|------------------|----------|----------|"]
    for item in data:
        inst = item.get("institutional", {})
        margin = item.get("margin", {})
        rows.append(
            f"| {item.get('symbol')} "
            f"| {inst.get('date', 'N/A')} "
            f"| {inst.get('foreign_net', 'N/A')} "
            f"| {inst.get('trust_net', 'N/A')} "
            f"| {inst.get('dealer_net', 'N/A')} "
            f"| {inst.get('total_3_institutions', 'N/A')} "
            f"| {margin.get('margin_buy_balance', 'N/A')} "
            f"| {margin.get('short_sell_balance', 'N/A')} |"
        )
    return "\n".join(rows)


def _summarize_announcements(data: list[dict]) -> str:
    if not data:
        return "今日無重大訊息公告"
    parts = []
    for item in data:
        sym = item.get("symbol", "")
        items = item.get("items", [])
        if not items:
            continue
        lines = [f"**{sym}**"]
        for it in items:
            lines.append(f"  - [{it.get('date', '')} {it.get('time', '')}] {it.get('subject', '')}")
        parts.append("\n".join(lines))
    return "\n\n".join(parts) if parts else "今日無重大訊息公告"


def _summarize_mops_financials(data: list[dict]) -> str:
    if not data:
        return "本季查無財報資料"
    skip = {"公司代號", "公司名稱", "出表日期"}
    parts = []
    for item in data:
        sym = item.get("symbol", "")
        fields = item.get("fields", {})
        name = fields.get("公司名稱", "")
        lines = [f"**{sym} {name}**"]
        for k, v in fields.items():
            if k in skip or not v:
                continue
            lines.append(f"  {k}: {v}")
        parts.append("\n".join(lines))
    return "\n\n".join(parts) if parts else "本季查無財報資料"


def _summarize_insights(data: list[dict]) -> str:
    if not data:
        return "無法說會/技術新聞資料"
    parts = []
    for item in data:
        sym = item.get("symbol", "")
        name = item.get("company_name", "")
        articles = item.get("articles", [])
        if not articles:
            continue
        lines = [f"**{sym} {name}**"]
        for a in articles:
            lines.append(f"  - {a['title']}")
            if a.get("content"):
                lines.append(f"    {a['content'][:150]}")
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


def _summarize_indices(data: dict) -> str:
    if not data:
        return "大盤指數資料暫時無法取得"
    lines = []
    for key, idx in data.items():
        if idx.get("error"):
            name = idx.get("name", key)
            lines.append(f"  {name}: 資料暫時無法取得")
            continue
        change = idx.get("change_pct", 0)
        arrow = "▲" if change >= 0 else "▼"
        lines.append(
            f"  {idx['name']}: {idx['close']:,} {arrow}{abs(change)}%"
            f"  [{idx.get('date', '')}]"
        )
    return "\n".join(lines) if lines else "大盤指數資料暫時無法取得"


def _summarize_social(data: list[dict]) -> str:
    if not data:
        return "無社群訊號"
    ptt = [p for p in data if p.get("source") != "CMoney討論區"]
    cmoney = [p for p in data if p.get("source") == "CMoney討論區"]
    lines = []
    if ptt:
        lines.append("【PTT Stock 板】")
        for p in ptt[:5]:
            kws = ", ".join(p.get("keywords", []))
            lines.append(f"- {p.get('title', '')} | 關鍵詞: {kws} | {p.get('url', '')}")
    if cmoney:
        lines.append("【CMoney 討論區 — 投資人觀點/獨家技術】")
        for p in cmoney[:8]:
            content = p.get("content", "")
            lines.append(f"- [{','.join(p.get('tickers', []))}] {p.get('title', '')}" +
                         (f"\n  摘要: {content}" if content else ""))
    return "\n".join(lines)


async def write_report(
    user_message: str,
    target_symbols: list[str],
    news_articles: list[dict],
    market_indices: dict,
    technical_data: list[dict],
    fundamental_data: list[dict],
    chip_data: list[dict],
    insight_data: list[dict],
    social_signals: list[dict],
    announcement_data: list[dict],
    mops_financial_data: list[dict],
    rag_context: str,
    sources: list[str],
) -> dict:
    logger.info("Synthesizer: generating report")

    query_context = f"個股查詢：{', '.join(target_symbols)}" if target_symbols else "每日市場摘要"

    now = _tw_now()
    def _safe(text: str) -> str:
        return text.replace("{", "{{").replace("}", "}}")

    rag_text = rag_context or "無補充資料"

    # Pre-render data tables (these will be inserted directly, bypassing LLM)
    tech_table = _summarize_technical(technical_data)
    fund_table = _summarize_fundamental(fundamental_data)
    chip_table = _summarize_chip(chip_data)
    indices_table = _summarize_indices(market_indices)

    try:
        prompt = SYNTHESIS_PROMPT.format(
            today=now.strftime("%Y-%m-%d %A"),
            is_trading_day="是" if _is_trading_day(now) else "否（週末或假日，股價為最近交易日收盤價）",
            user_message=_safe(user_message),
            query_context=query_context,
            news_count=len(news_articles),
            news_summary=_safe(_summarize_news(news_articles, symbols=target_symbols)),
            technical_summary=tech_table,
            fundamental_summary=fund_table,
            chip_summary=chip_table,
            announcement_summary=_safe(_summarize_announcements(announcement_data)),
            mops_financial_summary=_safe(_summarize_mops_financials(mops_financial_data)),
            insight_summary=_safe(_summarize_insights(insight_data)),
            market_indices_summary=indices_table,
            social_summary=_safe(_summarize_social(social_signals)),
            rag_context=_safe(rag_text),
        )
        llm_analysis = await claude_code_chat(
            messages=[{"role": "user", "content": prompt}],
            system=SYNTHESIS_SYSTEM,
        )
    except Exception as exc:
        logger.error(f"Synthesizer LLM failed: {exc}")
        llm_analysis = f"⚠️ 報告生成失敗：{exc}"

    report, conclusion = extract_conclusion(llm_analysis)

    # Persist daily snapshots to SQLite (best-effort, non-blocking)
    import asyncio as _asyncio

    from src.memory.stock_store import (
        upsert_daily_chip,
        upsert_daily_fundamental,
        upsert_daily_price,
    )
    _asyncio.ensure_future(upsert_daily_price(technical_data))
    _asyncio.ensure_future(upsert_daily_chip(chip_data))
    _asyncio.ensure_future(upsert_daily_fundamental(fundamental_data))

    return {
        "final_report": report,
        "conclusion": conclusion,
        "sources": list(set(sources)),
        "target_symbols": target_symbols,
    }
