"""Redis-backed conversation session store.

Uses a Redis List per channel for atomic append operations.
Each element is a JSON-encoded message dict. This avoids the
read-modify-write race condition of the previous blob approach.

Key: session:<channel_id>  (List, shared across all users in the channel)
Messages include a [username] prefix so the LLM can distinguish speakers
and decide whether a follow-up refers to a previous user's topic.
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


def _session_key(channel_id: str) -> str:
    return f"session:{channel_id}"


async def get_session_messages(channel_id: str, user_id: str = "") -> list[dict[str, Any]]:
    r = get_redis()
    raw_items = await r.lrange(_session_key(channel_id), 0, -1)
    return [json.loads(item) for item in raw_items]


async def append_message(
    channel_id: str,
    user_id: str,
    role: str,
    content: str,
    meta: dict | None = None,
    max_messages: int = 20,
    username: str = "",
) -> None:
    r = get_redis()
    key = _session_key(channel_id)
    # Prefix user messages with [username] so LLM can distinguish speakers
    stored_content = f"[{username}]: {content}" if role == "user" and username else content
    entry = json.dumps({"role": role, "content": stored_content, "meta": meta or {}}, ensure_ascii=False)

    async with r.pipeline(transaction=True) as pipe:
        pipe.rpush(key, entry)
        pipe.ltrim(key, -max_messages, -1)
        pipe.expire(key, settings.session_ttl_seconds)
        await pipe.execute()


async def clear_session(channel_id: str, user_id: str = "") -> None:
    r = get_redis()
    await r.delete(_session_key(channel_id))
