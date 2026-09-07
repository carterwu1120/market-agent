"""Deterministic data collection plus one-shot paper-trading decisions.

Background trading deliberately does not use the open-ended ReAct/MCP loop.
Python collects a bounded, auditable packet; the LLM returns proposed JSON;
the existing business-rule functions validate and execute each proposal.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from loguru import logger

from src.llm import llm_chat_with_usage
from src.tools.chip_data import get_institutional_trading
from src.tools.knowledge_base import read_knowledge_base
from src.tools.mops_data import get_material_info_batch
from src.tools.news_fetcher import _TICKER_NAMES, NewsArticle, fetch_targeted_news
from src.tools.paper_trading_actions import (
    buy,
    cancel_watch_condition,
    drop_watchlist,
    sell,
    set_condition,
)
from src.tools.stock_data import get_fundamental_data, get_technical_indicators

DECISION_SYSTEM = """你是台股紙上交易決策器。
系統已提供本輪完整資料；禁止呼叫工具、補造數字或分析範圍外股票。

只回傳合法 JSON，不要 Markdown：
{"decisions":[{"symbol":"2330.TW","action":"hold","reason":"...","horizon":"short_term","allocation_pct":10,"condition":null,"condition_id":null}]}

action 只能是 buy、sell、hold、set_condition、cancel_condition、drop_watchlist。
- watchlist 標的只能 buy、set_condition、drop_watchlist；不可只說繼續觀察。
- position 標的只能 sell、hold、set_condition、cancel_condition。
- buy 必須提供 horizon 與 allocation_pct。
- sell 的 reason 要說明出場原因。
- set_condition 的 condition 必須包含 indicator、operator、threshold、action。
  買入條件可附 horizon/allocation_pct，賣出條件可附 exit_reason。
- cancel_condition 必須提供 condition_id，且只能取消資料包中存在的有效條件。
- 資料不足時：watchlist 用 drop_watchlist；position 用 hold。不要猜測。
"""

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*|```", re.IGNORECASE)
_VALID_ACTIONS = {
    "buy", "sell", "hold", "set_condition", "cancel_condition", "drop_watchlist",
}


def _article_summary(article: NewsArticle) -> dict[str, str]:
    return {
        "title": article.title,
        "published_at": article.published_at.isoformat(),
        "source": article.source_name,
        "url": article.source_url,
    }


def _news_for_symbol(symbol: str, articles: list[NewsArticle]) -> list[dict[str, str]]:
    code = symbol.split(".", 1)[0]
    terms = [code, *_TICKER_NAMES.get(code, [])]
    matched = [
        article for article in articles
        if any(term.lower() in f"{article.title} {article.content}".lower() for term in terms)
    ]
    return [_article_summary(article) for article in matched[:5]]


async def collect_decision_packets(targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fetch all fixed data sources concurrently for at most two targets."""
    targets = targets[:2]
    symbols = [target["symbol"] for target in targets]
    technical_task = asyncio.gather(
        *(get_technical_indicators(symbol) for symbol in symbols),
        return_exceptions=True,
    )
    fundamental_task = asyncio.gather(
        *(get_fundamental_data(symbol) for symbol in symbols),
        return_exceptions=True,
    )
    chip_task = asyncio.gather(
        *(get_institutional_trading(symbol) for symbol in symbols),
        return_exceptions=True,
    )
    technical, fundamental, chip, announcements, news = await asyncio.gather(
        technical_task,
        fundamental_task,
        chip_task,
        get_material_info_batch(symbols),
        fetch_targeted_news(symbols),
        return_exceptions=True,
    )
    technical = technical if not isinstance(technical, Exception) else [technical] * len(symbols)
    fundamental = (
        fundamental if not isinstance(fundamental, Exception) else [fundamental] * len(symbols)
    )
    chip = chip if not isinstance(chip, Exception) else [chip] * len(symbols)
    announcements = announcements if isinstance(announcements, dict) else {}
    news = news if isinstance(news, list) else []

    packets = []
    for index, target in enumerate(targets):
        packet = dict(target)
        packet.update(
            {
                "technical": _safe_result(technical[index]),
                "fundamental": _safe_result(fundamental[index]),
                "chip": _safe_result(chip[index]),
                "announcements": announcements.get(target["symbol"], []),
                "news": _news_for_symbol(target["symbol"], news),
            }
        )
        packets.append(packet)
    return packets


def _safe_result(value: Any) -> Any:
    if isinstance(value, Exception):
        return {"error": str(value)}
    return value


