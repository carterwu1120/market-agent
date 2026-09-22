"""Exercises the conditional-order feature end to end:
paper_trading_actions.set_condition()/cancel_watch_condition() (validation
+ storage) and paper_trading_loop._check_conditions() (the mechanical,
no-LLM-call evaluator that executes buy()/sell() the instant a condition
is true). Expected behavior comes from the documented contract -- a
condition fires once and is never re-evaluated after triggering -- not
from tracing the implementation.
"""
from unittest.mock import AsyncMock

import pytest

from src.agents import paper_trading_loop as loop
from src.config import settings
from src.memory.paper_trading_store import get_active_conditions, open_position
from src.memory.store import init_storage
from src.tools import paper_trading_actions as actions

_PRICE_OK = {"price": 100.0}
_TECHNICAL_OK = {"rsi_14": 25.0, "sma_20": 105.0}


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


# ── set_condition / cancel_watch_condition validation ───────────────────

async def test_set_condition_rejects_invalid_indicator():
    result = await actions.set_condition("2330.TW", "kd_j", "lt", 550.0, "buy")
    assert "error" in result


async def test_set_condition_rejects_invalid_operator():
    result = await actions.set_condition("2330.TW", "close", "below", 550.0, "buy")
    assert "error" in result


async def test_set_condition_normalizes_common_operator_symbol():
    result = await actions.set_condition("2330.TW", "close", ">=", 550.0, "buy")

    assert result["success"] is True
    condition = (await get_active_conditions())[0]
    assert condition["operator"] == "gte"


async def test_set_condition_rejects_invalid_action():
    result = await actions.set_condition("2330.TW", "close", "lt", 550.0, "hold")
    assert "error" in result


async def test_set_condition_success_returns_id():
    result = await actions.set_condition("2330.TW", "close", "lt", 550.0, "buy")
    assert result["success"] is True
    assert isinstance(result["condition_id"], int)


async def test_cancel_condition_succeeds_when_active():
    created = await actions.set_condition("2330.TW", "close", "lt", 550.0, "buy")
    result = await actions.cancel_watch_condition(created["condition_id"])
    assert result["success"] is True


async def test_cancel_condition_fails_when_not_found():
    result = await actions.cancel_watch_condition(9999)
    assert "error" in result


# ── _check_conditions() mechanical evaluation ───────────────────────────

async def test_buy_condition_triggers_when_price_crosses_below_threshold(monkeypatch):
    await actions.set_condition("2330.TW", "close", "lt", 150.0, "buy", allocation_pct=10.0)
    monkeypatch.setattr(loop, "get_quote", AsyncMock(return_value=_PRICE_OK))  # 100 < 150
    monkeypatch.setattr(loop, "get_technical_indicators", AsyncMock(return_value=_TECHNICAL_OK))
    buy_mock = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(loop, "buy", buy_mock)

    await loop._check_conditions()

    buy_mock.assert_awaited_once()
    assert buy_mock.await_args.args[0] == "2330.TW"


async def test_condition_does_not_trigger_when_threshold_not_met(monkeypatch):
    await actions.set_condition("2330.TW", "close", "lt", 50.0, "buy")  # 100 is not < 50
    monkeypatch.setattr(loop, "get_quote", AsyncMock(return_value=_PRICE_OK))
    monkeypatch.setattr(loop, "get_technical_indicators", AsyncMock(return_value=_TECHNICAL_OK))
    buy_mock = AsyncMock()
    monkeypatch.setattr(loop, "buy", buy_mock)

    await loop._check_conditions()

    buy_mock.assert_not_awaited()
    assert len(await get_active_conditions()) == 1  # still active, untouched


async def test_sell_condition_triggers_and_calls_sell(monkeypatch):
    await open_position("2330.TW", entry_price=100.0, horizon="short_term")
    await actions.set_condition("2330.TW", "rsi_14", "lt", 30.0, "sell", exit_reason="take_profit")
    monkeypatch.setattr(loop, "get_quote", AsyncMock(return_value=_PRICE_OK))
    monkeypatch.setattr(loop, "get_technical_indicators", AsyncMock(return_value=_TECHNICAL_OK))
    sell_mock = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(loop, "sell", sell_mock)

    await loop._check_conditions()

    sell_mock.assert_awaited_once()
    assert sell_mock.await_args.kwargs.get("exit_reason", sell_mock.await_args.args[-1])


async def test_triggered_condition_is_not_reevaluated(monkeypatch):
    await actions.set_condition("2330.TW", "close", "lt", 150.0, "buy")
    monkeypatch.setattr(loop, "get_quote", AsyncMock(return_value=_PRICE_OK))
    monkeypatch.setattr(loop, "get_technical_indicators", AsyncMock(return_value=_TECHNICAL_OK))
    buy_mock = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(loop, "buy", buy_mock)

    await loop._check_conditions()
    await loop._check_conditions()  # second tick

    assert buy_mock.await_count == 1
    assert await get_active_conditions() == []


