"""Deterministic daily market brief.

Fetches every data source directly in plain async code (no LLM decides what to
fetch), then makes one claude_code_chat() call to write the report. Runs
unattended on a schedule (08:30/12:00/14:30) plus /brief, so it must never
silently skip a source the way an LLM-driven tool-calling loop could —
see docs/adr/0001-drop-langgraph-delegate-to-claude-code.md.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict

from loguru import logger

from src.agents.market_agent import _extract_hot_stocks, _normalize_articles
from src.agents.synthesizer import write_report, write_weekend_digest
from src.config import settings
from src.memory.news_cache import load_news_cache, save_news_cache
from src.tools.chip_data import get_institutional_trading, get_margin_trading
from src.tools.cmoney_forum import get_forum_posts
from src.tools.company_insight import get_company_insights
from src.tools.knowledge_base import read_knowledge_base
from src.tools.mops_data import (
    INCOME_STATEMENT_URL,
    MATERIAL_INFO_URL,
    get_financial_summary_batch,
    get_material_info_batch,
)
from src.tools.news_fetcher import fetch_all_news
from src.tools.social_signal import fetch_ptt_stock, filter_signal_posts
from src.tools.stock_data import (
    get_fundamental_data,
    get_market_indices,
    get_stock_price,
    get_technical_indicators,
)


async def _fetch_news() -> list[dict]:
    cached = await load_news_cache()
    if cached:
        articles = _normalize_articles(cached)
        logger.info(f"daily_brief: serving {len(articles)} articles from cache")
        return articles[: settings.max_news_per_run]

    logger.info("daily_brief: cache miss — fetching from sources")
    raw = await fetch_all_news(settings.news_lookback_hours)
    articles = _normalize_articles([asdict(a) for a in raw])[: settings.max_news_per_run]
    if articles:
        await save_news_cache(articles)
    else:
        logger.warning("daily_brief: news fetch returned nothing")
    return articles


async def _technical(symbols: list[str]) -> tuple[list[dict], list[dict], list[str]]:
    if not symbols:
        return [], [], []

    async def _analyze(sym: str) -> dict:
        price, indicators, insights = await asyncio.gather(
            get_stock_price(sym),
            get_technical_indicators(sym),
            get_company_insights(sym, max_articles=6),
        )
        return {"symbol": sym, "price": price, "indicators": indicators, "insights": insights}

    results = await asyncio.gather(*[_analyze(s) for s in symbols], return_exceptions=True)
    data, insight_data, sources = [], [], []
    for r in results:
        if isinstance(r, Exception):
            logger.warning(f"daily_brief technical error: {r}")
            continue
        data.append({"symbol": r["symbol"], "price": r["price"], "indicators": r["indicators"]})
        if r.get("insights", {}).get("articles"):
            insight_data.append(r["insights"])
        if r.get("price", {}).get("source"):
            sources.append(r["price"]["source"])
    return data, insight_data, sources


async def _fundamental(symbols: list[str]) -> tuple[list[dict], list[str]]:
    if not symbols:
        return [], []

    results = await asyncio.gather(*[get_fundamental_data(s) for s in symbols], return_exceptions=True)
    data, sources = [], []
    for r in results:
        if isinstance(r, Exception):
            logger.warning(f"daily_brief fundamental error: {r}")
            continue
        data.append(r)
        if r.get("source"):
            sources.append(r["source"])
    return data, sources


async def _chip(symbols: list[str]) -> tuple[list[dict], list[str]]:
    if not symbols:
        return [], []

    async def _fetch(sym: str) -> dict:
        institutional, margin = await asyncio.gather(
            get_institutional_trading(sym),
            get_margin_trading(sym),
        )
        return {"symbol": sym, "institutional": institutional, "margin": margin}

    results = await asyncio.gather(*[_fetch(s) for s in symbols], return_exceptions=True)
    data, sources = [], []
    for r in results:
        if isinstance(r, Exception):
            logger.warning(f"daily_brief chip error: {r}")
            continue
        data.append(r)
        if r.get("institutional", {}).get("source"):
            sources.append(r["institutional"]["source"])
    return data, sources


async def _social(symbols: list[str]) -> tuple[list[dict], list[str]]:
    ptt_task = asyncio.create_task(fetch_ptt_stock(max_pages=2))
    cmoney_tasks = (
        [asyncio.create_task(get_forum_posts(sym, max_posts=8)) for sym in symbols[:3]] if symbols else []
    )

    ptt_posts_raw = await ptt_task
    cmoney_results = await asyncio.gather(*cmoney_tasks, return_exceptions=True)

    signal_posts = filter_signal_posts(ptt_posts_raw, min_keywords=1)
    if symbols:
        codes = [s.replace(".TW", "") for s in symbols]
        filtered = [
            p for p in signal_posts
            if any(code in p.title + p.content for code in codes) or any(s in p.tickers for s in symbols)
        ]
        if not filtered:
            filtered = signal_posts[:10]
    else:
        filtered = signal_posts[:15]

    post_dicts = [asdict(p) for p in filtered]
    sources = list({p.url for p in filtered if p.url})

    for result in cmoney_results:
        if isinstance(result, Exception) or not isinstance(result, dict):
            continue
        for post in result.get("posts", []):
            post_dicts.append({
                "source": "CMoney討論區",
                "title": post["title"],
                "content": post.get("content", ""),
                "url": post["url"],
                "keywords": [],
                "tickers": [result["symbol"]],
            })
        if result.get("forum_url"):
            sources.append(result["forum_url"])

    return post_dicts, sources


async def _mops(symbols: list[str]) -> tuple[list[dict], list[dict], list[str]]:
    """Today's official MOPS material-info announcements + latest-quarter financials."""
    if not symbols:
        return [], [], []

    announcements, financials = await asyncio.gather(
        get_material_info_batch(symbols),
        get_financial_summary_batch(symbols),
        return_exceptions=True,
    )
    if isinstance(announcements, Exception):
        logger.warning(f"daily_brief MOPS announcements error: {announcements}")
        announcements = {}
    if isinstance(financials, Exception):
        logger.warning(f"daily_brief MOPS financials error: {financials}")
        financials = {}

    announcement_data = [{"symbol": sym, "items": items} for sym, items in announcements.items() if items]
    financial_data = [{"symbol": sym, "fields": fields} for sym, fields in financials.items()]
    sources = []
    if announcements:
        sources.append(MATERIAL_INFO_URL)
    if financials:
        sources.append(INCOME_STATEMENT_URL)
    return announcement_data, financial_data, sources


