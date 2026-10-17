"""Tests for the bounded evidence/context builder."""

from __future__ import annotations

from ai.context_builder import (
    build_context_for_question,
    build_evidence_package,
    numbered_snippet,
    select_important_files,
    truncate_text,
)
from analyzer.code_map import detect_entry_points, resolve_imports
from analyzer.file_scanner import analyze_source

SOURCES = {
    "main.py": '''from lib import helper


def main():
    helper()


if __name__ == "__main__":
    main()
''',
    "lib/__init__.py": "from .core import helper\n",
    "lib/core.py": "def helper():\n    return 1\n",
    "lib/extra.py": "def unused():\n    return 2\n",
}

REPO_META = {
    "full_name": "acme/demo",
    "description": "A demo",
    "language": "Python",
    "default_branch": "main",
    "commit_sha": "abc123",
}


def _modules():
    return {path: analyze_source(path, src) for path, src in SOURCES.items()}


def test_truncate_text_respects_budget():
    assert truncate_text("hello", 10) == "hello"
    result = truncate_text("x" * 100, 20)
    assert len(result) <= 20
    assert "truncated" in result


def test_numbered_snippet_has_line_numbers_and_bounds():
    snippet = numbered_snippet("a\nb\nc\nd\ne", 1, max_lines=3)
    assert "   1: a" in snippet
    assert "omitted" in snippet
    # Should only contain 3 numbered lines.
    assert "   4: d" not in snippet


def test_select_important_files_prioritises_entry_points():
    modules = _modules()
    entry_points = detect_entry_points(modules)
    relations = resolve_imports(modules)
    selected = select_important_files(modules, entry_points, relations, limit=2)
    assert selected[0] == "main.py"


def test_build_evidence_package_is_bounded_and_cited():
    modules = _modules()
    entry_points = detect_entry_points(modules)
    relations = resolve_imports(modules)
    contents = dict(SOURCES)
    package = build_evidence_package(
        REPO_META,
        "# Demo\nThis is a demo repo.",
        modules,
        entry_points,
        relations,
        contents,
        max_chars=4000,
        max_snippets=3,
        snippet_lines=20,
    )
    assert len(package["text"]) <= 4000
    assert package["repository"]["full_name"] == "acme/demo"
    assert package["snippets"], "expected at least one snippet"
    # Snippets are line-numbered.
    assert ": " in package["snippets"][0]["text"]
    # Facts text contains paths and line references.
    facts = package["facts_text"]
    assert "main.py" in facts
    assert "line" in facts
    # Entry points are represented with their confidence label.
    confidences = {ep["confidence"] for ep in package["entry_points"]}
    assert "evidence" in confidences


def test_build_context_for_question_bounded():
    modules = _modules()
    relations = resolve_imports(modules)
    entry_points = detect_entry_points(modules)
    package = build_evidence_package(
        REPO_META, "", modules, entry_points, relations, dict(SOURCES), max_chars=1500
    )
    context = build_context_for_question(package, max_chars=1500)
    assert len(context) <= 1500
