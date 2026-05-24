"""Report Synthesizer Agent — integrates all data into a cited investment report.

This is the only agent that calls the LLM with actual data.
Every claim in the output must reference a source from the collected data.
"""

import json
import re
from datetime import datetime, timezone, timedelta
from loguru import logger

from src.agents.state import AgentState
from src.llm import llm_chat
from src.memory.news_cache import load_news_cache

_TW_TZ = timezone(timedelta(hours=8))

def _tw_now() -> datetime:
    return datetime.now(_TW_TZ)

def _is_trading_day(dt: datetime) -> bool:
    """Return True if dt is a Taiwan stock market trading day (Mon–Fri, not checking holidays)."""
    return dt.weekday() < 5  # 0=Mon ... 4=Fri

SYNTHESIS_SYSTEM = """你是一個專業的財經分析師 AI，負責整合多方數據撰寫投資分析報告。

規則：
1. 每一個分析判斷必須引用提供的數據來源，不可憑空生成數字
2. 明確標注數據來源（例如：[來源: TWSE] 或 [來源: Yahoo Finance]）
3. 技術面、基本面、籌碼面分開呈現
4. 提供明確的風險提示
5. 語氣專業但易懂，使用繁體中文
6. 如果某項數據缺失，直接說明「資料暫時無法取得」，不要猜測
7. 【新聞引用原則】報告中提到或引用的每則新聞，必須與分析標的或市場主題直接相關。
   不相關的新聞不得出現在報告內文或數據來源列表中。
   在「數據來源列表」只列出你實際引用過、對分析有幫助的新聞連結，其餘略去。
8. 【重要】法說會與技術亮點段落只能引用「法說會與技術新聞」區塊中實際存在的內容。
   若該區塊為空、或沒有與該股票相關的文章，必須直接寫「暫無法說會或技術公告資料」。
   嚴禁使用自身訓練知識補充任何公司的技術能力、產品或競爭優勢描述。
8. 【時間觀念】報告中已提供今日日期與是否為交易日。
   - 禁止將非今日的新聞或數據描述為「今日」、「今天」；應使用新聞標注的實際日期或「近期」、「本週」等相對描述。
   - 若今日為非交易日（週末/假日），股價數據為最近一個交易日收盤價，必須標注「截至上週五收盤」或「最近交易日」，嚴禁寫成「今日股價」。
   - 新聞若發布於 3 天以前，描述時應用「上週」、「日前」等詞，不得用「今日」。
9. 報告末尾必須包含 CONCLUSION_SUMMARY: ... END_CONCLUSION 區塊，用 3-5 句繁體中文總結：分析了哪些標的、最重要發現、投資建議方向及風險等級。
"""

