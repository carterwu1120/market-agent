from unittest.mock import AsyncMock

import pytest

from src.config import settings
from src.memory.store import init_storage


@pytest.fixture(autouse=True)
async def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "test.db"))
    monkeypatch.setattr(settings, "llm_backend", "codex")
    monkeypatch.setattr(settings, "paper_trading_enabled", True)
    await init_storage()


async def test_discord_status_reports_account_and_empty_lists():
    from src.bot.discord_bot import _build_paper_status_message

    message = await _build_paper_status_message()

    assert "Market Agent 狀態" in message
    assert "`codex`" in message
    assert "紙上交易：**啟用**" in message
    assert "目前沒有持倉" in message
    assert "目前持倉（0 筆）" in message
    assert "最近已平倉（0 筆）" in message
    assert "尚無已平倉交易" in message
    assert "觀察名單（0 檔）" in message
    assert "有效條件單（0 筆）" in message


async def test_discord_status_separates_open_and_closed_positions(monkeypatch):
    import src.agents.paper_trading as paper_trading_module
    from src.bot.discord_bot import _build_paper_status_message
    from src.memory.paper_trading_store import close_position, open_position

    monkeypatch.setattr(
        paper_trading_module,
        "get_quote",
        AsyncMock(return_value={"price": 110.0}),
    )
    closed_id = await open_position("2330.TW", 100.0, shares=10)
    assert closed_id is not None
    await close_position(closed_id, 99.0, "test")
    await open_position("2382.TW", 100.0, shares=20)

    message = await _build_paper_status_message()

    current_section, closed_section = message.split("**最近已平倉（1 筆）**")
    current_positions = current_section.split("**目前持倉（1 筆）**", 1)[1]
    assert "2382.TW" in current_positions
    assert "未實現損益" in current_positions
    assert "2330.TW" not in current_positions
    assert "2330.TW" in closed_section
    assert "已實現損益" in closed_section


async def test_discord_log_reports_persisted_event():
    from src.bot.discord_bot import _build_paper_log_message
    from src.memory.paper_trading_store import log_event

    await log_event("tight_scan", "checked two symbols", symbol="2330.TW")

    message = await _build_paper_log_message(20)

    assert "紙上交易執行紀錄（最近 1 筆）" in message
    assert "tight_scan" in message
    assert "2330.TW" in message
    assert "checked two symbols" in message


async def test_discord_log_limit_is_clamped(monkeypatch):
    import src.memory.paper_trading_store as store
    from src.bot.discord_bot import _build_paper_log_message

    seen = []

    async def fake_get_recent_log(limit):
        seen.append(limit)
        return []

    monkeypatch.setattr(store, "get_recent_log", fake_get_recent_log)

    await _build_paper_log_message(999)
    await _build_paper_log_message(0)

    assert seen == [50, 1]
