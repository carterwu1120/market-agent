"""Redis-backed conversation session store.

Uses a Redis List per session for atomic append operations.
Each element is a JSON-encoded message dict. This avoids the
read-modify-write race condition of the previous blob approach.

Key: session:<channel_id>:<user_id>  (List)
"""

import json
from typing import Any
import redis.asyncio as aioredis

from src.config import settings

_redis: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _redis


def _session_key(channel_id: str, user_id: str) -> str:
    return f"session:{channel_id}:{user_id}"


async def get_session_messages(channel_id: str, user_id: str) -> list[dict[str, Any]]:
    r = get_redis()
    key = _session_key(channel_id, user_id)
    # LRANGE returns all elements in insertion order
    raw_items = await r.lrange(key, 0, -1)
    return [json.loads(item) for item in raw_items]


async def append_message(
    channel_id: str,
    user_id: str,
    role: str,
    content: str,
    meta: dict | None = None,
    max_messages: int = 20,
) -> None:
    r = get_redis()
    key = _session_key(channel_id, user_id)
    entry = json.dumps({"role": role, "content": content, "meta": meta or {}}, ensure_ascii=False)

    # Atomic pipeline: RPUSH + LTRIM + EXPIRE
    async with r.pipeline(transaction=True) as pipe:
        pipe.rpush(key, entry)
        pipe.ltrim(key, -max_messages, -1)      # keep only last N messages
        pipe.expire(key, settings.session_ttl_seconds)
        await pipe.execute()


async def clear_session(channel_id: str, user_id: str) -> None:
    r = get_redis()
    await r.delete(_session_key(channel_id, user_id))
