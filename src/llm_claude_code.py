"""Research agent backend via local Claude Code CLI + MCP tools.

Uses `claude -p --mcp-config ...` so Claude Code drives the ReAct loop
with its own native tool-calling against src/mcp_server.py — no
hand-rolled JSON-action text protocol. Billed against the Claude Code
subscription's included usage rather than a metered API key.

This intentionally does NOT try to make `claude -p` behave like a bare
text-completion API (that was tried and is unreliable — see git log).
Instead it leans into what Claude Code actually is: an agent you hand
a goal and a toolset, and it runs its own loop to a final answer.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from loguru import logger

CLAUDE_BIN = "claude"
DEFAULT_TIMEOUT_SECONDS = 180
MCP_SERVER_NAME = "market-agent-tools"
PROJECT_ROOT = Path(__file__).resolve().parent.parent

ALL_TOOL_NAMES = [
    "sector_lookup", "theme_lookup", "technical_analysis", "fundamental_analysis",
    "chip_analysis", "company_news", "stock_history",
    "discord_message", "discord_dm", "gmail_draft", "gmail_send",
]


class ClaudeCodeError(RuntimeError):
    pass


def _mcp_config() -> dict:
    return {
        "mcpServers": {
            MCP_SERVER_NAME: {
                "command": "uv",
                "args": ["run", "python", "-m", "src.mcp_server"],
                "cwd": str(PROJECT_ROOT),
            }
        }
    }


def _allowed_tools(tool_names: list[str]) -> str:
    return ",".join(f"mcp__{MCP_SERVER_NAME}__{name}" for name in tool_names)


def _render_prompt(system: str, history: list[dict], user_message: str) -> str:
    parts = [system, ""]
    for m in history:
        role = "使用者" if m.get("role") == "user" else "你（先前回覆）"
        parts.append(f"[{role}]\n{m.get('content', '')}")
    parts.append(f"[使用者本輪提問]\n{user_message}")
    return "\n\n".join(parts)


async def claude_code_research(
    system: str,
    history: list[dict],
    user_message: str,
    tool_names: list[str] = ALL_TOOL_NAMES,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> str:
    """Run one Claude Code agentic turn with MCP tools bound.

    Claude Code runs its own internal tool-calling loop (may invoke
    several MCP tools before answering) and returns the final text.
    """
    prompt = _render_prompt(system, history, user_message)
    mcp_config_path = PROJECT_ROOT / ".mcp_market_agent_config.json"
    mcp_config_path.write_text(json.dumps(_mcp_config()))

    cmd = [
        CLAUDE_BIN, "-p", prompt,
        "--output-format", "json",
        "--mcp-config", str(mcp_config_path),
        "--allowedTools", _allowed_tools(tool_names),
    ]

    logger.debug(f"claude_code_research: invoking CLI (prompt_len={len(prompt)})")

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(PROJECT_ROOT),
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise ClaudeCodeError(f"claude CLI timed out after {timeout}s")

    if proc.returncode != 0:
        raise ClaudeCodeError(f"claude CLI exited {proc.returncode}: {stderr.decode(errors='replace')}")

    try:
        payload = json.loads(stdout.decode())
    except json.JSONDecodeError as e:
        raise ClaudeCodeError(f"failed to parse claude CLI output: {e}\nraw: {stdout[:500]!r}")

    if payload.get("is_error"):
        raise ClaudeCodeError(f"claude CLI returned error: {payload.get('result')}")

    denials = payload.get("permission_denials") or []
    if denials:
        logger.warning(f"claude_code_research: {len(denials)} tool permission denials: {denials}")

    logger.debug(
        f"claude_code_research: cost=${payload.get('total_cost_usd', 0):.4f} "
        f"turns={payload.get('num_turns')}"
    )
    return payload.get("result", "")
