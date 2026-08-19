"""Shared helper for parsing the CONCLUSION_SUMMARY/END_CONCLUSION convention
both daily_brief's synthesizer and react's research_agent ask the LLM to emit.
"""

from __future__ import annotations

import re

_CONCLUSION_RE = re.compile(r"CONCLUSION_SUMMARY:\s*(.*?)\s*END_CONCLUSION", re.DOTALL)


def extract_conclusion(report: str) -> tuple[str, str]:
    """Strip the CONCLUSION_SUMMARY tags out of a report and return the summary.

    Returns (cleaned_report, conclusion). If the tags aren't present,
    returns (report, "") unchanged.
    """
    match = _CONCLUSION_RE.search(report)
    if not match:
        return report, ""
    conclusion = match.group(1).strip()
    cleaned = re.sub(r"CONCLUSION_SUMMARY:\s*", "", report)
    cleaned = re.sub(r"\s*END_CONCLUSION", "", cleaned)
    return cleaned, conclusion
