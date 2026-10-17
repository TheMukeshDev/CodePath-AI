"""Tests for the shared structured-output helpers (Phase 4)."""

from ai.structured_output import (
    MAX_LIST_ITEMS,
    MAX_TEXT_CHARS,
    clean_list,
    clean_text,
    extract_json_object,
    strip_control,
)


def test_extract_raw_object():
    assert extract_json_object('{"a": 1}') == {"a": 1}


def test_extract_fenced_object():
    text = 'Sure:\n```json\n{"a": 1}\n```\n'
    assert extract_json_object(text) == {"a": 1}


def test_extract_object_embedded_in_prose():
    text = 'Here is the answer {"a": [1, 2], "b": "x"} thanks'
    assert extract_json_object(text) == {"a": [1, 2], "b": "x"}


def test_extract_returns_none_for_non_object():
    assert extract_json_object("[1, 2, 3]") is None
    assert extract_json_object("no json here") is None
    assert extract_json_object(None) is None


def test_extract_handles_braces_inside_strings():
    text = '{"a": "a } brace", "b": 2}'
    assert extract_json_object(text) == {"a": "a } brace", "b": 2}


def test_clean_text_truncates_and_strips_control():
    dirty = "hi\x00there\x07" + "x" * (MAX_TEXT_CHARS + 50)
    cleaned = clean_text(dirty)
    assert "\x00" not in cleaned
    assert "\x07" not in cleaned
    assert cleaned.endswith("…")
    assert len(cleaned) <= MAX_TEXT_CHARS + 1


def test_clean_text_handles_non_strings():
    assert clean_text(12) == "12"
    assert clean_text(None) == ""
    assert clean_text(["a", "b"]) == "a b"


def test_clean_list_bounds_and_dedupes_bad_values():
    values = ["a", None, "b", "a", 3] + ["x"] * 50
    cleaned = clean_list(values)
    assert cleaned[:4] == ["a", "b", "3", "x"]
    assert len(cleaned) <= MAX_LIST_ITEMS


def test_clean_list_wraps_string():
    assert clean_list("solo") == ["solo"]


def test_strip_control_keeps_newlines():
    assert strip_control("a\nb\tc") == "a\nb\tc"
