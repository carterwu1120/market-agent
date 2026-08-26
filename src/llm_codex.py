"""LLM backend via the locally authenticated Codex CLI."""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

from loguru import logger

from src.config import settings
from src.llm_claude_code import (
    DEFAULT_TIMEOUT_SECONDS,
    PROJECT_ROOT,
    USER_FACING_TOOL_NAMES,
    _render_prompt,
)

CODEX_BIN = "codex"
# Dotted CLI config overrides cannot address a TOML key containing hyphens.
# Keep the Codex-side server name identifier-safe; the Claude backend may use
# its own hyphenated name because it writes a JSON config file instead.
MCP_SERVER_NAME = "market_agent_tools"


class CodexError(RuntimeError):
    pass


def _mcp_overrides(tool_names: list[str]) -> list[str]:
    """Return CLI config overrides for the project's stdio MCP server."""
    allowed = ",".join(tool_names)
    python_executable = str(Path(sys.executable).resolve())
    return [
        f'mcp_servers.{MCP_SERVER_NAME}.command={json.dumps(python_executable)}',
        f'mcp_servers.{MCP_SERVER_NAME}.args=["-m","src.mcp_server"]',
        f'mcp_servers.{MCP_SERVER_NAME}.cwd={json.dumps(str(PROJECT_ROOT))}',
        (
            f'mcp_servers.{MCP_SERVER_NAME}.env='
            f'{{MARKET_AGENT_MCP_ALLOWED_TOOLS={json.dumps(allowed)}}}'
        ),
        f"mcp_servers.{MCP_SERVER_NAME}.startup_timeout_sec=30",
        f"mcp_servers.{MCP_SERVER_NAME}.required=true",
    ]


async def _run_codex_cli(
    prompt: str,
    timeout: int,
    *,
    tool_names: list[str] | None = None,
) -> str:
    """Run ``codex exec`` and return its final assistant message."""
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as output_file:
        output_path = Path(output_file.name)

    cmd = [
        CODEX_BIN,
        "exec",
        "-",
        "--ephemeral",
        "--color",
        "never",
        "--approve-for-me",
        "--output-last-message",
        str(output_path),
        "--cd",
        str(PROJECT_ROOT),
    ]
    if settings.codex_model:
        cmd += ["--model", settings.codex_model]
    cmd += ["-c", f'model_reasoning_effort="{settings.codex_reasoning_effort}"']
    if tool_names is not None:
        for override in _mcp_overrides(tool_names):
            cmd += ["-c", override]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(PROJECT_ROOT),
        )
        try:
            communicate_task = asyncio.create_task(proc.communicate(prompt.encode("utf-8")))
            stdout, stderr = await asyncio.wait_for(
                asyncio.shield(communicate_task), timeout=timeout
            )
        except TimeoutError:
            proc.kill()
            stdout, stderr = await communicate_task
            detail = stderr.decode(errors="replace") or stdout.decode(errors="replace")
            detail = detail.strip()
            if detail:
                logger.warning(f"codex CLI timeout output: {detail[-4000:]}")
            suffix = f"; last output: {detail[-2000:]}" if detail else ""
            raise CodexError(f"codex CLI timed out after {timeout}s{suffix}")

        if proc.returncode != 0:
            detail = stderr.decode(errors="replace") or stdout.decode(errors="replace")
            raise CodexError(f"codex CLI exited {proc.returncode}: {detail[-2000:]}")

        stderr_text = stderr.decode(errors="replace").strip()
        if stderr_text:
            logger.debug(f"codex CLI stderr: {stderr_text[-4000:]}")

        result = output_path.read_text(encoding="utf-8").strip()
        if not result:
            raise CodexError("codex CLI returned an empty final message")
        return result
    finally:
        output_path.unlink(missing_ok=True)


async def codex_chat(
    messages: list[dict],
    system: str = "",
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> str:
    history = [m for m in messages if m.get("role") in ("user", "assistant")]
    user_message = history[-1]["content"] if history and history[-1]["role"] == "user" else ""
    prior = history[:-1] if user_message else history
    rendered = _render_prompt(prior, user_message) if prior else user_message
    prompt = (
        "Do not inspect files or call tools. Answer only from the supplied "
        "instructions and input.\n\n"
        f"{system}\n\n{rendered}"
    )
    logger.debug(f"codex_chat: invoking CLI (prompt_len={len(prompt)})")
    return await _run_codex_cli(prompt, timeout)


async def codex_research(
    system: str,
    history: list[dict],
    user_message: str,
    tool_names: list[str] = USER_FACING_TOOL_NAMES,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict:
    allowed = ", ".join(tool_names)
    prompt = (
        f"{system}\n\n{_render_prompt(history, user_message)}\n\n"
        f"Use only these tools from the {MCP_SERVER_NAME} MCP server: {allowed}. "
        "Do not use shell commands or edit files."
    )
    logger.debug(f"codex_research: invoking CLI (prompt_len={len(prompt)})")
    effective_timeout = max(timeout, settings.codex_research_timeout_seconds)
    result = await _run_codex_cli(prompt, effective_timeout, tool_names=tool_names)
    logger.warning(
        "Codex CLI does not report total_cost_usd; paper-trading USD budget accounting "
        "cannot include this call"
    )
    return {"result": result, "cost_usd": 0.0}