SYNTHESIS_PROMPT = """以下是從各數據源收集到的最新資訊，請根據這些數據生成投資分析報告。

=== 時間資訊 ===
今日日期：{today}
今日為交易日：{is_trading_day}
（若非交易日，股價數據為最近交易日收盤價，請在報告中標注「截至最近交易日」，勿寫成「今日」）

=== 使用者問題 ===
{user_message}

=== 最新新聞（{news_count} 則，附發布日期）===
{news_summary}

=== 技術面數據 ===
{technical_summary}

=== 基本面數據 ===
{fundamental_summary}

=== 籌碼面數據 ===
{chip_summary}

=== 法說會與技術新聞（獨家技術亮點）===
{insight_summary}

=== 社群訊號 ===
{social_summary}

=== RAG 知識庫補充 ===
{rag_context}

=== 查詢類型說明 ===
{query_context}

請生成完整的分析報告，格式如下：
1. **市場摘要** - 近期重要事件（注意日期標注）
2. **個股/類股分析**（如有指定股票）
   - 技術面分析（含具體數據）
   - 基本面分析（含具體數據）
   - 籌碼面分析（含具體數據）
3. **社群輿情與獨家技術亮點**（整合 PTT 與 CMoney 討論區，提煉投資人關注的核心技術優勢或產品護城河）
4. **投資建議** - 需標注風險等級
5. **數據來源列表**（每筆附上完整 URL，格式：來源名稱 – 標題 – URL）

---
在報告的最末尾，加入以下格式（必須是最後一個區塊）：

CONCLUSION_SUMMARY:
（3-5 句繁體中文：分析了哪些股票或類股、最重要的技術/基本/籌碼面發現、投資建議方向及風險等級）
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
        date_str = f" ({pub[:10]})" if pub else ""
        lines.append(f"- [{n.get('source_name', '')}]{date_str} {n.get('title', '')} | {n.get('source_url', '')}")
    return "\n".join(lines)


def _summarize_technical(data: list[dict]) -> str:
    if not data:
        return "無技術面數據"
    parts = []
    for item in data:
        sym = item.get("symbol", "")
        price = item.get("price", {})
        ind = item.get("indicators", {})
        parts.append(
            f"**{sym}**\n"
            f"  現價: {price.get('last_price')} | 漲跌: {price.get('change_pct')}%\n"
            f"  RSI(14): {ind.get('rsi_14')} | MACD: {ind.get('macd')}\n"
            f"  MA20: {ind.get('sma_20')} | MA60: {ind.get('sma_60')}\n"
            f"  乖離率(20): {ind.get('bias_20')}% | 乖離率(60): {ind.get('bias_60')}%\n"
            f"  布林上軌: {ind.get('bb_upper')} | 下軌: {ind.get('bb_lower')}\n"
            f"  [來源: {price.get('source', ind.get('source', 'Yahoo Finance'))}]"
        )
    return "\n".join(parts)


def _summarize_fundamental(data: list[dict]) -> str:
    if not data:
        return "無基本面數據"
    parts = []
    for item in data:
        parts.append(
            f"**{item.get('symbol')} {item.get('company_name', '')}**\n"
            f"  本益比: {item.get('pe_ratio')} | 股價淨值比: {item.get('pb_ratio')}\n"
            f"  EPS(TTM): {item.get('eps_ttm')} | ROE: {item.get('roe')}\n"
            f"  營收成長: {item.get('revenue_growth')} | 毛利率: {item.get('gross_margin')}\n"
            f"  分析師目標價: {item.get('analyst_target')} | 評等: {item.get('analyst_recommendation')}\n"
            f"  [來源: {item.get('source', 'Yahoo Finance')}]"
        )
    return "\n".join(parts)


def _summarize_chip(data: list[dict]) -> str:
    if not data:
        return "無籌碼面數據"
    parts = []
    for item in data:
        inst = item.get("institutional", {})
        margin = item.get("margin", {})
        parts.append(
            f"**{item.get('symbol')}**\n"
            f"  外資淨買超: {inst.get('foreign_net', 'N/A')} 張\n"
            f"  投信淨買超: {inst.get('trust_net', 'N/A')} 張\n"
            f"  自營商淨買超: {inst.get('dealer_net', 'N/A')} 張\n"
            f"  融資餘額: {margin.get('margin_buy_balance', 'N/A')} | 融券餘額: {margin.get('short_sell_balance', 'N/A')}\n"
            f"  [來源: {inst.get('source', 'TWSE')}]"
        )
    return "\n".join(parts)


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


async def synthesizer_node(state: AgentState) -> dict:
    logger.info("Synthesizer: generating report")

    # theme_query: use theme_articles (news from keyword search) as primary news source
    news_articles = state.news_articles
    if state.theme_articles:
        news_articles = state.theme_articles + news_articles

    # If news_agent was skipped (cache hit), load news from Redis cache now
    if not news_articles and state.news_cached:
        cached = await load_news_cache()
        if cached:
            news_articles = cached
            logger.info(f"Synthesizer: loaded {len(news_articles)} articles from cache (news_agent was skipped)")

    # Build query context description for the prompt
    if state.intent == "theme_query":
        query_context = (
            f"主題查詢：「{state.sector_query}」"
            f"（從 {len(state.theme_articles)} 則相關新聞中找出 {len(state.target_symbols)} 檔個股）"
        )
    elif state.sector_names:
        sector_label = "、".join(state.sector_names)
        query_context = f"類股查詢：{sector_label}（共 {len(state.target_symbols)} 檔代表股）"
    elif state.target_symbols:
        query_context = f"個股查詢：{', '.join(state.target_symbols)}"
    else:
        query_context = "每日市場摘要"

    now = _tw_now()
    prompt = SYNTHESIS_PROMPT.format(
        today=now.strftime("%Y-%m-%d %A"),
        is_trading_day="是" if _is_trading_day(now) else "否（週末或假日，股價為最近交易日收盤價）",
        user_message=state.user_message,
        query_context=query_context,
        news_count=len(news_articles),
        news_summary=_summarize_news(news_articles, symbols=state.target_symbols),
        technical_summary=_summarize_technical(state.technical_data),
        fundamental_summary=_summarize_fundamental(state.fundamental_data),
        chip_summary=_summarize_chip(state.chip_data),
        insight_summary=_summarize_insights(state.insight_data),
        social_summary=_summarize_social(state.social_signals),
        rag_context="\n".join(r.get("content", "") for r in state.rag_context) or "無補充資料",
    )

    try:
        report = await llm_chat(
            messages=[
                {"role": "system", "content": SYNTHESIS_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
        )
    except Exception as exc:
        logger.error(f"Synthesizer LLM failed: {exc}")
        report = f"⚠️ 報告生成失敗：{exc}"

    # Extract conclusion paragraph and clean tags from report
    conclusion = ""
    match = re.search(r"CONCLUSION_SUMMARY:\s*(.*?)\s*END_CONCLUSION", report, re.DOTALL)
    if match:
        conclusion = match.group(1).strip()
        report = re.sub(r"CONCLUSION_SUMMARY:\s*", "", report)
        report = re.sub(r"\s*END_CONCLUSION", "", report)

    # Collect all sources
    all_sources = list(set(state.sources))

    return {"final_report": report, "conclusion": conclusion, "sources": all_sources}
