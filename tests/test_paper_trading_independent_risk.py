import asyncio
from unittest.mock import AsyncMock

import pytest

from src.agents import paper_trading_loop as loop


async def test_slow_research_does_not_block_risk_and_shutdown_cancels_workers(monkeypatch):
    research_started = asyncio.Event()
    stop_checked = asyncio.Event()
    conditions_checked = asyncio.Event()
    cancelled = asyncio.Event()

    async def research():
        research_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async def stop():
        await research_started.wait()
        stop_checked.set()

    async def conditions():
        await research_started.wait()
        conditions_checked.set()

    monkeypatch.setattr(loop, "init_storage", AsyncMock())
    monkeypatch.setattr(loop, "_is_trading_hours", lambda _: True)
    monkeypatch.setattr(loop, "_research_loop", research)
    monkeypatch.setattr(loop, "_check_mechanical_stop_loss", stop)
    monkeypatch.setattr(loop, "_check_conditions", conditions)
    monkeypatch.setattr(loop, "_snapshot_equity", AsyncMock())
    task = asyncio.create_task(loop.run())
    try:
        await asyncio.wait_for(stop_checked.wait(), 2)
        await asyncio.wait_for(conditions_checked.wait(), 2)
        assert not task.done()
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert cancelled.is_set()
