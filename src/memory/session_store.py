"""Local SQLite-backed conversation session store.

One row per channel (shared across all users in the channel), storing the
whole rolling message list as a JSON blob — mirrors the old Redis List +
LTRIM + EXPIRE semantics closely enough at this data volume.

Key: channel_id. Messages include a [username] prefix so the LLM can
distinguish speakers and decide whether a follow-up refers to a previous
user's topic.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from src.config import settings
from src.memory.store import _connect


def _get_messages_sync(channel_id: str) -> list[dict[str, Any]]:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT messages, expires_at FROM sessions WHERE channel_id = ?", (channel_id,)
        ).fetchone()
        if row is None or row["expires_at"] <= time.time():
            return []
        return json.loads(row["messages"])
    finally:
        conn.close()


def _append_message_sync(
    channel_id: str,
    role: str,
    content: str,
    meta: dict | None,
    max_messages: int,
    username: str,
    user_id: str,
) -> None:
    conn = _connect()
    try:
        # BEGIN IMMEDIATE takes the write lock before the SELECT, so a second
        # concurrent append (e.g. two messages landing in the same channel at
        # once) blocks until this one commits instead of racing on a stale
        # read and silently dropping one of the two appends.
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT messages, expires_at FROM sessions WHERE channel_id = ?", (channel_id,)
        ).fetchone()
        messages = (
            json.loads(row["messages"])
            if row is not None and row["expires_at"] > time.time()
            else []
        )

        stored_content = f"[{username}]: {content}" if role == "user" and username else content
        messages.append({"role": role, "content": stored_content, "meta": meta or {}})
        messages = messages[-max_messages:]

        conn.execute(
            """
            INSERT INTO sessions (channel_id, messages, expires_at) VALUES (?, ?, ?)
            ON CONFLICT(channel_id) DO UPDATE SET messages = excluded.messages, expires_at = excluded.expires_at
            """,
            (channel_id, json.dumps(messages, ensure_ascii=False), time.time() + settings.session_ttl_seconds),
        )
        # Permanent audit log — never expires, never trimmed, independent of
        # the rolling `sessions` window above (see conversation_log's comment
        # in store.py's schema for why this exists as a separate table).
        conn.execute(
            """
            INSERT INTO conversation_log (channel_id, user_id, role, content, meta, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                channel_id, user_id, role, stored_content,
                json.dumps(meta or {}, ensure_ascii=False), time.time(),
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _get_conversation_log_sync(channel_id: str, limit: int) -> list[dict[str, Any]]:
    conn = _connect()
    try:
        rows = conn.execute(
            """
            SELECT user_id, role, content, meta, created_at FROM conversation_log
            WHERE channel_id = ? ORDER BY created_at DESC LIMIT ?
            """,
            (channel_id, limit),
        ).fetchall()
        return [
            {
                "user_id": r["user_id"],
                "role": r["role"],
                "content": r["content"],
                "meta": json.loads(r["meta"]) if r["meta"] else {},
                "created_at": r["created_at"],
            }
            for r in reversed(rows)
        ]
    finally:
        conn.close()


def _clear_session_sync(channel_id: str) -> None:
    conn = _connect()
    try:
        conn.execute("DELETE FROM sessions WHERE channel_id = ?", (channel_id,))
        conn.commit()
    finally:
        conn.close()


async def get_session_messages(channel_id: str, user_id: str = "") -> list[dict[str, Any]]:
    return await asyncio.to_thread(_get_messages_sync, channel_id)


async def append_message(
    channel_id: str,
    user_id: str,
    role: str,
    content: str,
    meta: dict | None = None,
    max_messages: int = 20,
    username: str = "",
) -> None:
    await asyncio.to_thread(
        _append_message_sync, channel_id, role, content, meta, max_messages, username, user_id
    )


async def get_conversation_log(channel_id: str, limit: int = 100) -> list[dict[str, Any]]:
    """Full permanent history for a channel, oldest first — unlike
    get_session_messages, never capped or expired."""
    return await asyncio.to_thread(_get_conversation_log_sync, channel_id, limit)


async def clear_session(channel_id: str, user_id: str = "") -> None:
    """Clears only the rolling LLM-context window (`sessions`). The permanent
    conversation_log audit trail is intentionally untouched by this."""
    await asyncio.to_thread(_clear_session_sync, channel_id)
