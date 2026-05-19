"""Redis-backed conversation session store.

Short-term memory: keeps the last N messages per Discord channel in Redis
so the agent can reply with context without hitting PostgreSQL every turn.
Long-term persistence happens asynchronously via PostgreSQL.
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
    raw = await r.get(_session_key(channel_id, user_id))
    if not raw:
        return []
    return json.loads(raw)


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
    messages = await get_session_messages(channel_id, user_id)
    messages.append({"role": role, "content": content, "meta": meta or {}})
    # Keep only the last N messages to bound context size
    messages = messages[-max_messages:]
    await r.setex(key, settings.session_ttl_seconds, json.dumps(messages, ensure_ascii=False))


async def clear_session(channel_id: str, user_id: str) -> None:
    r = get_redis()
    await r.delete(_session_key(channel_id, user_id))
