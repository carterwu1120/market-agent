"""Shared LangGraph state definition.

All agents read from and write to this state. LangGraph merges
list fields with the `add` reducer (append semantics).
"""

from typing import Annotated, Any
from dataclasses import dataclass, field
import operator


@dataclass
class AgentState:
    # ── Input ──────────────────────────────────────────────────────
    user_message: str = ""
    user_id: str = ""
    channel_id: str = ""
    conversation_history: list[dict] = field(default_factory=list)

    # ── Routing ────────────────────────────────────────────────────
    intent: str = ""         # "daily_brief" | "stock_query" | "sector_query" | "follow_up" | "unknown"
    target_symbols: list[str] = field(default_factory=list)  # resolved ticker list
    sector_query: str = ""   # e.g. "半導體", "傳產", "油電燃氣業"
    sector_names: list[str] = field(default_factory=list)    # resolved official sector names

    # ── Collected data (each agent appends its results) ────────────
    news_articles: Annotated[list[dict], operator.add] = field(default_factory=list)
    technical_data: Annotated[list[dict], operator.add] = field(default_factory=list)
    fundamental_data: Annotated[list[dict], operator.add] = field(default_factory=list)
    chip_data: Annotated[list[dict], operator.add] = field(default_factory=list)
    social_signals: Annotated[list[dict], operator.add] = field(default_factory=list)
    rag_context: Annotated[list[dict], operator.add] = field(default_factory=list)

    # ── Output ─────────────────────────────────────────────────────
    final_report: str = ""
    sources: Annotated[list[str], operator.add] = field(default_factory=list)  # all cited URLs
    error: str = ""