async def test_kd_condition_triggers_using_new_technical_field(monkeypatch):
    # documents the contract added alongside the KD/MA5/MA10/volume_ratio
    # indicators: they flow through the same _CONDITION_TECHNICAL_FIELDS
    # path as the original fields, not a separate code path.
    await actions.set_condition("2330.TW", "kd_k", "lt", 30.0, "sell", exit_reason="stop_loss")
    await open_position("2330.TW", entry_price=100.0, horizon="short_term")
    monkeypatch.setattr(loop, "get_quote", AsyncMock(return_value=_PRICE_OK))
    monkeypatch.setattr(
        loop, "get_technical_indicators", AsyncMock(return_value={"kd_k": 25.0, "kd_d": 40.0})
    )
    sell_mock = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(loop, "sell", sell_mock)

    await loop._check_conditions()

    sell_mock.assert_awaited_once()


async def test_streak_condition_fetches_institutional_data_only_when_referenced(monkeypatch):
    await actions.set_condition("2330.TW", "trust_streak_days", "gte", 3.0, "buy")
    monkeypatch.setattr(loop, "get_quote", AsyncMock(return_value=_PRICE_OK))
    monkeypatch.setattr(loop, "get_technical_indicators", AsyncMock(return_value=_TECHNICAL_OK))
    streak_mock = AsyncMock(return_value={"trust_streak_days": 5, "foreign_streak_days": 0})
    monkeypatch.setattr(loop, "get_institutional_streak", streak_mock)
    buy_mock = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(loop, "buy", buy_mock)

    await loop._check_conditions()

    streak_mock.assert_awaited_once_with("2330.TW")
    buy_mock.assert_awaited_once()


async def test_streak_not_fetched_when_no_condition_references_it(monkeypatch):
    await actions.set_condition("2330.TW", "close", "lt", 150.0, "buy")
    monkeypatch.setattr(loop, "get_quote", AsyncMock(return_value=_PRICE_OK))
    monkeypatch.setattr(loop, "get_technical_indicators", AsyncMock(return_value=_TECHNICAL_OK))
    streak_mock = AsyncMock()
    monkeypatch.setattr(loop, "get_institutional_streak", streak_mock)
    monkeypatch.setattr(loop, "buy", AsyncMock(return_value={"success": True}))

    await loop._check_conditions()

    streak_mock.assert_not_awaited()


async def test_missing_indicator_value_skips_without_crashing(monkeypatch):
    await actions.set_condition("2330.TW", "rsi_14", "lt", 30.0, "buy")
    monkeypatch.setattr(loop, "get_quote", AsyncMock(return_value=_PRICE_OK))
    monkeypatch.setattr(
        loop, "get_technical_indicators", AsyncMock(return_value={"error": "fetch failed"})
    )
    buy_mock = AsyncMock()
    monkeypatch.setattr(loop, "buy", buy_mock)

    await loop._check_conditions()  # must not raise

    buy_mock.assert_not_awaited()
    assert len(await get_active_conditions()) == 1


async def test_expired_buy_is_cancelled_without_execution(monkeypatch):
    await actions.set_condition("2330.TW", "close", "gte", 100, "buy")
    monkeypatch.setattr(settings, "paper_trading_condition_ttl_seconds", 0)
    buy_mock = AsyncMock()
    monkeypatch.setattr(loop, "buy", buy_mock)
    await loop._check_conditions()
    buy_mock.assert_not_awaited()
    assert await get_active_conditions() == []


async def test_failed_sell_remains_active_until_success(monkeypatch):
    await actions.set_condition("2330.TW", "close", "lt", 150, "sell")
    monkeypatch.setattr(loop, "get_quote", AsyncMock(return_value=_PRICE_OK))
    monkeypatch.setattr(loop, "get_technical_indicators", AsyncMock(return_value=_TECHNICAL_OK))
    monkeypatch.setattr(loop, "sell", AsyncMock(side_effect=[
        {"error": "quote unavailable"}, {"success": True},
    ]))
    await loop._check_conditions()
    assert len(await get_active_conditions()) == 1
    await loop._check_conditions()
    assert await get_active_conditions() == []


@pytest.mark.parametrize("price", [99, 103])
async def test_buy_revalidates_actual_execution_quote(monkeypatch, price):
    await actions.set_condition("2330.TW", "close", "gte", 100, "buy")
    condition = (await get_active_conditions())[0]
    monkeypatch.setattr(actions, "get_quote", AsyncMock(return_value={"price": price}))
    result = await actions.buy("2330.TW", "test", condition=condition)
    assert "error" in result
    assert await actions._broker.get_positions() == []
