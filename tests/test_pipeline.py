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

    monkeypatch.setattr(pipeline, "llm_chat", fail_if_called)

    intent = await pipeline.classify_intent("早安，今天市場概況如何？")
    assert intent == "daily_brief"


@pytest.mark.asyncio
async def test_bare_ticker_disqualifies_fast_path_even_with_brief_keyword(monkeypatch):
    async def fake_chat(messages, system):
        return '{"intent": "react", "reasoning": "specific ticker"}'

    monkeypatch.setattr(pipeline, "llm_chat", fake_chat)

    intent = await pipeline.classify_intent("2330 今天早安")
    assert intent == "react"


@pytest.mark.asyncio
async def test_topic_keyword_disqualifies_fast_path_even_with_brief_keyword(monkeypatch):
    calls = []

    async def fake_chat(messages, system):
        calls.append(messages)
        return '{"intent": "react", "reasoning": "sector topic"}'

    monkeypatch.setattr(pipeline, "llm_chat", fake_chat)

    intent = await pipeline.classify_intent("半導體今日總結")
    assert intent == "react"
    assert len(calls) == 1  # confirms it actually went through the LLM path


@pytest.mark.asyncio
async def test_llm_path_returns_daily_brief_when_llm_says_so(monkeypatch):
    async def fake_chat(messages, system):
        return '{"intent": "daily_brief", "reasoning": "broad overview"}'

    monkeypatch.setattr(pipeline, "llm_chat", fake_chat)

    intent = await pipeline.classify_intent("幫我看一下整體投資機會")
    assert intent == "daily_brief"


@pytest.mark.asyncio
async def test_malformed_llm_json_falls_back_to_react(monkeypatch):
    async def fake_chat(messages, system):
        return "not valid json at all"

    monkeypatch.setattr(pipeline, "llm_chat", fake_chat)

    intent = await pipeline.classify_intent("隨便問個問題")
    assert intent == "react"


@pytest.mark.asyncio
async def test_llm_exception_falls_back_to_react(monkeypatch):
    async def fake_chat(messages, system):
        raise RuntimeError("claude CLI crashed")

    monkeypatch.setattr(pipeline, "llm_chat", fake_chat)

    intent = await pipeline.classify_intent("隨便問個問題")
    assert intent == "react"


def test_extract_requested_symbols_includes_tickers_and_company_names():
    assert pipeline.extract_requested_symbols(
        "定期定額2330 and 0050，以及聯發科適不適合買進？"
    ) == ["2330.TW", "0050.TW", "2454.TW"]


@pytest.mark.asyncio
async def test_three_symbols_are_split_and_successful_results_are_combined(monkeypatch):
    prompts = []

    async def fake_research(prompt, history):
        prompts.append(prompt)
        symbol = "2330.TW" if len(prompts) == 1 else "2454.TW"
        return {
            "final_report": f"report {symbol}",
            "conclusion": f"conclusion {symbol}",
            "target_symbols": [symbol],
            "cost_usd": 0.0,
        }

    monkeypatch.setattr(pipeline, "run_research", fake_research)
    result = await pipeline._run_research_batched("比較 2330、0050 與聯發科", [])

    assert len(prompts) == 2
    assert "2330.TW, 0050.TW" in prompts[0]
    assert "2454.TW" in prompts[1]
    assert "report 2330.TW" in result["final_report"]
    assert "report 2454.TW" in result["final_report"]


@pytest.mark.asyncio
async def test_batched_research_keeps_success_when_another_batch_times_out(monkeypatch):
    calls = 0

    async def fake_research(prompt, history):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("codex CLI timed out after 300s; technical_analysis completed")
        return {
            "final_report": "聯發科分析完成",
            "conclusion": "聯發科結論",
            "target_symbols": ["2454.TW"],
            "cost_usd": 0.0,
        }

    monkeypatch.setattr(pipeline, "run_research", fake_research)
    result = await pipeline._run_research_batched("比較 2330、0050 與聯發科", [])

    assert "聯發科分析完成" in result["final_report"]
    assert "2330.TW, 0050.TW" in result["final_report"]
    assert result["target_symbols"] == ["2454.TW"]
    assert result["error"] == "one or more research batches failed"


def test_timeout_is_classified_before_tool_names_in_error_output():
    error = "codex CLI timed out after 300s; technical_analysis completed"
    assert pipeline._infer_failed_stage(error) == "AI 分析逾時"
