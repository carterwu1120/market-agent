"""Expected behavior comes from the documented contract: the daily budget
resets on a new date, accumulates across calls, and notifies exactly once
per day when first crossed -- not from tracing the implementation.
"""
from unittest.mock import AsyncMock

import pytest

from src.agents import paper_trading_loop as loop
from src.memory.store import init_storage


@pytest.fixture(autouse=True)
async def _reset_budget_state(tmp_path, monkeypatch):
    """Each test starts with a clean slate -- these are module globals.

    Also isolates db_path to a temp file: _track_cost() now calls
    log_event() (paper_trading_log audit table) when budget is exceeded,
    and without this, a test run would silently write real rows into
    data/market_agent.db -- exactly the kind of test/production bleed this
    project's other paper_trading tests already guard against."""
    monkeypatch.setattr(loop, "_daily_cost_usd", 0.0)
    monkeypatch.setattr(loop, "_daily_cost_date", None)
    monkeypatch.setattr(loop, "_budget_notified", False)
    monkeypatch.setattr(loop, "_daily_llm_calls", 0)
    monkeypatch.setattr(loop, "_daily_timeouts", 0)
    monkeypatch.setattr(loop.settings, "paper_trading_daily_budget_usd", 1.0)
    monkeypatch.setattr(loop.settings, "paper_trading_max_llm_calls_per_day", 10)
    monkeypatch.setattr(loop.settings, "paper_trading_max_timeouts_per_day", 3)
    monkeypatch.setattr(loop.settings, "schedule_report_channel_id", "")
    monkeypatch.setattr(loop.settings, "db_path", str(tmp_path / "test.db"))
    await init_storage()


def test_not_exceeded_before_any_spend():
    assert loop._budget_exceeded(loop._tw_now()) is False


@pytest.mark.asyncio
async def test_exceeded_after_crossing_budget():
    await loop._track_cost(0.6)
    assert loop._budget_exceeded(loop._tw_now()) is False  # under 1.0
    await loop._track_cost(0.5)
    assert loop._budget_exceeded(loop._tw_now()) is True  # 1.1 >= 1.0


@pytest.mark.asyncio
async def test_notification_fires_exactly_once(monkeypatch):
    send_mock = AsyncMock()
    monkeypatch.setattr(loop, "send_channel_message", send_mock)
    monkeypatch.setattr(loop.settings, "schedule_report_channel_id", "123")

    await loop._track_cost(1.5)  # crosses budget immediately
    await loop._track_cost(0.1)  # still over budget, must not notify again
    await loop._track_cost(0.1)

    assert send_mock.await_count == 1


@pytest.mark.asyncio
async def test_budget_resets_on_a_new_day(monkeypatch):
    import datetime

    await loop._track_cost(1.5)
    assert loop._budget_exceeded(loop._tw_now()) is True

    tomorrow = loop._tw_now() + datetime.timedelta(days=1)
    assert loop._budget_exceeded(tomorrow) is False


def test_call_count_caps_codex_even_without_usd_cost(monkeypatch):
    monkeypatch.setattr(loop.settings, "paper_trading_daily_budget_usd", 999.0)
    for _ in range(10):
        loop._record_llm_call()
    assert loop._budget_exceeded(loop._tw_now()) is True


def test_timeout_count_trips_circuit_breaker(monkeypatch):
    monkeypatch.setattr(loop.settings, "paper_trading_daily_budget_usd", 999.0)
    for _ in range(3):
        loop._record_llm_failure(RuntimeError("codex timed out"))
    assert loop._decision_limit_reason(loop._tw_now()) == "timeouts 3/3"
