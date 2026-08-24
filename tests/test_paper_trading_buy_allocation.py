"""Exercises paper_trading_actions.buy()'s allocation sizing and cash-based
blocking against a temp SQLite database. Expected behavior comes from the
documented contract: shares are computed from allocation_pct (clamped) x
starting_capital, and a buy is rejected outright when simulated cash can't
cover it -- not from tracing buy()'s implementation.
"""
from unittest.mock import AsyncMock

import pytest

from src.config import settings
from src.memory.store import init_storage
from src.tools import paper_trading_actions as actions


@pytest.fixture(autouse=True)
async def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "test.db"))
    monkeypatch.setattr(settings, "paper_trading_max_short_term_positions", 20)
    monkeypatch.setattr(settings, "paper_trading_max_long_term_positions", 20)
    monkeypatch.setattr(settings, "paper_trading_starting_capital", 500_000.0)
    monkeypatch.setattr(settings, "paper_trading_min_allocation_pct", 5.0)
    monkeypatch.setattr(settings, "paper_trading_max_allocation_pct", 20.0)
    monkeypatch.setattr(settings, "schedule_report_channel_id", "")
    await init_storage()


async def test_buy_records_shares_and_allocation_from_pct(monkeypatch):
    monkeypatch.setattr(actions, "get_stock_price", AsyncMock(return_value={"last_price": 100.0}))

    result = await actions.buy("2330.TW", "test", allocation_pct=10.0)

    assert result["success"] is True
    assert result["shares"] == 500  # 10% of 500,000 = 50,000 / 100
    assert result["allocation_amount"] == 50_000.0


async def test_buy_rejected_when_simulated_cash_cannot_cover_it(monkeypatch):
    monkeypatch.setattr(actions, "get_stock_price", AsyncMock(return_value={"last_price": 100.0}))
    # 20% (max) x 500,000 = 100,000 per trade; 5 buys commit the full 500,000
    for i in range(5):
        result = await actions.buy(f"{1000 + i}.TW", "test", allocation_pct=20.0)
        assert result["success"] is True

    blocked = await actions.buy("9999.TW", "test", allocation_pct=20.0)

    assert "error" in blocked
    assert "現金不足" in blocked["error"]


async def test_buy_rejected_when_price_too_high_for_min_allocation(monkeypatch):
    # min allocation 5% of 500,000 = 25,000; a 30,000 stock buys 0 shares
    monkeypatch.setattr(
        actions, "get_stock_price", AsyncMock(return_value={"last_price": 30_000.0})
    )

    result = await actions.buy("9999.TW", "test", allocation_pct=5.0)

    assert "error" in result
    assert "過高" in result["error"]
