"""Exercises paper_trading_actions.buy()'s per-horizon concentration limit
against a temp SQLite database. Expected behavior comes from the documented
contract added alongside src.config's paper_trading_max_short_term_positions/
paper_trading_max_long_term_positions: each horizon is capped independently,
not from tracing buy()'s implementation.
"""
from unittest.mock import AsyncMock

import pytest

from src.config import settings
from src.memory.store import init_storage
from src.tools import paper_trading_actions as actions

_PRICE_OK = {"price": 100.0}


@pytest.fixture(autouse=True)
async def _temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "test.db"))
    monkeypatch.setattr(settings, "paper_trading_max_short_term_positions", 2)
    monkeypatch.setattr(settings, "paper_trading_max_long_term_positions", 1)
    monkeypatch.setattr(settings, "schedule_report_channel_id", "")
    monkeypatch.setattr(actions, "get_quote", AsyncMock(return_value=_PRICE_OK))
    await init_storage()


async def test_buy_succeeds_under_cap():
    result = await actions.buy("2330.TW", "test", horizon="short_term")
    assert result["success"] is True


async def test_buy_blocked_at_cap():
    await actions.buy("2330.TW", "test", horizon="short_term")
    await actions.buy("2454.TW", "test", horizon="short_term")  # cap is 2, now full

    result = await actions.buy("2317.TW", "test", horizon="short_term")

    assert "error" in result
    assert "上限" in result["error"]


async def test_cap_is_tracked_separately_per_horizon():
    await actions.buy("2330.TW", "test", horizon="short_term")
    await actions.buy("2454.TW", "test", horizon="short_term")  # short_term now full (cap=2)

    result = await actions.buy("2317.TW", "test", horizon="long_term")  # long_term cap=1, empty

    assert result["success"] is True


async def test_long_term_cap_blocks_independently_of_short_term():
    await actions.buy("2330.TW", "test", horizon="long_term")  # long_term cap is 1, now full

    result = await actions.buy("2454.TW", "test", horizon="long_term")

    assert "error" in result
    assert "上限" in result["error"]
