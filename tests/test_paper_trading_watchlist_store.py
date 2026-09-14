"""Exercises the real SQLite-backed watchlist functions against a temp
database (not the real data/market_agent.db). Expected behavior comes from
paper_trading_store.py's own documented contracts (INSERT OR IGNORE for
add, real DELETE for remove/expire, symbol as primary key), not from
tracing the SQL.
"""
import time

import pytest

from src.config import settings
from src.memory.paper_trading_store import (
    add_to_watchlist,
    expire_watchlist,
    get_watchlist,
    remove_from_watchlist,
    touch_watchlist,
    update_watchlist_assessment,
)
from src.memory.store import _connect, init_storage


@pytest.fixture(autouse=True)
async def _temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "test.db"))
    await init_storage()


async def test_add_then_get_roundtrips():
    await add_to_watchlist("2330.TW", "測試理由")
    watchlist = await get_watchlist()
    assert len(watchlist) == 1
    assert watchlist[0]["symbol"] == "2330.TW"
    assert watchlist[0]["reason"] == "測試理由"
    assert watchlist[0]["last_checked"] == 0


async def test_adding_same_symbol_twice_does_not_duplicate():
    await add_to_watchlist("2330.TW")
    await add_to_watchlist("2330.TW")
    watchlist = await get_watchlist()
    assert len(watchlist) == 1


async def test_remove_returns_true_when_symbol_was_present():
    await add_to_watchlist("2330.TW")
    removed = await remove_from_watchlist("2330.TW")
    assert removed is True
    assert await get_watchlist() == []


async def test_remove_returns_false_when_symbol_was_absent():
    removed = await remove_from_watchlist("9999.TW")
    assert removed is False


async def test_touch_updates_last_checked_for_named_symbols_only():
    await add_to_watchlist("2330.TW")
    await add_to_watchlist("2454.TW")
    await touch_watchlist(["2330.TW"], 12345.0)

    watchlist = {w["symbol"]: w for w in await get_watchlist()}
    assert watchlist["2330.TW"]["last_checked"] == 12345.0
    assert watchlist["2454.TW"]["last_checked"] == 0


async def test_expire_removes_only_entries_older_than_cutoff():
    now_ts = time.time()
    await add_to_watchlist("2330.TW")  # first_seen ~= now_ts, not stale

    conn = _connect()
    conn.execute(
        "INSERT INTO paper_watchlist (symbol, first_seen, last_checked, reason) "
        "VALUES (?, ?, 0, '')",
        ("2454.TW", now_ts - 10000),
    )
    conn.commit()
    conn.close()

    expired = await expire_watchlist(now_ts - 5000)

    assert expired == ["2454.TW"]
    remaining = {w["symbol"] for w in await get_watchlist()}
    assert remaining == {"2330.TW"}


async def test_long_term_watchlist_survives_intraday_expiry():
    now_ts = time.time()
    await add_to_watchlist("2454.TW")
    assert await update_watchlist_assessment(
        "2454.TW", "long_term", "developing", 60, "等待合理估值"
    )
    conn = _connect()
    conn.execute(
        "UPDATE paper_watchlist SET first_seen = ? WHERE symbol = ?",
        (now_ts - 10000, "2454.TW"),
    )
    conn.commit()
    conn.close()

    expired = await expire_watchlist(now_ts - 5000)

    assert expired == []
    item = (await get_watchlist())[0]
    assert item["strategy_horizon"] == "long_term"
    assert item["assessment_score"] == 60
    assert item["thesis"] == "等待合理估值"


async def test_existing_watchlist_schema_is_migrated_without_losing_rows():
    conn = _connect()
    conn.execute("DROP TABLE paper_watchlist")
    conn.execute(
        """
        CREATE TABLE paper_watchlist (
            symbol TEXT PRIMARY KEY,
            first_seen REAL NOT NULL,
            last_checked REAL NOT NULL DEFAULT 0,
            reason TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.execute(
        "INSERT INTO paper_watchlist (symbol, first_seen, reason) VALUES (?, ?, ?)",
        ("2330.TW", 1.0, "existing"),
    )
    conn.commit()
    conn.close()

    await init_storage()

    item = (await get_watchlist())[0]
    assert item["symbol"] == "2330.TW"
    assert item["strategy_horizon"] == "unclassified"
    assert item["assessment_status"] == "insufficient_evidence"
    assert item["assessment_score"] == 0
