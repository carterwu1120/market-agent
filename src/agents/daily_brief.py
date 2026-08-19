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

from src.config import settings
from src.memory.news_cache import load_news_cache, save_news_cache
from src.tools.news_fetcher import fetch_all_news
from src.tools.stock_data import (
    get_market_indices,
    get_stock_price,
    get_technical_indicators,
    get_fundamental_data,
)
from src.tools.company_insight import get_company_insights
from src.tools.chip_data import get_institutional_trading, get_margin_trading
from src.tools.social_signal import fetch_ptt_stock, filter_signal_posts
from src.tools.cmoney_forum import get_forum_posts
from src.rag.knowledge_store import search_knowledge
from src.agents.market_agent import _normalize_articles, _extract_hot_stocks
from src.agents.synthesizer import write_report


async def _fetch_news() -> list[dict]:
    cached = await load_news_cache()
    if cached:
        articles = _normalize_articles(cached)
        logger.info(f"daily_brief: serving {len(articles)} articles from cache")
        return articles

    logger.info("daily_brief: cache miss — fetching from sources")
    raw = await fetch_all_news(settings.news_lookback_hours)
    articles = _normalize_articles([asdict(a) for a in raw])
    if articles:
        await save_news_cache(articles)
    else:
        logger.warning("daily_brief: news fetch returned nothing")
    return articles[: settings.max_news_per_run]


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


async def _rag(user_message: str, symbols: list[str]) -> list[dict]:
    query = f"{user_message} {' '.join(symbols)}" if symbols else user_message
    try:
        return await search_knowledge(query, top_k=5)
    except Exception as exc:
        logger.warning(f"daily_brief RAG search failed: {exc}")
        return []


async def run_daily_brief(user_message: str) -> dict:
    logger.info("daily_brief: fetching all data sources")

    news_articles = await _fetch_news()
    news_sources = list({a["source_url"] for a in news_articles if a.get("source_url")})

    indices, hot_symbols = await asyncio.gather(
        get_market_indices(),
        _extract_hot_stocks(news_articles),
    )

    (technical_data, insight_data, tech_sources), (fundamental_data, fund_sources), \
        (chip_data, chip_sources), (social_signals, social_sources), rag_context = await asyncio.gather(
        _technical(hot_symbols),
        _fundamental(hot_symbols),
        _chip(hot_symbols),
        _social(hot_symbols),
        _rag(user_message, hot_symbols),
    )
    sources = list(set(news_sources + tech_sources + fund_sources + chip_sources + social_sources))

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
        rag_context=rag_context,
        sources=sources,
    )
