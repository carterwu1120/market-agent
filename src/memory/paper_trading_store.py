"""Local SQLite persistence for the paper-trading tracker: position
lifecycle (open -> closed) and the watchlist, both written by
src/agents/paper_trading_loop.py and (for watchlist_drop/paper_trade_buy/
sell) by the MCP tool subprocess in src/tools/paper_trading_actions.py.

The watchlist is persisted (not in-memory) specifically so an agent-driven
watchlist_drop tool call -- running in a separate `claude -p` subprocess --
can actually affect it; two OS processes can only share state through the
database, not a Python dict.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from loguru import logger

from src.memory.store import _connect


def _open_position_sync(
    symbol: str,
    entry_price: float,
    entry_reason: str,
    horizon: str,
    shares: int,
    allocation_amount: float,
) -> int:
    now = datetime.now(UTC)
    conn = _connect()
    try:
        cur = conn.execute(
            """
            INSERT INTO paper_positions
                (symbol, status, horizon, shares, allocation_amount,
                 entry_price, entry_date, entry_reason, created_at)
            VALUES (?, 'open', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                symbol, horizon, shares, allocation_amount,
                entry_price, now.date().isoformat(), entry_reason, now.timestamp(),
            ),
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


def _get_open_sync(horizon: str | None) -> list[dict]:
    conn = _connect()
    try:
        if horizon:
            rows = conn.execute(
                "SELECT * FROM paper_positions WHERE status = 'open' AND horizon = ? "
                "ORDER BY created_at ASC",
                (horizon,),
            ).fetchall()
        else:
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


async def open_position(
    symbol: str,
    entry_price: float,
    entry_reason: str = "",
    horizon: str = "short_term",
    shares: int = 0,
    allocation_amount: float = 0.0,
) -> int | None:
    """Records a new open position with a real fetched entry_price. shares/
    allocation_amount are recorded at entry time (not recomputed later) so
    the capital-simulation replay in evaluate_paper_trades() stays accurate
    even if config sizing bounds change afterward. Returns the new row id,
    or None if the write failed (best-effort, never raises)."""
    try:
        return await asyncio.to_thread(
            _open_position_sync, symbol, entry_price, entry_reason, horizon, shares,
            allocation_amount,
        )
    except Exception as exc:
        logger.warning(f"paper_positions: open_position failed for {symbol}: {exc}")
        return None


async def close_position(position_id: int, exit_price: float, exit_reason: str = "") -> None:
    """Closes an existing open position with a real fetched exit_price."""
    try:
        await asyncio.to_thread(_close_position_sync, position_id, exit_price, exit_reason)
    except Exception as exc:
        logger.warning(f"paper_positions: close_position failed for id={position_id}: {exc}")


async def get_open_positions(horizon: str | None = None) -> list[dict]:
    """horizon=None returns both short_term and long_term positions."""
    return await asyncio.to_thread(_get_open_sync, horizon)


async def get_all_positions() -> list[dict]:
    return await asyncio.to_thread(_get_all_sync)


# ── Watchlist ──────────────────────────────────────────────────────────

def _add_to_watchlist_sync(symbol: str, reason: str, now_ts: float) -> None:
    conn = _connect()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO paper_watchlist (symbol, first_seen, last_checked, reason) "
            "VALUES (?, ?, 0, ?)",
            (symbol, now_ts, reason),
        )
        conn.commit()
    finally:
        conn.close()


