"""Expected behavior comes from the documented contract: the daily budget
resets on a new date, accumulates across calls, and notifies exactly once
per day when first crossed -- not from tracing the implementation.
"""
from unittest.mock import AsyncMock

import pytest

from src.agents import paper_trading_loop as loop


@pytest.fixture(autouse=True)
def _reset_budget_state(monkeypatch):
    """Each test starts with a clean slate -- these are module globals."""
    monkeypatch.setattr(loop, "_daily_cost_usd", 0.0)
    monkeypatch.setattr(loop, "_daily_cost_date", None)
    monkeypatch.setattr(loop, "_budget_notified", False)
    monkeypatch.setattr(loop.settings, "paper_trading_daily_budget_usd", 1.0)
    monkeypatch.setattr(loop.settings, "schedule_report_channel_id", "")


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
