"""News Agent — fetches and filters news relevant to the current query."""

from dataclasses import asdict
from loguru import logger

from src.agents.state import AgentState
from src.tools.news_fetcher import fetch_all_news
from src.memory.news_cache import load_news_cache, save_news_cache
from src.config import settings


async def news_agent_node(state: AgentState) -> dict:
    """LangGraph node: fetch recent news (Redis cache → scrape fallback)."""
    logger.info("NewsAgent: checking cache")

    # Try cache first (orchestrator already confirmed cache is stale/missing,
    # but if Redis was unavailable during the check, try once more here)
    cached = await load_news_cache()
    if cached:
        articles = cached
        logger.info(f"NewsAgent: serving {len(articles)} articles from cache")
    else:
        logger.info("NewsAgent: cache miss — fetching from sources")
        raw = await fetch_all_news(settings.news_lookback_hours)
        articles = [asdict(a) for a in raw]
        await save_news_cache(articles)

    # Filter by target symbols if specified
    symbols = state.target_symbols
    if symbols:
        codes = [s.replace(".TW", "") for s in symbols]
        filtered = [
            a for a in articles
            if any(code in (a.get("title", "") + a.get("content", "")) for code in codes)
        ]
        if not filtered:
            filtered = articles
    else:
        filtered = articles

    limited = filtered[: settings.max_news_per_run]
    sources = list({a["source_url"] for a in limited if a.get("source_url")})

    logger.info(f"NewsAgent: returning {len(limited)} articles")
    return {"news_articles": limited, "sources": sources}
