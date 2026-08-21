"""Expected outcomes below are derived from classify_intent's own routing
rule (see src/agents/pipeline.py): the fast path fires only when a message
has a _BRIEF_KEYWORDS hit AND no bare 4-digit ticker AND no _TOPIC_KEYWORDS
hit; everything else falls through to the LLM. Cases are picked to probe
that boundary, not copied from a prior run's output.
"""
import pytest

from src.agents import pipeline


@pytest.mark.asyncio
async def test_brief_keyword_alone_takes_fast_path_without_calling_llm(monkeypatch):
    async def fail_if_called(*args, **kwargs):
        raise AssertionError("fast path should not call the LLM")

    monkeypatch.setattr(pipeline, "claude_code_chat", fail_if_called)

    intent = await pipeline.classify_intent("早安，今天市場概況如何？")
    assert intent == "daily_brief"


@pytest.mark.asyncio
async def test_bare_ticker_disqualifies_fast_path_even_with_brief_keyword(monkeypatch):
    async def fake_chat(messages, system):
        return '{"intent": "react", "reasoning": "specific ticker"}'

    monkeypatch.setattr(pipeline, "claude_code_chat", fake_chat)

    intent = await pipeline.classify_intent("2330 今天早安")
    assert intent == "react"


@pytest.mark.asyncio
async def test_topic_keyword_disqualifies_fast_path_even_with_brief_keyword(monkeypatch):
    calls = []

    async def fake_chat(messages, system):
        calls.append(messages)
        return '{"intent": "react", "reasoning": "sector topic"}'

    monkeypatch.setattr(pipeline, "claude_code_chat", fake_chat)

    intent = await pipeline.classify_intent("半導體今日總結")
    assert intent == "react"
    assert len(calls) == 1  # confirms it actually went through the LLM path


@pytest.mark.asyncio
async def test_llm_path_returns_daily_brief_when_llm_says_so(monkeypatch):
    async def fake_chat(messages, system):
        return '{"intent": "daily_brief", "reasoning": "broad overview"}'

    monkeypatch.setattr(pipeline, "claude_code_chat", fake_chat)

    intent = await pipeline.classify_intent("幫我看一下整體投資機會")
    assert intent == "daily_brief"


@pytest.mark.asyncio
async def test_malformed_llm_json_falls_back_to_react(monkeypatch):
    async def fake_chat(messages, system):
        return "not valid json at all"

    monkeypatch.setattr(pipeline, "claude_code_chat", fake_chat)

    intent = await pipeline.classify_intent("隨便問個問題")
    assert intent == "react"


@pytest.mark.asyncio
async def test_llm_exception_falls_back_to_react(monkeypatch):
    async def fake_chat(messages, system):
        raise RuntimeError("claude CLI crashed")

    monkeypatch.setattr(pipeline, "claude_code_chat", fake_chat)

    intent = await pipeline.classify_intent("隨便問個問題")
    assert intent == "react"