def _remove_from_watchlist_sync(symbol: str) -> bool:
    conn = _connect()
    try:
        cur = conn.execute("DELETE FROM paper_watchlist WHERE symbol = ?", (symbol,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def _get_watchlist_sync() -> list[dict]:
    conn = _connect()
    try:
        rows = conn.execute("SELECT * FROM paper_watchlist ORDER BY first_seen ASC").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _touch_watchlist_sync(symbols: list[str], now_ts: float) -> None:
    conn = _connect()
    try:
        conn.executemany(
            "UPDATE paper_watchlist SET last_checked = ? WHERE symbol = ?",
            [(now_ts, s) for s in symbols],
        )
        conn.commit()
    finally:
        conn.close()


def _expire_watchlist_sync(cutoff_ts: float) -> list[str]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT symbol FROM paper_watchlist WHERE first_seen < ?", (cutoff_ts,)
        ).fetchall()
        expired = [r["symbol"] for r in rows]
        conn.execute("DELETE FROM paper_watchlist WHERE first_seen < ?", (cutoff_ts,))
        conn.commit()
        return expired
    finally:
        conn.close()


async def add_to_watchlist(symbol: str, reason: str = "") -> None:
    import time

    await asyncio.to_thread(_add_to_watchlist_sync, symbol, reason, time.time())


async def remove_from_watchlist(symbol: str) -> bool:
    """Returns True if the symbol was actually on the watchlist."""
    return await asyncio.to_thread(_remove_from_watchlist_sync, symbol)


async def get_watchlist() -> list[dict]:
    return await asyncio.to_thread(_get_watchlist_sync)


# ── Conditional orders ──────────────────────────────────────────────────

def _add_condition_sync(
    symbol: str, indicator: str, operator: str, threshold: float, action: str,
    reason: str, horizon: str, allocation_pct: float, exit_reason: str, now_ts: float,
) -> int:
    conn = _connect()
    try:
        cur = conn.execute(
            """
            INSERT INTO paper_trade_conditions
                (symbol, indicator, operator, threshold, action, horizon,
                 allocation_pct, exit_reason, reason, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)
            """,
            (
                symbol, indicator, operator, threshold, action, horizon,
                allocation_pct, exit_reason, reason, now_ts,
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _get_active_conditions_sync() -> list[dict]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM paper_trade_conditions WHERE status = 'active' ORDER BY created_at ASC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _set_condition_status_sync(condition_id: int, status: str, now_ts: float | None) -> bool:
    conn = _connect()
    try:
        cur = conn.execute(
            "UPDATE paper_trade_conditions SET status = ?, triggered_at = ? "
            "WHERE id = ? AND status = 'active'",
            (status, now_ts, condition_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


async def add_condition(
    symbol: str, indicator: str, operator: str, threshold: float, action: str,
    reason: str = "", horizon: str = "short_term", allocation_pct: float = 10.0,
    exit_reason: str = "llm_signal",
) -> int | None:
    """Records an agent-set conditional order. Returns the new row id, or
    None if the write failed (best-effort, never raises)."""
    import time

    try:
        return await asyncio.to_thread(
            _add_condition_sync, symbol, indicator, operator, threshold, action,
            reason, horizon, allocation_pct, exit_reason, time.time(),
        )
    except Exception as exc:
        logger.warning(f"paper_trade_conditions: add_condition failed for {symbol}: {exc}")
        return None


async def get_active_conditions() -> list[dict]:
    return await asyncio.to_thread(_get_active_conditions_sync)


async def mark_condition_triggered(condition_id: int) -> bool:
    """Returns True if an active condition with this id existed and was
    marked triggered (guards against double-firing the same condition)."""
    import time

    return await asyncio.to_thread(
        _set_condition_status_sync, condition_id, "triggered", time.time()
    )


async def cancel_condition(condition_id: int) -> bool:
    """Returns True if an active condition with this id existed and was
    cancelled."""
    return await asyncio.to_thread(_set_condition_status_sync, condition_id, "cancelled", None)


async def touch_watchlist(symbols: list[str], now_ts: float) -> None:
    if symbols:
        await asyncio.to_thread(_touch_watchlist_sync, symbols, now_ts)


async def expire_watchlist(cutoff_ts: float) -> list[str]:
    """Removes and returns symbols first seen before cutoff_ts -- the
    mechanical safety net for candidates the agent never explicitly
    dropped via watchlist_drop."""
    return await asyncio.to_thread(_expire_watchlist_sync, cutoff_ts)
