"""News Agent — fetches and filters news relevant to the current query."""

from dataclasses import asdict
from loguru import logger

from src.agents.state import AgentState
from src.tools.news_fetcher import fetch_all_news
from src.config import settings


async def news_agent_node(state: AgentState) -> dict:
    """LangGraph node: fetch recent news and filter by relevance."""
    logger.info("NewsAgent: fetching news")
    articles = await fetch_all_news(settings.news_lookback_hours)

    # Filter by target symbols if specified
    symbols = state.target_symbols
    if symbols:
        codes = [s.replace(".TW", "") for s in symbols]
        filtered = [
            a for a in articles
            if any(code in (a.title + a.content) for code in codes)
        ]
        # Fall back to all articles if nothing matches
        if not filtered:
            filtered = articles
    else:
        filtered = articles

    # Cap and convert to dicts
    limited = filtered[: settings.max_news_per_run]
    news_dicts = [asdict(a) for a in limited]

    # Collect source URLs
    sources = list({a.source_url for a in limited if a.source_url})

    logger.info(f"NewsAgent: returning {len(news_dicts)} articles")
    return {"news_articles": news_dicts, "sources": sources}
