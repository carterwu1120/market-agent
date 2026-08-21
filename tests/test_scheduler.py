"""_send_scheduled_report/_send_weekend_digest are the real logic behind the
@tasks.loop-decorated functions in src/bot/scheduler.py; the loop scheduling
itself is discord.ext.tasks library behavior, not re-tested here.

Expected behavior comes from the module docstring's own table (pre/mid/post
skip Sat/Sun; weekend_digest fires only Sunday) -- each case asserts that on
a "should skip" day, the expensive pipeline call (run_agent/run_weekend_digest)
and the Discord send are never reached at all, not just that the return value
looks right.
"""
import datetime as real_datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.bot import scheduler


def _patch_now(monkeypatch, fixed: real_datetime.datetime):
    class _FixedDatetime:
        @staticmethod
        def now(tz=None):
            return fixed

    monkeypatch.setattr(scheduler, "datetime", SimpleNamespace(datetime=_FixedDatetime))


def _make_fake_bot():
    channel = SimpleNamespace(send=AsyncMock())
    bot = SimpleNamespace(get_channel=lambda cid: channel)
    return bot, channel


SATURDAY = real_datetime.datetime(2026, 8, 22, 9, 0)   # 2026-08-22 is a Saturday
SUNDAY = real_datetime.datetime(2026, 8, 23, 20, 0)    # 2026-08-23 is a Sunday
MONDAY = real_datetime.datetime(2026, 8, 24, 8, 30)    # 2026-08-24 is a Monday


@pytest.mark.asyncio
@pytest.mark.parametrize("weekend_day", [SATURDAY, SUNDAY])
async def test_weekday_report_skips_on_weekend(monkeypatch, weekend_day):
    _patch_now(monkeypatch, weekend_day)
    bot, channel = _make_fake_bot()
    monkeypatch.setattr(scheduler, "_bot", bot)
    monkeypatch.setattr(scheduler.settings, "schedule_report_channel_id", "123")
    run_agent_mock = AsyncMock()
    monkeypatch.setattr(scheduler, "run_agent", run_agent_mock)

    await scheduler._send_scheduled_report("pre_market")

    run_agent_mock.assert_not_called()
    channel.send.assert_not_called()


@pytest.mark.asyncio
async def test_weekday_report_runs_on_a_trading_day(monkeypatch):
    _patch_now(monkeypatch, MONDAY)
    bot, channel = _make_fake_bot()
    monkeypatch.setattr(scheduler, "_bot", bot)
    monkeypatch.setattr(scheduler.settings, "schedule_report_channel_id", "123")
    run_agent_mock = AsyncMock(return_value={"final_report": "測試報告內容"})
    monkeypatch.setattr(scheduler, "run_agent", run_agent_mock)

    await scheduler._send_scheduled_report("pre_market")

    run_agent_mock.assert_called_once()
    channel.send.assert_called_once_with("測試報告內容")


@pytest.mark.asyncio
@pytest.mark.parametrize("non_sunday", [SATURDAY, MONDAY])
async def test_weekend_digest_skips_on_non_sunday(monkeypatch, non_sunday):
    _patch_now(monkeypatch, non_sunday)
    bot, channel = _make_fake_bot()
    monkeypatch.setattr(scheduler, "_bot", bot)
    monkeypatch.setattr(scheduler.settings, "schedule_report_channel_id", "123")
    run_digest_mock = AsyncMock()
    monkeypatch.setattr(scheduler, "run_weekend_digest", run_digest_mock)

    await scheduler._send_weekend_digest()

    run_digest_mock.assert_not_called()
    channel.send.assert_not_called()


@pytest.mark.asyncio
async def test_weekend_digest_runs_on_sunday(monkeypatch):
    _patch_now(monkeypatch, SUNDAY)
    bot, channel = _make_fake_bot()
    monkeypatch.setattr(scheduler, "_bot", bot)
    monkeypatch.setattr(scheduler.settings, "schedule_report_channel_id", "123")
    run_digest_mock = AsyncMock(return_value={"final_report": "假日摘要內容"})
    monkeypatch.setattr(scheduler, "run_weekend_digest", run_digest_mock)

    await scheduler._send_weekend_digest()

    run_digest_mock.assert_called_once()
    channel.send.assert_called_once_with("假日摘要內容")
