"""Expected values below come from extract_conclusion's documented contract
(see src/agents/report_utils.py docstring), not from tracing the regex —
if a case here fails, that's the implementation disagreeing with its own
contract, not a wrong test.
"""
from src.agents.report_utils import extract_conclusion


def test_no_tags_returns_report_unchanged_and_empty_conclusion():
    report = "## 市場摘要\n今天大盤上漲。\n\n## 投資建議\n觀望。"
    cleaned, conclusion = extract_conclusion(report)
    assert cleaned == report
    assert conclusion == ""


def test_tags_present_extracts_conclusion_and_strips_markers():
    report = (
        "## 市場摘要\n今天大盤上漲。\n\n"
        "CONCLUSION_SUMMARY:\n"
        "大盤收紅，建議觀望。\n"
        "END_CONCLUSION"
    )
    cleaned, conclusion = extract_conclusion(report)
    assert conclusion == "大盤收紅，建議觀望。"
    assert "CONCLUSION_SUMMARY" not in cleaned
    assert "END_CONCLUSION" not in cleaned
    assert "今天大盤上漲。" in cleaned


def test_conclusion_spanning_multiple_lines_is_captured_in_full():
    report = (
        "CONCLUSION_SUMMARY:\n"
        "第一句話。\n"
        "第二句話。\n"
        "END_CONCLUSION"
    )
    _, conclusion = extract_conclusion(report)
    assert conclusion == "第一句話。\n第二句話。"


def test_missing_end_tag_is_treated_as_no_tags_present():
    report = "CONCLUSION_SUMMARY:\n這句話沒有結尾標記。"
    cleaned, conclusion = extract_conclusion(report)
    assert cleaned == report
    assert conclusion == ""
