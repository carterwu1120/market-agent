"""Redis-backed news cache.

Stores the latest fetched news articles with a TTL so the orchestrator
can decide whether to skip news_agent entirely on follow-up queries.

Key: news:cache  (Redis string, JSON)
TTL: settings.news_cache_ttl_seconds (default 30 min)
"""

import json
from datetime import datetime, timezone
from typing import Any

from loguru import logger

from src.memory.session_store import get_redis
from src.config import settings

_CACHE_KEY = "news:cache"


async def save_news_cache(articles: list[dict[str, Any]]) -> None:
    """Store fetched articles in Redis with TTL."""
    try:
        r = get_redis()
        payload = json.dumps(
            {"fetched_at": datetime.now(timezone.utc).isoformat(), "articles": articles},
            ensure_ascii=False,
            default=str,
        )
        await r.set(_CACHE_KEY, payload, ex=settings.news_cache_ttl_seconds)
        logger.info(f"NewsCache: saved {len(articles)} articles (TTL={settings.news_cache_ttl_seconds}s)")
    except Exception as exc:
        logger.warning(f"NewsCache: save failed (non-critical): {exc}")


async def load_news_cache() -> list[dict[str, Any]] | None:
    """Return cached articles if cache is still valid, else None."""
    try:
        r = get_redis()
        raw = await r.get(_CACHE_KEY)
        if not raw:
            return None
        data = json.loads(raw)
        articles = data.get("articles", [])
        fetched_at = data.get("fetched_at", "")
        logger.info(f"NewsCache: hit — {len(articles)} articles (fetched_at={fetched_at})")
        return articles
    except Exception as exc:
        logger.warning(f"NewsCache: load failed (non-critical): {exc}")
        return None


async def has_fresh_news() -> bool:
    """True if Redis has a valid (non-expired) news cache."""
    try:
        r = get_redis()
        return bool(await r.exists(_CACHE_KEY))
    except Exception as exc:
        logger.warning(f"NewsCache: exists check failed: {exc}")
        return False
