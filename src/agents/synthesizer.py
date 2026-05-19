"""Report Synthesizer Agent — integrates all data into a cited investment report.

This is the only agent that calls the LLM with actual data.
Every claim in the output must reference a source from the collected data.
"""

import json
from loguru import logger

from src.agents.state import AgentState
from src.llm import llm_chat
from src.memory.news_cache import load_news_cache

SYNTHESIS_SYSTEM = """你是一個專業的財經分析師 AI，負責整合多方數據撰寫投資分析報告。

規則：
1. 每一個分析判斷必須引用提供的數據來源，不可憑空生成數字
2. 明確標注數據來源（例如：[來源: TWSE] 或 [來源: Yahoo Finance]）
3. 技術面、基本面、籌碼面分開呈現
4. 提供明確的風險提示
5. 語氣專業但易懂，使用繁體中文
6. 如果某項數據缺失，直接說明「資料暫時無法取得」，不要猜測
"""

SYNTHESIS_PROMPT = """以下是從各數據源收集到的最新資訊，請根據這些數據生成投資分析報告。

=== 使用者問題 ===
{user_message}

=== 最新新聞（{news_count} 則）===
{news_summary}

=== 技術面數據 ===
{technical_summary}

=== 基本面數據 ===
{fundamental_summary}

=== 籌碼面數據 ===
{chip_summary}

=== 社群訊號 ===
{social_summary}

=== RAG 知識庫補充 ===
{rag_context}

=== 查詢類型說明 ===
{query_context}

請生成完整的分析報告，格式如下：
1. **市場摘要** - 今日重要事件
2. **個股/類股分析**（如有指定股票）
   - 技術面分析（含具體數據）
   - 基本面分析（含具體數據）
   - 籌碼面分析（含具體數據）
3. **社群輿情**
4. **投資建議** - 需標注風險等級
5. **數據來源列表**
"""


def _summarize_news(news: list[dict], max_items: int = 10) -> str:
    if not news:
        return "無最新新聞資料"
    lines = []
    for n in news[:max_items]:
        lines.append(f"- [{n.get('source_name', '')}] {n.get('title', '')} | {n.get('source_url', '')}")
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


def _summarize_social(data: list[dict]) -> str:
    if not data:
        return "無社群訊號"
    lines = []
    for p in data[:8]:
        kws = ", ".join(p.get("keywords", []))
        lines.append(f"- [{p.get('source', 'PTT')}] {p.get('title', '')} | 關鍵詞: {kws} | {p.get('url', '')}")
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

    prompt = SYNTHESIS_PROMPT.format(
        user_message=state.user_message,
        query_context=query_context,
        news_count=len(news_articles),
        news_summary=_summarize_news(news_articles),
        technical_summary=_summarize_technical(state.technical_data),
        fundamental_summary=_summarize_fundamental(state.fundamental_data),
        chip_summary=_summarize_chip(state.chip_data),
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

    # Collect all sources
    all_sources = list(set(state.sources))

    return {"final_report": report, "sources": all_sources}
