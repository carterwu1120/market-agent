"""_parse_json_objects tolerates >1 JSON object on stdout -- observed live:
claude -p --output-format json occasionally emits a second object after the
expected one for large/long-thinking responses (root cause unconfirmed).
Expected values are picked from json.JSONDecoder's documented raw_decode
contract, not traced from this function's own implementation.
"""
from src.llm_claude_code import _parse_json_objects


def test_single_object_parses_as_before():
    assert _parse_json_objects('{"a": 1}') == [{"a": 1}]


def test_single_object_with_trailing_newline():
    assert _parse_json_objects('{"a": 1}\n') == [{"a": 1}]


def test_two_objects_newline_separated_both_parsed_in_order():
    result = _parse_json_objects('{"a": 1}\n{"b": 2}')
    assert result == [{"a": 1}, {"b": 2}]


def test_two_objects_with_no_separator_still_parsed():
    # raw_decode finds the end of the first value even with no whitespace
    # between objects.
    result = _parse_json_objects('{"a": 1}{"b": 2}')
    assert result == [{"a": 1}, {"b": 2}]


def test_empty_string_returns_empty_list():
    assert _parse_json_objects("") == []


def test_whitespace_only_returns_empty_list():
    assert _parse_json_objects("   \n\n  ") == []
