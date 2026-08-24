"""Exercises the mechanical stop-loss safety net in paper_trading_loop.py
against a temp SQLite database. Expected behavior comes from the
documented contract: force-sell a position once pnl_pct <= -threshold,
threshold picked per horizon (short_term vs long_term), independent of
any agent judgment -- not from tracing the implementation.
"""
from unittest.mock import AsyncMock

import pytest

from src.agents import paper_trading_loop as loop
from src.config import settings
from src.memory.paper_trading_store import get_open_positions, open_position
from src.memory.store import init_storage


@pytest.fixture(autouse=True)
async def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "test.db"))
    monkeypatch.setattr(settings, "paper_trading_short_term_stop_loss_pct", 15.0)
    monkeypatch.setattr(settings, "paper_trading_long_term_stop_loss_pct", 20.0)
    monkeypatch.setattr(settings, "schedule_report_channel_id", "")
    await init_storage()


def _price(value: float) -> AsyncMock:
    return AsyncMock(return_value={"last_price": value})


async def test_position_within_threshold_is_left_open(monkeypatch):
    await open_position("2330.TW", entry_price=100.0, horizon="short_term")
    monkeypatch.setattr(loop, "get_stock_price", _price(90.0))  # -10%, under 15% threshold
    sell_mock = AsyncMock()
    monkeypatch.setattr(loop, "sell", sell_mock)

    await loop._check_mechanical_stop_loss()

    sell_mock.assert_not_awaited()
    assert len(await get_open_positions()) == 1


async def test_short_term_position_breaching_threshold_is_force_sold(monkeypatch):
    await open_position("2330.TW", entry_price=100.0, horizon="short_term")
    monkeypatch.setattr(loop, "get_stock_price", _price(84.0))  # -16%, past 15% threshold
    sell_mock = AsyncMock()
    monkeypatch.setattr(loop, "sell", sell_mock)

    await loop._check_mechanical_stop_loss()

    sell_mock.assert_awaited_once()
    assert sell_mock.await_args.args[0] == "2330.TW"
    assert sell_mock.await_args.kwargs["exit_reason"] == "stop_loss"


async def test_long_term_position_uses_its_own_wider_threshold(monkeypatch):
    await open_position("2454.TW", entry_price=100.0, horizon="long_term")
    monkeypatch.setattr(loop, "get_stock_price", _price(82.0))  # -18%: under short_term's 15
    sell_mock = AsyncMock()                                     # threshold but under long_term's 20
    monkeypatch.setattr(loop, "sell", sell_mock)

    await loop._check_mechanical_stop_loss()

    sell_mock.assert_not_awaited()


async def test_price_fetch_error_does_not_crash_or_trigger_sell(monkeypatch):
    await open_position("2330.TW", entry_price=100.0, horizon="short_term")
    monkeypatch.setattr(loop, "get_stock_price", AsyncMock(return_value={"error": "timeout"}))
    sell_mock = AsyncMock()
    monkeypatch.setattr(loop, "sell", sell_mock)

    await loop._check_mechanical_stop_loss()

    sell_mock.assert_not_awaited()
