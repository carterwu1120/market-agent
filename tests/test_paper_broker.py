"""Exercises PaperBroker in isolation -- the concrete implementation of the
Broker seam (src/tools/broker.py) introduced so paper_trading_actions.py's
business rules don't depend on *how* a trade gets executed. Expected
behavior comes from the documented contract (get_cash/get_positions read
through to the existing store functions; execute_buy/execute_sell perform
the same open/close + watchlist-removal paper_trading_actions.buy()/sell()
used to do inline) not from tracing the implementation.
"""
import pytest

from src.config import settings
from src.memory.paper_trading_store import add_to_watchlist, get_watchlist
from src.memory.store import init_storage
from src.tools.paper_broker import PaperBroker

broker = PaperBroker()


@pytest.fixture(autouse=True)
async def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "test.db"))
    monkeypatch.setattr(settings, "paper_trading_starting_capital", 500_000.0)
    await init_storage()


async def test_get_cash_starts_at_starting_capital():
    assert await broker.get_cash() == 500_000.0


async def test_get_positions_empty_when_none_open():
    assert await broker.get_positions() == []


async def test_execute_buy_opens_a_position_and_removes_from_watchlist():
    await add_to_watchlist("2330.TW", "test")

    fill = await broker.execute_buy("2330.TW", 100.0, 500, 50_000.0, "test reason", "short_term")

    assert fill is not None
    assert fill["position_id"] is not None
    positions = await broker.get_positions()
    assert len(positions) == 1
    assert positions[0]["symbol"] == "2330.TW"
    assert positions[0]["shares"] == 500
    assert await get_watchlist() == []


async def test_execute_buy_returns_fill_price_equal_to_reference_price():
    fill = await broker.execute_buy("2330.TW", 100.0, 500, 50_000.0, "test reason", "short_term")

    assert fill["fill_price"] == 100.0


async def test_execute_buy_reduces_available_cash():
    await broker.execute_buy("2330.TW", 100.0, 500, 50_000.0, "test reason", "short_term")

    assert await broker.get_cash() == 450_000.0


async def test_execute_sell_closes_the_position():
    fill = await broker.execute_buy(
        "2330.TW", 100.0, 500, 50_000.0, "test reason", "short_term"
    )

    sell_fill = await broker.execute_sell(fill["position_id"], 120.0, "take_profit")

    assert sell_fill["fill_price"] == 120.0
    assert await broker.get_positions() == []


async def test_get_positions_filters_by_horizon():
    await broker.execute_buy("2330.TW", 100.0, 500, 50_000.0, "test", "short_term")
    await broker.execute_buy("2454.TW", 100.0, 500, 50_000.0, "test", "long_term")

    short_term = await broker.get_positions(horizon="short_term")

    assert [p["symbol"] for p in short_term] == ["2330.TW"]