async def run_daily_brief(user_message: str) -> dict:
    logger.info("daily_brief: fetching all data sources")

    news_articles = await _fetch_news()
    news_sources = list({a["source_url"] for a in news_articles if a.get("source_url")})

    indices, hot_symbols = await asyncio.gather(
        get_market_indices(),
        _extract_hot_stocks(news_articles),
    )

    (technical_data, insight_data, tech_sources), (fundamental_data, fund_sources), \
        (chip_data, chip_sources), (social_signals, social_sources), \
        (announcement_data, mops_financial_data, mops_sources) = await asyncio.gather(
        _technical(hot_symbols),
        _fundamental(hot_symbols),
        _chip(hot_symbols),
        _social(hot_symbols),
        _mops(hot_symbols),
    )
    rag_context = read_knowledge_base()
    sources = list(set(
        news_sources + tech_sources + fund_sources + chip_sources + social_sources + mops_sources
    ))

    return await write_report(
        user_message=user_message,
        target_symbols=hot_symbols,
        news_articles=news_articles,
        market_indices=indices,
        technical_data=technical_data,
        fundamental_data=fundamental_data,
        chip_data=chip_data,
        insight_data=insight_data,
        social_signals=social_signals,
        announcement_data=announcement_data,
        mops_financial_data=mops_financial_data,
        rag_context=rag_context,
        sources=sources,
    )


async def run_weekend_digest() -> dict:
    """假日消息面摘要：台股休市，技術面/籌碼面/基本面不會有新數據，所以不抓；
    只擴大時間窗抓新聞（涵蓋週五收盤後到現在）+ 週五美股收盤指數（美股收盤時間
    早於台股週一開盤，是有憑有據的最新盤前參考數據，不算「重複舊資料」），
    交給 LLM 判斷哪些事件可能影響週一開盤。
    見 docs/adr/0001-drop-langgraph-delegate-to-claude-code.md 的決定性原則——
    這條路徑一樣是固定抓取，不讓 LLM 決定要不要查新聞。
    """
    logger.info("weekend_digest: fetching news (72h lookback) + latest market indices")
    raw, indices = await asyncio.gather(
        fetch_all_news(lookback_hours=72),
        get_market_indices(),
    )
    articles = _normalize_articles([asdict(a) for a in raw])
    sources = list({a["source_url"] for a in articles if a.get("source_url")})
    for idx in indices.values():
        if idx.get("source"):
            sources.append(idx["source"])

    return await write_weekend_digest(articles, indices, sources)
