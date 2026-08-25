"""Exercises the permanent paper_trading_log audit table: the store-level
log_event()/get_recent_log() round trip, and that buy()/sell()/
set_condition()/cancel_watch_condition() each write a row on success.
Expected behavior comes from the documented contract (most-recent-first,
never trimmed, best-effort) not from tracing the implementation.
"""
from unittest.mock import AsyncMock

import pytest

from src.config import settings
from src.memory.paper_trading_store import get_recent_log, log_event, open_position
from src.memory.store import init_storage
from src.tools import paper_trading_actions as actions

_PRICE_OK = {"last_price": 100.0}


@pytest.fixture(autouse=True)
async def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "test.db"))
    monkeypatch.setattr(settings, "schedule_report_channel_id", "")
    await init_storage()


# ── store-level round trip ──────────────────────────────────────────────

async def test_log_event_roundtrips_through_get_recent_log():
    await log_event("buy", "test detail", symbol="2330.TW")

    events = await get_recent_log()

    assert len(events) == 1
    assert events[0]["event_type"] == "buy"
    assert events[0]["symbol"] == "2330.TW"
    assert events[0]["detail"] == "test detail"


async def test_get_recent_log_orders_most_recent_first():
    await log_event("broad_scan", "first")
    await log_event("tight_scan", "second")

    events = await get_recent_log()

    assert [e["detail"] for e in events] == ["second", "first"]


async def test_get_recent_log_respects_limit():
    for i in range(5):
        await log_event("broad_scan", str(i))

    events = await get_recent_log(limit=2)

    assert len(events) == 2


async def test_symbol_is_optional_for_non_symbol_events():
    await log_event("broad_scan", "found 3 candidates")

    events = await get_recent_log()

    assert events[0]["symbol"] is None


# ── integration: paper_trading_actions writes to the log on success ─────

async def test_buy_writes_a_log_entry(monkeypatch):
    monkeypatch.setattr(actions, "get_stock_price", AsyncMock(return_value=_PRICE_OK))

    await actions.buy("2330.TW", "test reason")

    events = await get_recent_log()
    assert events[0]["event_type"] == "buy"
    assert events[0]["symbol"] == "2330.TW"


async def test_sell_writes_a_log_entry(monkeypatch):
    monkeypatch.setattr(actions, "get_stock_price", AsyncMock(return_value=_PRICE_OK))
    await open_position("2330.TW", entry_price=90.0, horizon="short_term")

    await actions.sell("2330.TW", "test reason", "take_profit")

    events = await get_recent_log()
    assert events[0]["event_type"] == "sell"
    assert events[0]["symbol"] == "2330.TW"


async def test_set_condition_writes_a_log_entry():
    await actions.set_condition("2330.TW", "close", "lt", 600.0, "buy")

    events = await get_recent_log()
    assert events[0]["event_type"] == "condition_set"
    assert events[0]["symbol"] == "2330.TW"


async def test_cancel_condition_writes_a_log_entry():
    created = await actions.set_condition("2330.TW", "close", "lt", 600.0, "buy")

    await actions.cancel_watch_condition(created["condition_id"])

    events = await get_recent_log()
    assert events[0]["event_type"] == "condition_cancelled"


async def test_failed_buy_does_not_write_a_log_entry(monkeypatch):
    monkeypatch.setattr(actions, "get_stock_price", AsyncMock(return_value={"error": "timeout"}))

    result = await actions.buy("2330.TW", "test reason")

    assert "error" in result
    assert await get_recent_log() == []
