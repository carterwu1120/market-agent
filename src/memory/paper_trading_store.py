"""Local SQLite persistence for the paper-trading tracker's position
lifecycle (open -> closed), written by src/agents/paper_trading_loop.py.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from loguru import logger

from src.memory.store import _connect


def _open_position_sync(symbol: str, entry_price: float, entry_reason: str) -> int:
    now = datetime.now(UTC)
    conn = _connect()
    try:
        cur = conn.execute(
            """
            INSERT INTO paper_positions
                (symbol, status, entry_price, entry_date, entry_reason, created_at)
            VALUES (?, 'open', ?, ?, ?, ?)
            """,
            (symbol, entry_price, now.date().isoformat(), entry_reason, now.timestamp()),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _close_position_sync(position_id: int, exit_price: float, exit_reason: str) -> None:
    now = datetime.now(UTC)
    conn = _connect()
    try:
        conn.execute(
            """
            UPDATE paper_positions
            SET status = 'closed', exit_price = ?, exit_date = ?, exit_reason = ?, closed_at = ?
            WHERE id = ? AND status = 'open'
            """,
            (exit_price, now.date().isoformat(), exit_reason, now.timestamp(), position_id),
        )
        conn.commit()
    finally:
        conn.close()


def _get_open_sync() -> list[dict]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM paper_positions WHERE status = 'open' ORDER BY created_at ASC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _get_all_sync() -> list[dict]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM paper_positions ORDER BY created_at ASC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


async def open_position(symbol: str, entry_price: float, entry_reason: str = "") -> int | None:
    """Records a new open position with a real fetched entry_price. Returns
    the new row id, or None if the write failed (best-effort, never raises)."""
    try:
        return await asyncio.to_thread(_open_position_sync, symbol, entry_price, entry_reason)
    except Exception as exc:
        logger.warning(f"paper_positions: open_position failed for {symbol}: {exc}")
        return None


async def close_position(position_id: int, exit_price: float, exit_reason: str = "") -> None:
    """Closes an existing open position with a real fetched exit_price."""
    try:
        await asyncio.to_thread(_close_position_sync, position_id, exit_price, exit_reason)
    except Exception as exc:
        logger.warning(f"paper_positions: close_position failed for id={position_id}: {exc}")


async def get_open_positions() -> list[dict]:
    return await asyncio.to_thread(_get_open_sync)


async def get_all_positions() -> list[dict]:
    return await asyncio.to_thread(_get_all_sync)
