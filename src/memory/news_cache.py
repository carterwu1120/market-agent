"""Local SQLite-backed news cache.

Single JSON blob under one key, TTL = settings.news_cache_ttl_seconds.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from loguru import logger

from src.config import settings
from src.memory.cache_store import get_cached, set_cached

_CACHE_KEY = "news:cache"


async def save_news_cache(articles: list[dict[str, Any]]) -> None:
    try:
        await set_cached(
            _CACHE_KEY,
            {"fetched_at": datetime.now(timezone.utc).isoformat(), "articles": articles},
            ttl=settings.news_cache_ttl_seconds,
        )
    except Exception as exc:
        logger.warning(f"NewsCache: save failed (non-critical): {exc}")


async def load_news_cache() -> list[dict[str, Any]] | None:
    try:
        data = await get_cached(_CACHE_KEY)
        return data["articles"] if data else None
    except Exception as exc:
        logger.warning(f"NewsCache: load failed (non-critical): {exc}")
        return None
