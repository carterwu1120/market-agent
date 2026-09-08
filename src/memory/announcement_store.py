"""Persistent archive for TWSE MOPS material-information snapshots."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from src.memory.store import _connect

_TZ = ZoneInfo("Asia/Taipei")


def _upsert_announcements_sync(items: list[dict]) -> int:
    conn = _connect()
    try:
        before = conn.total_changes
        conn.executemany(
            """
            INSERT INTO company_announcements
                (symbol, announced_at, raw_date, raw_time, subject, source_url, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, announced_at, subject) DO UPDATE SET
                source_url = excluded.source_url,
                fetched_at = excluded.fetched_at
            """,
            [
                (
                    item["symbol"],
                    item["announced_at"],
                    item["date"],
                    item.get("time", ""),
                    item["subject"],
                    item["source_url"],
                    item["fetched_at"],
                )
                for item in items
            ],
        )
        conn.commit()
        return conn.total_changes - before
    finally:
        conn.close()


def _get_recent_announcements_sync(symbols: list[str], days: int) -> dict[str, list[dict]]:
    result = {symbol: [] for symbol in symbols}
    if not symbols:
        return result

    cutoff = (datetime.now(_TZ) - timedelta(days=days)).isoformat()
    placeholders = ",".join("?" for _ in symbols)
    conn = _connect()
    try:
        rows = conn.execute(
            f"""
            SELECT symbol, announced_at, raw_date, raw_time, subject, source_url
            FROM company_announcements
            WHERE symbol IN ({placeholders}) AND announced_at >= ?
            ORDER BY announced_at DESC
            """,
            (*symbols, cutoff),
        ).fetchall()
        for row in rows:
            result[row["symbol"]].append(
                {
                    "date": row["raw_date"],
                    "time": row["raw_time"],
                    "announced_at": row["announced_at"],
                    "subject": row["subject"],
                    "source": row["source_url"],
                }
            )
        return result
    finally:
        conn.close()


async def upsert_announcements(items: list[dict]) -> int:
    if not items:
        return 0
    return await asyncio.to_thread(_upsert_announcements_sync, items)


async def get_recent_announcements(symbols: list[str], days: int = 30) -> dict[str, list[dict]]:
    days = max(1, min(days, 365))
    return await asyncio.to_thread(_get_recent_announcements_sync, symbols, days)
