import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src import llm, llm_codex
from src.llm_codex import CodexError, _codex_command, _mcp_overrides, _run_codex_cli


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


@pytest.mark.asyncio
async def test_llm_chat_with_usage_preserves_claude_cost(monkeypatch):
    monkeypatch.setattr(llm.settings, "llm_backend", "claude")
    payload = {"result": "claude", "cost_usd": 0.25}
    monkeypatch.setattr(llm, "claude_code_chat_with_usage", AsyncMock(return_value=payload))

    assert await llm.llm_chat_with_usage([{"role": "user", "content": "hi"}]) == payload


@pytest.mark.asyncio
async def test_llm_chat_with_usage_marks_codex_cost_unknown_as_zero(monkeypatch):
    monkeypatch.setattr(llm.settings, "llm_backend", "codex")
    monkeypatch.setattr(llm, "codex_chat", AsyncMock(return_value="codex"))

    result = await llm.llm_chat_with_usage([{"role": "user", "content": "hi"}])

    assert result == {"result": "codex", "cost_usd": 0.0}


def test_codex_timeout_summary_is_bounded_and_keeps_latest_activity():
    detail = "\n".join(f"tool call {index}" for index in range(100))
    summary = llm_codex._summarize_cli_output(detail, max_chars=80)

    assert len(summary) <= 80
    assert "tool call 99" in summary
    assert "tool call 0" not in summary


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


def test_codex_command_uses_windows_cmd_for_npm_shim(monkeypatch):
    monkeypatch.setattr(llm_codex.shutil, "which", lambda _: r"C:\npm\codex.CMD")
    monkeypatch.setattr(llm_codex, "_native_codex_from_npm_shim", lambda _: None)
    monkeypatch.setattr(llm_codex.sys, "platform", "win32")
    monkeypatch.setenv("COMSPEC", r"C:\Windows\System32\cmd.exe")

    command = _codex_command(["exec", "--version"])

    assert command[:4] == [
        r"C:\Windows\System32\cmd.exe",
        "/d",
        "/s",
        "/c",
    ]
    assert "codex.CMD" in command[4]
    assert "--version" in command[4]


def test_codex_command_prefers_native_executable_behind_npm_shim(monkeypatch):
    native = Path(r"C:\npm\node_modules\@openai\codex\vendor\bin\codex.exe")
    monkeypatch.setattr(llm_codex.shutil, "which", lambda _: r"C:\npm\codex.CMD")
    monkeypatch.setattr(llm_codex.sys, "platform", "win32")
    monkeypatch.setattr(llm_codex, "_native_codex_from_npm_shim", lambda _: native)

    assert _codex_command(["--version"]) == [str(native), "--version"]


def test_codex_command_reports_missing_cli(monkeypatch):
    monkeypatch.setattr(llm_codex.shutil, "which", lambda _: None)

    with pytest.raises(CodexError, match="not found on PATH"):
        _codex_command(["exec"])


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
