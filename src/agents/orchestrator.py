"""Orchestrator Agent — routes incoming user messages to the right sub-agents.

Uses LLM to classify intent and extract ticker symbols from natural language.
"""

import json
import re
from loguru import logger

from src.agents.state import AgentState
from src.llm import llm_chat

# Known Taiwan ticker lookup (expandable)
TW_COMPANY_TO_CODE: dict[str, str] = {
    "台積電": "2330.TW", "tsmc": "2330.TW",
    "聯發科": "2454.TW", "mediatek": "2454.TW",
    "鴻海": "2317.TW", "foxconn": "2317.TW",
    "台達電": "2308.TW",
    "廣達": "2382.TW",
    "緯創": "3231.TW",
    "日月光": "3711.TW",
    "聯電": "2303.TW", "umc": "2303.TW",
    "中華電": "2412.TW",
    "富邦金": "2881.TW",
    "國泰金": "2882.TW",
    "玉山金": "2884.TW",
}

INTENT_PROMPT = """You are a financial assistant router. Analyze the user message and return JSON.

Intents:
- "daily_brief": User wants today's market summary or investment recommendations
- "stock_query": User is asking about specific stock(s)
- "follow_up": User is asking for more details on the previous response
- "unknown": Cannot determine

Extract Taiwan stock codes from company names using your knowledge.

Return ONLY valid JSON:
{{"intent": "<intent>", "symbols": ["2330.TW", ...], "reasoning": "brief reason"}}

User message: {message}
"""


async def orchestrator_node(state: AgentState) -> dict:
    """LangGraph node: classify intent and extract symbols."""
    msg = state.user_message
    logger.info(f"Orchestrator: processing message '{msg[:80]}'")

    # Quick lookup for bare ticker codes like "2330" or "2330.TW"
    bare_codes = re.findall(r"\b(\d{4})(?:\.TW)?\b", msg)
    pre_symbols = [f"{c}.TW" for c in bare_codes]

    # LLM-based intent classification
    try:
        raw = await llm_chat(
            messages=[{"role": "user", "content": INTENT_PROMPT.format(message=msg)}],
            temperature=0.1,
        )
        # Strip markdown fences if present
        raw = re.sub(r"```(?:json)?|```", "", raw).strip()
        parsed = json.loads(raw)
        intent = parsed.get("intent", "unknown")
        llm_symbols = parsed.get("symbols", [])
    except Exception as exc:
        logger.warning(f"Orchestrator LLM call failed: {exc}")
        intent = "daily_brief" if not pre_symbols else "stock_query"
        llm_symbols = []

    # Also check company name lookup
    name_symbols = [
        code for name, code in TW_COMPANY_TO_CODE.items()
        if name.lower() in msg.lower()
    ]

    all_symbols = list(dict.fromkeys(pre_symbols + llm_symbols + name_symbols))

    if all_symbols and intent == "daily_brief":
        intent = "stock_query"

    logger.info(f"Orchestrator → intent={intent}, symbols={all_symbols}")
    return {"intent": intent, "target_symbols": all_symbols}
