import json
from unittest.mock import AsyncMock

import pytest

from src.agents import paper_trading_decision as decision


def test_parse_decisions_accepts_json_fence():
    raw = """```json
    {"decisions":[{"symbol":"2330.TW","action":"hold","reason":"test"}]}
    ```"""
    assert decision.parse_decisions(raw)[0]["symbol"] == "2330.TW"


def test_parse_decisions_rejects_missing_array():
    with pytest.raises(ValueError, match="decisions array"):
        decision.parse_decisions("{}")


@pytest.mark.asyncio
async def test_request_decisions_uses_one_shot_chat_without_tools(monkeypatch):
    llm_chat = AsyncMock(
        return_value={
            "result": json.dumps(
                {"decisions": [{"symbol": "2330.TW", "action": "hold", "reason": "test"}]}
            ),
            "cost_usd": 0.12,
        }
    )
    monkeypatch.setattr(decision, "llm_chat_with_usage", llm_chat)
    monkeypatch.setattr(decision, "read_knowledge_base", lambda: "strategy")

    result = await decision.request_decisions([{"symbol": "2330.TW", "role": "position"}])

    assert result["decisions"][0]["action"] == "hold"
    assert result["cost_usd"] == 0.12
    assert llm_chat.await_count == 1
    assert llm_chat.await_args.kwargs["timeout"] == 120


@pytest.mark.asyncio
async def test_execute_rejects_action_outside_target_role(monkeypatch):
    buy = AsyncMock()
    monkeypatch.setattr(decision, "buy", buy)

    results = await decision.execute_decisions(
        [{"symbol": "2330.TW", "action": "buy", "reason": "test"}],
        [{"symbol": "2330.TW", "role": "position", "conditions": []}],
    )

    assert results[0]["error"] == "invalid position action"
    buy.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_valid_watchlist_buy_uses_guarded_action(monkeypatch):
    buy = AsyncMock(return_value={"success": True, "position_id": 1})
    monkeypatch.setattr(decision, "buy", buy)

    results = await decision.execute_decisions(
        [{
            "symbol": "2330.TW",
            "action": "buy",
            "reason": "breakout",
            "horizon": "short_term",
            "allocation_pct": 5,
        }],
        [{"symbol": "2330.TW", "role": "watchlist", "conditions": []}],
    )

    assert results[0]["success"] is True
    buy.assert_awaited_once_with("2330.TW", "breakout", "short_term", 5.0)
