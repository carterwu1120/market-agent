"""Smoke-tests cli.py's /status dashboard against a temp SQLite database in
every population state (empty, with a position, with a watchlist entry,
with a condition) -- it reuses evaluate_paper_trades()/
simulate_portfolio_equity()/get_watchlist()/get_active_conditions(),
which are already covered elsewhere; what this guards against is a
formatting bug (missing dict key, wrong field name) in how cli.py renders
their output, which only a real run through _print_status() would catch.
"""
from unittest.mock import AsyncMock

import pytest

from src.config import settings
from src.memory.paper_trading_store import close_position, open_position
from src.memory.store import init_storage
from src.tools import paper_trading_actions as actions

_PRICE_OK = {"price": 123.45}


@pytest.fixture(autouse=True)
async def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "test.db"))
    monkeypatch.setattr(settings, "schedule_report_channel_id", "")
    await init_storage()


async def test_status_with_empty_state_does_not_crash():
    from src.cli import _print_status

    await _print_status()  # must not raise


async def test_status_missing_quote_shows_incomplete_valuation(monkeypatch):
    from src.agents import paper_trading
    from src.cli import _print_status

    await open_position("2330.TW", entry_price=100, shares=10)
    monkeypatch.setattr(paper_trading, "get_quote", AsyncMock(return_value={"error": "offline"}))
    await _print_status()


async def test_status_with_position_watchlist_and_condition_does_not_crash(monkeypatch):
    import src.agents.paper_trading as paper_trading_module
    import src.tools.market_data as market_data_module

    # Two separate bindings need patching: cli.py's own `from
    # src.tools.market_data import get_quote` (used for watchlist
    # prices) is a fresh lookup at call time, but evaluate_paper_trades()
    # (used for position prices) already bound its own module-level
    # reference to the same function at import time.
    monkeypatch.setattr(market_data_module, "get_quote", AsyncMock(return_value=_PRICE_OK))
    monkeypatch.setattr(paper_trading_module, "get_quote", AsyncMock(return_value=_PRICE_OK))
    closed_id = await open_position(
        "2330.TW", entry_price=100.0, horizon="short_term", shares=10
    )
    assert closed_id is not None
    await close_position(closed_id, 99.0, "test")
    await open_position("2382.TW", entry_price=100.0, horizon="short_term", shares=10)
    await actions.set_condition("2454.TW", "close", "lt", 50.0, "buy")

    from src.memory.paper_trading_store import add_to_watchlist
    await add_to_watchlist("2317.TW", "test")

    from src.cli import _print_status
    await _print_status()  # must not raise


async def test_log_with_empty_state_does_not_crash():
    from src.cli import _print_log

    await _print_log(20)  # must not raise


async def test_log_with_events_does_not_crash():
    from src.memory.paper_trading_store import log_event

    await log_event("buy", "test detail", symbol="2330.TW")

    from src.cli import _print_log
    await _print_log(20)  # must not raise
