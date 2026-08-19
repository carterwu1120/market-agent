"""Generic TTL key-value cache backed by the local SQLite `kv_cache` table.

Replaces the ad-hoc Redis GET/SET-with-EX usage that used to be scattered
across news_cache.py and src/tools/stock_data.py.
"""

from __future__ import annotations

import asyncio
import json
import time

from src.memory.store import _connect


def _get_sync(key: str) -> dict | None:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT value, expires_at FROM kv_cache WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return None
        if row["expires_at"] <= time.time():
            conn.execute("DELETE FROM kv_cache WHERE key = ?", (key,))
            conn.commit()
            return None
        return json.loads(row["value"])
    finally:
        conn.close()


def _set_sync(key: str, value: dict, ttl: int) -> None:
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO kv_cache (key, value, expires_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, expires_at = excluded.expires_at
            """,
            (key, json.dumps(value, ensure_ascii=False, default=str), time.time() + ttl),
        )
        conn.commit()
    finally:
        conn.close()


async def get_cached(key: str) -> dict | None:
    return await asyncio.to_thread(_get_sync, key)


async def set_cached(key: str, value: dict, ttl: int) -> None:
    await asyncio.to_thread(_set_sync, key, value, ttl)
