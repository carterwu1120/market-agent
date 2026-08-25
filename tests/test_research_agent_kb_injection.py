"""Exercises run_research()'s injection of the personal strategy notes
(data/knowledge_base) into the system prompt. Expected behavior comes from
the documented contract: injection is unconditional -- every run_research()
call gets the notes regardless of tool_names, because they are the user's
own judgment criteria, not optional flavor text a judgment-type call could
silently skip. (An earlier version gated this by tool_names to save token
cost on plain /stock queries; that was reverted because it let a judgment
question like "2330 該不該買？" miss the user's own strategy notes, which
defeats the entire point of having this file.)
"""
import pytest

from src.agents import research_agent
from src.llm import ALL_TOOL_NAMES, USER_FACING_TOOL_NAMES


@pytest.fixture(autouse=True)
def _capture_system_prompt(monkeypatch):
    """llm_research's first positional arg is the fully-assembled system
    prompt -- capture it instead of hitting a real LLM CLI."""
    captured = {}

    async def fake_claude_code_research(system, history, user_message, tool_names):
        captured["system"] = system
        return {"result": "CONCLUSION_SUMMARY: ok END_CONCLUSION", "cost_usd": 0.0}

    monkeypatch.setattr(research_agent, "llm_research", fake_claude_code_research)
    monkeypatch.setattr(
        research_agent, "read_knowledge_base", lambda: "【測試筆記內容】均線黃金交叉"
    )
    return captured


async def test_user_facing_call_gets_knowledge_base(_capture_system_prompt):
    await research_agent.run_research("2330 該不該買？", [], tool_names=USER_FACING_TOOL_NAMES)

    assert "測試筆記內容" in _capture_system_prompt["system"]


async def test_trading_call_gets_knowledge_base(_capture_system_prompt):
    await research_agent.run_research("檢查觀察名單", [], tool_names=ALL_TOOL_NAMES)

    assert "測試筆記內容" in _capture_system_prompt["system"]


async def test_no_notes_available_leaves_system_prompt_unchanged(
    _capture_system_prompt, monkeypatch
):
    monkeypatch.setattr(research_agent, "read_knowledge_base", lambda: "")

    await research_agent.run_research("2330 該不該買？", [], tool_names=USER_FACING_TOOL_NAMES)

    assert "個人策略筆記" not in _capture_system_prompt["system"]
