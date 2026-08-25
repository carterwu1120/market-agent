import pytest

from src import llm
from src.llm_codex import _mcp_overrides


@pytest.mark.asyncio
async def test_llm_chat_routes_to_codex(monkeypatch):
    monkeypatch.setattr(llm.settings, "llm_backend", "codex")

    async def fake_codex(messages, system, timeout):
        return "codex"

    monkeypatch.setattr(llm, "codex_chat", fake_codex)
    assert await llm.llm_chat([{"role": "user", "content": "hi"}]) == "codex"


@pytest.mark.asyncio
async def test_llm_chat_routes_to_claude_by_default(monkeypatch):
    monkeypatch.setattr(llm.settings, "llm_backend", "claude")

    async def fake_claude(messages, system, timeout):
        return "claude"

    monkeypatch.setattr(llm, "claude_code_chat", fake_claude)
    assert await llm.llm_chat([{"role": "user", "content": "hi"}]) == "claude"


def test_codex_mcp_overrides_start_project_server():
    overrides = _mcp_overrides(["technical_analysis"])
    assert any('command="uv"' in item for item in overrides)
    assert any("src.mcp_server" in item for item in overrides)
    assert any("MARKET_AGENT_MCP_ALLOWED_TOOLS" in item for item in overrides)
    assert any("technical_analysis" in item for item in overrides)
