from unittest.mock import AsyncMock

import pytest

from src.agents import paper_trading_loop as loop


def test_select_due_watchlist_limits_and_rotates_oldest_first():
    now = 10_000.0
    watchlist = [
        {"symbol": "D.TW", "first_seen": 4.0, "last_checked": 9_500.0},
        {"symbol": "C.TW", "first_seen": 3.0, "last_checked": 100.0},
        {"symbol": "B.TW", "first_seen": 2.0, "last_checked": 0.0},
        {"symbol": "A.TW", "first_seen": 1.0, "last_checked": 0.0},
        {"symbol": "E.TW", "first_seen": 5.0, "last_checked": 200.0},
    ]

    selected, queued = loop._select_due_watchlist(watchlist, now)

    assert selected == ["A.TW", "B.TW", "C.TW"]
    assert queued == 1


def test_select_due_watchlist_reports_empty_batch():
    now = 10_000.0
    watchlist = [
        {"symbol": "A.TW", "first_seen": 1.0, "last_checked": 9_500.0},
    ]

    assert loop._select_due_watchlist(watchlist, now) == ([], 0)


@pytest.mark.asyncio
async def test_tight_scan_logs_research_failure_without_touching_watchlist(monkeypatch):
    watchlist = [
        {"symbol": f"000{i}.TW", "first_seen": float(i), "last_checked": 0.0}
        for i in range(1, 5)
    ]
    log_event = AsyncMock()
    touch_watchlist = AsyncMock()
    run_research = AsyncMock(side_effect=RuntimeError("timed out"))
    monkeypatch.setattr(loop, "get_watchlist", AsyncMock(return_value=watchlist))
    monkeypatch.setattr(loop, "get_open_positions", AsyncMock(return_value=[]))
    monkeypatch.setattr(loop, "get_active_conditions", AsyncMock(return_value=[]))
    monkeypatch.setattr(loop, "run_research", run_research)
    monkeypatch.setattr(loop, "log_event", log_event)
    monkeypatch.setattr(loop, "touch_watchlist", touch_watchlist)

    await loop._tight_scan()

    prompt = run_research.await_args.args[0]
    assert "0001.TW" in prompt
    assert "0003.TW" in prompt
    assert "0004.TW" not in prompt
    assert log_event.await_args.args[0] == "research_failed"
    assert "queued=1" in log_event.await_args.args[1]
    touch_watchlist.assert_not_awaited()


@pytest.mark.asyncio
async def test_tight_scan_touches_only_successful_batch(monkeypatch):
    watchlist = [
        {"symbol": f"000{i}.TW", "first_seen": float(i), "last_checked": 0.0}
        for i in range(1, 5)
    ]
    get_watchlist = AsyncMock(side_effect=[watchlist, watchlist])
    touch_watchlist = AsyncMock()
    monkeypatch.setattr(loop, "get_watchlist", get_watchlist)
    monkeypatch.setattr(loop, "get_open_positions", AsyncMock(return_value=[]))
    monkeypatch.setattr(loop, "get_active_conditions", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        loop,
        "run_research",
        AsyncMock(return_value={"cost_usd": 0.0, "conclusion": "done"}),
    )
    monkeypatch.setattr(loop, "log_event", AsyncMock())
    monkeypatch.setattr(loop, "touch_watchlist", touch_watchlist)

    await loop._tight_scan()

    assert touch_watchlist.await_args.args[0] == ["0001.TW", "0002.TW", "0003.TW"]
