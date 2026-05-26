"""News Agent — fetches and filters news relevant to the current query."""

from dataclasses import asdict
from loguru import logger

from src.agents.state import AgentState
from src.tools.news_fetcher import fetch_all_news, fetch_targeted_news
from src.memory.news_cache import load_news_cache, save_news_cache
from src.config import settings


async def news_agent_node(state: AgentState) -> dict:
    """LangGraph node: fetch recent news.

    daily_brief / no symbols → broad RSS (cached)
    stock / sector / theme query → targeted fetch per symbols (not cached)
    """
    is_broad = state.intent == "daily_brief" or not state.target_symbols

    if is_broad:
        logger.info("NewsAgent: broad mode — checking cache")
        cached = await load_news_cache()
        if cached:
            articles = cached
            logger.info(f"NewsAgent: serving {len(articles)} articles from cache")
        else:
            logger.info("NewsAgent: cache miss — fetching from sources")
            raw = await fetch_all_news(settings.news_lookback_hours)
            articles = [asdict(a) for a in raw]
            await save_news_cache(articles)
    else:
        logger.info(f"NewsAgent: targeted mode for {len(state.target_symbols)} symbols")
        raw = await fetch_targeted_news(
            state.target_symbols,
            settings.news_lookback_hours,
            extra_keywords=[state.sector_query] if state.sector_query else None,
        )
        articles = [asdict(a) for a in raw]

    limited = articles[: settings.max_news_per_run]
    sources = list({a["source_url"] for a in limited if a.get("source_url")})

    logger.info(f"NewsAgent: returning {len(limited)} articles")
    return {"news_articles": limited, "sources": sources}
