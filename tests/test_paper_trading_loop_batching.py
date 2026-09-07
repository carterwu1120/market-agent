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

    assert selected == ["A.TW", "B.TW"]
    assert queued == 2


def test_select_due_watchlist_reports_empty_batch():
    now = 10_000.0
    watchlist = [
        {"symbol": "A.TW", "first_seen": 1.0, "last_checked": 9_500.0},
    ]

    assert loop._select_due_watchlist(watchlist, now) == ([], 0)


@pytest.mark.asyncio
async def test_tight_scan_cools_down_failed_watchlist_batch(monkeypatch):
    watchlist = [
        {"symbol": f"000{i}.TW", "first_seen": float(i), "last_checked": 0.0}
        for i in range(1, 5)
    ]
    log_event = AsyncMock()
    touch_watchlist = AsyncMock()
    run_decision = AsyncMock(side_effect=RuntimeError("timed out"))
    monkeypatch.setattr(loop, "get_watchlist", AsyncMock(return_value=watchlist))
    monkeypatch.setattr(loop, "get_open_positions", AsyncMock(return_value=[]))
    monkeypatch.setattr(loop, "get_active_conditions", AsyncMock(return_value=[]))
    monkeypatch.setattr(loop, "run_paper_decision", run_decision)
    monkeypatch.setattr(loop, "log_event", log_event)
    monkeypatch.setattr(loop, "touch_watchlist", touch_watchlist)

    await loop._tight_scan()

    targets = run_decision.await_args.args[0]
    assert [target["symbol"] for target in targets] == ["0001.TW", "0002.TW"]
    assert log_event.await_args.args[0] == "research_failed"
    assert "queued=2" in log_event.await_args.args[1]
    assert touch_watchlist.await_args.args[0] == ["0001.TW", "0002.TW"]


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
        "run_paper_decision",
        AsyncMock(
            return_value={
                "results": [
                    {"symbol": "0001.TW", "action": "set_condition", "success": True},
                    {"symbol": "0002.TW", "action": "drop_watchlist", "success": True},
                ]
            }
        ),
    )
    monkeypatch.setattr(loop, "log_event", AsyncMock())
    monkeypatch.setattr(loop, "touch_watchlist", touch_watchlist)

    await loop._tight_scan()

    assert touch_watchlist.await_args.args[0] == ["0001.TW", "0002.TW"]


def test_build_targets_balances_watchlist_and_positions(monkeypatch):
    monkeypatch.setattr(loop, "_position_last_checked", {})
    watchlist = [
        {"symbol": "1111.TW", "first_seen": 1.0, "last_checked": 0.0},
        {"symbol": "2222.TW", "first_seen": 2.0, "last_checked": 0.0},
    ]
    positions = [
        {"symbol": "3333.TW", "created_at": 1.0},
        {"symbol": "4444.TW", "created_at": 2.0},
    ]

    targets, queued = loop._build_targets(watchlist, positions, 10_000.0)

    assert [(target["symbol"], target["role"]) for target in targets] == [
        ("1111.TW", "watchlist"),
        ("3333.TW", "position"),
    ]
    assert queued == 1
