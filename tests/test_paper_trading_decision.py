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


async def test_decision_audit_links_inputs_proposal_and_execution(monkeypatch):
    packets = [{"symbol": "2330.TW", "role": "position"}]
    proposal = {"decisions": [{"symbol": "2330.TW", "action": "hold"}], "cost_usd": 0}
    results = [{"symbol": "2330.TW", "action": "hold", "success": True}]
    audit = AsyncMock()
    monkeypatch.setattr(decision, "collect_decision_packets", AsyncMock(return_value=packets))
    monkeypatch.setattr(decision, "request_decisions", AsyncMock(return_value=proposal))
    monkeypatch.setattr(decision, "execute_decisions", AsyncMock(return_value=results))
    monkeypatch.setattr(decision, "read_knowledge_base", lambda: "test strategy")
    monkeypatch.setattr(decision, "log_event", audit)
    await decision.run_paper_decision(packets)
    calls = audit.await_args_list
    assert [call.args[0] for call in calls] == [
        "decision_input", "decision_proposed", "decision_executed",
    ]
    payloads = [json.loads(call.args[1]) for call in calls]
    assert len({payload["id"] for payload in payloads}) == 1
    assert payloads[0]["packets"] == packets
    assert payloads[0]["knowledge"] == "test strategy"
    assert payloads[1]["decisions"] == proposal["decisions"]
    assert payloads[2]["results"] == results


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


@pytest.mark.asyncio
async def test_execute_rejects_buy_without_explicit_horizon(monkeypatch):
    buy = AsyncMock()
    monkeypatch.setattr(decision, "buy", buy)

    results = await decision.execute_decisions(
        [{"symbol": "2330.TW", "action": "buy", "reason": "test", "allocation_pct": 5}],
        [{"symbol": "2330.TW", "role": "watchlist", "conditions": []}],
    )

    assert "horizon" in results[0]["error"]
    buy.assert_not_awaited()


@pytest.mark.asyncio
async def test_watch_long_term_persists_assessment(monkeypatch):
    update = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "src.memory.paper_trading_store.update_watchlist_assessment", update
    )
    target = {
        "symbol": "2330.TW",
        "role": "watchlist",
        "watchlist": {"strategy_horizon": "unclassified"},
        "long_term_assessment": {
            "eligible_for_long_term": True,
            "classification": "developing",
            "score": 62,
        },
        "conditions": [],
    }

    results = await decision.execute_decisions(
        [{"symbol": "2330.TW", "action": "watch_long_term", "reason": "等待估值"}],
        [target],
    )

    assert results[0]["success"] is True
    update.assert_awaited_once_with("2330.TW", "long_term", "developing", 62, "等待估值")


@pytest.mark.asyncio
async def test_watch_long_term_rejects_weak_evidence(monkeypatch):
    update = AsyncMock()
    monkeypatch.setattr(
        "src.memory.paper_trading_store.update_watchlist_assessment", update
    )

    results = await decision.execute_decisions(
        [{"symbol": "2330.TW", "action": "watch_long_term", "reason": "題材"}],
        [{
            "symbol": "2330.TW",
            "role": "watchlist",
            "long_term_assessment": {"eligible_for_long_term": False},
            "conditions": [],
        }],
    )

    assert results[0]["error"] == "long-term evidence threshold not met"
    update.assert_not_awaited()
