# Drop LangGraph; delegate all orchestration to Claude Code except daily_brief's fixed fan-out

The multi-agent pipeline used LangGraph's `StateGraph` to route between two paths: a fixed
parallel fan-out (`daily_brief`) and a Claude-Code-driven ReAct loop (`react`, everything else —
stock/sector/theme/history queries, comparisons, follow-ups). The `react` path already delegates
its entire tool-selection decision to Claude Code's own agentic loop (`claude_code_research()`
via MCP), so LangGraph's routing/state-merging layer around it was pure overhead — it never
performed any decision-making of its own on that path. We are removing LangGraph, `graph.py`,
`state.py`, and `orchestrator.py` entirely. All non-brief queries go straight to
`claude_code_research()` with the full MCP toolset. `daily_brief` keeps its deterministic parallel
fetch of all data sources as a plain async function (no LLM, no LangGraph) rather than also being
handed to Claude Code, because we confirmed `claude -p` has no `tool_choice`-equivalent mechanism
to force a tool call — an LLM-driven `daily_brief` could silently skip a data source with nobody
present to notice (it runs unattended on a schedule), which conflicts with the project's core
"every number must be sourced, never hallucinated" guarantee.

## Considered Options

- **Rely on system-prompt instructions to make Claude Code call every daily_brief tool.** Rejected
  — confirmed unenforceable (soft guidance only, the model may ignore it), and `daily_brief` runs
  unattended, so a silently incomplete report could go unnoticed for a whole scheduled slot.
- **Use the raw Anthropic Messages API's `tool_choice` param instead of the `claude -p` CLI**, which
  does support forcing tool calls. Rejected to avoid maintaining a second LLM call path/dependency
  alongside the Claude Code CLI backend.

## Consequences

- `graph.py`, `state.py`, `orchestrator.py`, and the LangGraph-node versions of `synthesizer.py`,
  `market_agent.py`, `news_agent.py`, `technical_agent.py`, `fundamental_agent.py`, `chip_agent.py`,
  `social_agent.py`, `rag_agent.py` are removed; their underlying `src/tools/*.py` fetch functions
  are reused directly by the new plain-async `daily_brief` function.
- `langgraph` is dropped from `pyproject.toml`.
- The `intent` field's dead values (`sector_query`, `theme_query`, `history_query`) — leftover from
  an earlier, finer-grained orchestrator classification — go away along with `state.py`; the only
  remaining split is `daily_brief` vs. everything else.