def parse_decisions(raw: str) -> list[dict[str, Any]]:
    cleaned = _JSON_FENCE_RE.sub("", raw).strip()
    if not cleaned.startswith("{"):
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start >= 0 and end > start:
            cleaned = cleaned[start:end + 1]
    payload = json.loads(cleaned)
    decisions = payload.get("decisions")
    if not isinstance(decisions, list):
        raise ValueError("decision response must contain a decisions array")
    return [decision for decision in decisions if isinstance(decision, dict)]


async def request_decisions(packets: list[dict[str, Any]]) -> dict[str, Any]:
    knowledge = read_knowledge_base()
    prompt = (
        "請依資料包做本輪紙上交易決策。\n\n"
        f"使用者策略筆記：\n{knowledge or '（無）'}\n\n"
        "本輪資料包：\n"
        + json.dumps(packets, ensure_ascii=False, default=str)
    )
    payload = await llm_chat_with_usage(
        messages=[{"role": "user", "content": prompt}],
        system=DECISION_SYSTEM,
        timeout=120,
    )
    return {
        "decisions": parse_decisions(payload["result"]),
        "cost_usd": float(payload.get("cost_usd", 0.0)),
    }


async def execute_decisions(
    decisions: list[dict[str, Any]], targets: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Validate decision scope/role, then delegate to existing guarded actions."""
    target_map = {target["symbol"]: target for target in targets}
    results = []
    handled_symbols: set[str] = set()
    for decision in decisions:
        symbol = str(decision.get("symbol", "")).upper()
        action = decision.get("action")
        reason = str(decision.get("reason", "")).strip() or "LLM 未提供理由"
        target = target_map.get(symbol)
        if target is None or action not in _VALID_ACTIONS or symbol in handled_symbols:
            results.append({"symbol": symbol, "action": action, "error": "invalid scope/action"})
            continue
        handled_symbols.add(symbol)

        role = target["role"]
        if role == "watchlist" and action not in {"buy", "set_condition", "drop_watchlist"}:
            results.append(
                {"symbol": symbol, "action": action, "error": "invalid watchlist action"}
            )
            continue
        if role == "position" and action not in {
            "sell", "hold", "set_condition", "cancel_condition",
        }:
            results.append({"symbol": symbol, "action": action, "error": "invalid position action"})
            continue

        try:
            if action == "buy":
                result = await buy(
                    symbol,
                    reason,
                    str(decision.get("horizon") or "short_term"),
                    float(decision.get("allocation_pct") or 10.0),
                )
            elif action == "sell":
                result = await sell(symbol, reason, "llm_signal")
            elif action == "drop_watchlist":
                result = await drop_watchlist(symbol, reason)
            elif action == "set_condition":
                condition = decision.get("condition") or {}
                result = await set_condition(
                    symbol=symbol,
                    indicator=str(condition.get("indicator", "")),
                    operator=str(condition.get("operator", "")),
                    threshold=float(condition.get("threshold", 0)),
                    action=str(condition.get("action", "")),
                    reason=reason,
                    horizon=str(
                        condition.get("horizon")
                        or decision.get("horizon")
                        or "short_term"
                    ),
                    allocation_pct=float(
                        condition.get("allocation_pct")
                        or decision.get("allocation_pct")
                        or 10.0
                    ),
                    exit_reason=str(condition.get("exit_reason") or "llm_signal"),
                )
            elif action == "cancel_condition":
                condition_id = int(decision.get("condition_id") or 0)
                allowed_ids = {int(item["id"]) for item in target.get("conditions", [])}
                result = (
                    await cancel_watch_condition(condition_id)
                    if condition_id in allowed_ids
                    else {"error": "condition_id is outside this target packet"}
                )
            else:
                result = {"success": True, "held": True}
        except (TypeError, ValueError) as exc:
            result = {"error": f"invalid decision parameters: {exc}"}

        results.append({"symbol": symbol, "action": action, **result})
    for symbol in target_map.keys() - handled_symbols:
        results.append({"symbol": symbol, "action": None, "error": "missing decision"})
    return results


async def run_paper_decision(targets: list[dict[str, Any]]) -> dict[str, Any]:
    packets = await collect_decision_packets(targets)
    decision_payload = await request_decisions(packets)
    decisions = decision_payload["decisions"]
    results = await execute_decisions(decisions, targets)
    logger.info("paper decision completed: targets={} decisions={}", len(targets), len(results))
    return {
        "packets": packets,
        "decisions": decisions,
        "results": results,
        "cost_usd": decision_payload["cost_usd"],
    }
