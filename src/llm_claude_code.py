"""LLM backend via local Claude Code CLI — no metered API key required.

Two call shapes, matching the two ways this codebase talks to an LLM:

- claude_code_chat: bare system+user prompt -> text. No tools bound, no
  agentic loop. Used for single-shot classification/extraction/synthesis
  (orchestrator intent routing, market_agent hot-stock extraction,
  synthesizer report generation). This is reliable because there's no
  tool protocol for the model to roleplay around.

- claude_code_research: hands Claude Code the src/mcp_server.py MCP
  tools and lets it run its own ReAct loop. Used only where real tool
  use is needed (research_agent). An earlier attempt drove claude -p
  with a hand-rolled JSON-action text protocol instead of MCP — that
  was ~30-40% unreliable because claude -p is a full agent runtime and
  would roleplay fake tool results rather than emit plain JSON. Real
  MCP tool-calling removed that failure mode.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from loguru import logger

CLAUDE_BIN = "claude"
DEFAULT_TIMEOUT_SECONDS = 180
MCP_SERVER_NAME = "market-agent-tools"
PROJECT_ROOT = Path(__file__).resolve().parent.parent

ALL_TOOL_NAMES = [
    "sector_lookup", "theme_lookup", "technical_analysis", "fundamental_analysis",
    "chip_analysis", "company_news", "stock_history",
    "web_search", "company_announcements", "company_financial_summary",
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


def _render_prompt(history: list[dict], user_message: str) -> str:
    parts = []
    for m in history:
        role = "使用者" if m.get("role") == "user" else "你（先前回覆）"
        parts.append(f"[{role}]\n{m.get('content', '')}")
    parts.append(f"[使用者本輪提問]\n{user_message}")
    return "\n\n".join(parts)


async def _run_claude_cli(cmd: list[str], timeout: int, stdin_prompt: str) -> dict:
    """Runs `claude -p` with the prompt piped via stdin rather than argv.

    Windows' CreateProcess has a ~32K character command-line limit; daily_brief's
    prompts (news + several symbols' tables + knowledge base) can exceed that
    once populated. `claude -p` reads the prompt from stdin when no positional
    prompt arg is given (confirmed live: `echo "..." | claude -p` works), so cmd
    must NOT include the prompt itself.
    """
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(PROJECT_ROOT),
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=stdin_prompt.encode()), timeout=timeout
        )
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

    logger.debug(
        f"claude CLI: cost=${payload.get('total_cost_usd', 0):.4f} turns={payload.get('num_turns')}"
    )
    return payload


async def claude_code_chat(
    messages: list[dict],
    system: str = "",
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> str:
    """Single non-agentic `claude -p` call: system+user prompt -> text.

    No tools bound (--tools ""). Drop-in replacement for llm_chat()'s
    call shape — used for classification/extraction/synthesis prompts
    that don't need tool use.
    """
    history = [m for m in messages if m.get("role") in ("user", "assistant")]
    user_message = history[-1]["content"] if history and history[-1]["role"] == "user" else ""
    prior = history[:-1] if user_message else history
    prompt = _render_prompt(prior, user_message) if prior else user_message

    cmd = [CLAUDE_BIN, "-p", "--output-format", "json", "--tools", ""]
    if system:
        cmd += ["--system-prompt", system]

    logger.debug(f"claude_code_chat: invoking CLI (prompt_len={len(prompt)})")
    payload = await _run_claude_cli(cmd, timeout, prompt)
    return payload.get("result", "")


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
    prompt = system + "\n\n" + _render_prompt(history, user_message)
    mcp_config_path = PROJECT_ROOT / ".mcp_market_agent_config.json"
    mcp_config_path.write_text(json.dumps(_mcp_config()))

    cmd = [
        CLAUDE_BIN, "-p",
        "--output-format", "json",
        "--mcp-config", str(mcp_config_path),
        "--allowedTools", _allowed_tools(tool_names),
    ]

    logger.debug(f"claude_code_research: invoking CLI (prompt_len={len(prompt)})")
    payload = await _run_claude_cli(cmd, timeout, prompt)

    denials = payload.get("permission_denials") or []
    if denials:
        logger.warning(f"claude_code_research: {len(denials)} tool permission denials: {denials}")

    return payload.get("result", "")
