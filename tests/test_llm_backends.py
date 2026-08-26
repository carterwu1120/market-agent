import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src import llm, llm_codex
from src.llm_codex import CodexError, _mcp_overrides, _run_codex_cli


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
    expected_python = json.dumps(str(Path(sys.executable).resolve()))
    assert any(f"command={expected_python}" in item for item in overrides)
    assert any("src.mcp_server" in item for item in overrides)
    assert any("MARKET_AGENT_MCP_ALLOWED_TOOLS" in item for item in overrides)
    assert any("technical_analysis" in item for item in overrides)
    assert any("startup_timeout_sec=30" in item for item in overrides)
    assert any("required=true" in item for item in overrides)
    assert not any('command="uv"' in item for item in overrides)


@pytest.mark.asyncio
async def test_codex_timeout_preserves_last_cli_output(monkeypatch):
    class FakeProcess:
        def __init__(self):
            self.returncode = None
            self.killed = asyncio.Event()

        async def communicate(self, _input):
            await self.killed.wait()
            return b"", b"MCP startup failed: useful detail"

        def kill(self):
            self.returncode = -1
            self.killed.set()

    monkeypatch.setattr(
        llm_codex.asyncio,
        "create_subprocess_exec",
        AsyncMock(return_value=FakeProcess()),
    )

    with pytest.raises(CodexError, match="MCP startup failed: useful detail"):
        await _run_codex_cli("prompt", timeout=0.001)


@pytest.mark.asyncio
async def test_codex_research_uses_configured_longer_timeout(monkeypatch):
    run_cli = AsyncMock(return_value="done")
    monkeypatch.setattr(llm_codex, "_run_codex_cli", run_cli)
    monkeypatch.setattr(llm_codex.settings, "codex_research_timeout_seconds", 300)

    result = await llm_codex.codex_research("system", [], "message", timeout=180)

    assert result["result"] == "done"
    assert run_cli.await_args.args[1] == 300
