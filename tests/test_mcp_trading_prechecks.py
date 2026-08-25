from unittest.mock import AsyncMock

import pytest

from src import mcp_server


@pytest.fixture(autouse=True)
def _clear_completed_checks():
    mcp_server._completed_research_checks.clear()
    yield
    mcp_server._completed_research_checks.clear()


async def test_buy_is_rejected_when_required_research_is_missing(monkeypatch):
    buy = AsyncMock()
    monkeypatch.setattr(mcp_server.paper_trading_actions, "buy", buy)

    result = await mcp_server.paper_trade_buy("2330", "測試")

    assert "拒絕買入 2330.TW" in result
    assert "technical_analysis" in result
    assert "fundamental_analysis" in result
    assert "chip_analysis" in result
    assert "company_announcements" in result
    buy.assert_not_awaited()


async def test_buy_succeeds_after_all_checks_and_consumes_them(monkeypatch):
    mcp_server._completed_research_checks["2330.TW"] = set(
        mcp_server._REQUIRED_BUY_CHECKS
    )
    buy = AsyncMock(
        return_value={
            "symbol": "2330.TW",
            "price": 1000.0,
            "shares": 1,
            "allocation_amount": 1000.0,
            "position_id": 7,
        }
    )
    monkeypatch.setattr(mcp_server.paper_trading_actions, "buy", buy)

    result = await mcp_server.paper_trade_buy("2330", "研究完成")

    assert "2330.TW" in result
    buy.assert_awaited_once_with("2330.TW", "研究完成", "short_term", 10.0)
    assert "2330.TW" not in mcp_server._completed_research_checks


def test_paper_trading_tool_set_excludes_unrelated_messaging_tools():
    from src.llm import PAPER_TRADING_TOOL_NAMES

    assert {"sector_lookup", "theme_lookup"}.issubset(PAPER_TRADING_TOOL_NAMES)
    assert mcp_server._REQUIRED_BUY_CHECKS.issubset(PAPER_TRADING_TOOL_NAMES)
    assert "paper_trade_buy" in PAPER_TRADING_TOOL_NAMES
    assert "discord_message" not in PAPER_TRADING_TOOL_NAMES
    assert "gmail_send" not in PAPER_TRADING_TOOL_NAMES
