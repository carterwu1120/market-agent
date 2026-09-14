"""Deterministic first-pass classifier for long-term investment evidence."""

from __future__ import annotations

from typing import Any


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def assess_long_term(fundamental: Any, moat: Any) -> dict[str, Any]:
    """Score evidence quality without pretending that search leads prove a moat."""
    fundamental = fundamental if isinstance(fundamental, dict) else {}
    moat = moat if isinstance(moat, dict) else {}
    evidence = moat.get("evidence", {}) if isinstance(moat.get("evidence"), dict) else {}
    score = 0
    strong_fundamental_count = 0
    positives: list[str] = []
    risks: list[str] = []
    missing: list[str] = []

    checks = (
        ("revenue_growth", 0.10, 15, "營收具雙位數成長"),
        ("roe", 0.15, 15, "ROE 高於 15%"),
        ("gross_margin", 0.20, 10, "毛利率高於 20%"),
    )
    for field, threshold, points, label in checks:
        value = _number(fundamental.get(field))
        if value is None:
            missing.append(field)
        elif value >= threshold:
            score += points
            strong_fundamental_count += 1
            positives.append(label)

    debt = _number(fundamental.get("debt_to_equity"))
    if debt is None:
        missing.append("debt_to_equity")
    elif debt <= 100:
        score += 10
        positives.append("負債比在初步可接受範圍")
    else:
        risks.append("負債權益比偏高")

    pe = _number(fundamental.get("pe_ratio"))
    if pe is None:
        missing.append("pe_ratio")
    elif 0 < pe <= 40:
        score += 10
        positives.append("本益比未超過初步篩選上限")
    elif pe > 40:
        risks.append("本益比偏高，需額外估值安全邊際")

    group_points = {
        "technology": (10, "找到技術／產品證據線索"),
        "commercialization": (15, "找到量產／商業化證據線索"),
        "supply_chain": (10, "找到供應鏈地位證據線索"),
    }
    for group, (points, label) in group_points.items():
        if evidence.get(group):
            score += points
            positives.append(label)
        else:
            missing.append(group)
    if evidence.get("competition_risk"):
        risks.append("存在競爭／替代風險線索，需人工核對")

    score = min(score, 100)
    has_commercial = bool(evidence.get("commercialization"))
    has_supply = bool(evidence.get("supply_chain"))
    strong_fundamentals = strong_fundamental_count >= 2
    if score >= 70 and has_commercial and has_supply and strong_fundamentals:
        classification = "verified_advantage"
    elif score >= 45 and (has_commercial or has_supply):
        classification = "developing"
    elif any(evidence.values()):
        classification = "theme_only"
    else:
        classification = "insufficient_evidence"

    return {
        "score": score,
        "classification": classification,
        "positives": positives,
        "risks": risks,
        "missing_data": sorted(set(missing)),
        "eligible_for_long_term": classification in {"verified_advantage", "developing"},
    }
