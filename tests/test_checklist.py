"""Tests for the contribution checklist (Phase 4)."""

from analyzer.checklist import (
    CATEGORY_ORDER,
    build_checklist,
    checklist_key,
    group_by_category,
    progress,
)
from analyzer.file_scanner import analyze_source
from analyzer.github_client import RepoMetadata
from analyzer.issue_client import IssueInfo
from analyzer.issue_linker import IssueLinkResult, LinkedFile


def make_issue(title="Fix a bug", body="It crashes.", labels=None):
    return IssueInfo(
        number=7,
        title=title,
        body=body,
        labels=list(labels or []),
        html_url="https://github.com/acme/demo/issues/7",
        repo_full_name="acme/demo",
    )


def make_meta():
    return RepoMetadata(
        owner="acme",
        name="demo",
        full_name="acme/demo",
        default_branch="main",
        commit_sha="abc123",
        html_url="https://github.com/acme/demo",
    )


def modules_with_tests():
    return {
        "app.py": analyze_source("app.py", "def run():\n    return 1\n"),
        "tests/test_app.py": analyze_source("tests/test_app.py", "def test_x():\n    pass\n"),
    }


def empty_link():
    return IssueLinkResult(repo_full_name="acme/demo", issue_number=7)


def link_with_file():
    return IssueLinkResult(
        repo_full_name="acme/demo",
        issue_number=7,
        linked_files=[
            LinkedFile(
                path="app.py", module_name="app", score=60.0, confidence="confirmed"
            )
        ],
    )


def test_has_all_categories_in_order():
    items = build_checklist(
        make_issue(), link_with_file(), None, make_meta(), "hi", modules_with_tests()
    )
    cats = [i.category for i in items]
    present = [c for c in CATEGORY_ORDER if c in cats]
    assert present == ["understand", "setup", "implement", "verify", "share"]


def test_ids_are_unique():
    items = build_checklist(
        make_issue(), link_with_file(), None, make_meta(), "", modules_with_tests()
    )
    ids = [i.id for i in items]
    assert len(ids) == len(set(ids))


def test_bug_issue_has_reproduction_step():
    items = build_checklist(
        make_issue(title="Crash", labels=["bug"]),
        empty_link(),
        None,
        make_meta(),
        "",
        modules_with_tests(),
    )
    assert any("reproduce" in i.text.lower() for i in items)


def test_docs_issue_has_documentation_steps():
    items = build_checklist(
        make_issue(title="Fix README typo"),
        empty_link(),
        None,
        make_meta(),
        "",
        modules_with_tests(),
    )
    assert any("documentation" in i.text.lower() or "text" in i.text.lower() for i in items)


def test_contributing_readme_adds_step():
    items = build_checklist(
        make_issue(), empty_link(), None, make_meta(), "Please read CONTRIBUTING.md", {}
    )
    assert any("contributing" in i.text.lower() for i in items)


def test_tests_present_adds_verify_test_steps():
    items = build_checklist(
        make_issue(), empty_link(), None, make_meta(), "", modules_with_tests()
    )
    assert any("test suite" in i.text.lower() for i in items)


def test_no_tests_uses_manual_verify():
    modules = {"app.py": analyze_source("app.py", "def run():\n    return 1\n")}
    items = build_checklist(make_issue(), empty_link(), None, make_meta(), "", modules)
    assert any("manually confirm" in i.text.lower() for i in items)


def test_no_files_adds_maintainer_question():
    items = build_checklist(
        make_issue(), empty_link(), None, make_meta(), "", modules_with_tests()
    )
    assert any("maintainer" in i.text.lower() for i in items)


def test_checklist_key_is_stable_and_lowercased():
    assert checklist_key("Acme/Demo", 7) == checklist_key("acme/demo", 7)
    assert checklist_key("acme/demo", 7) != checklist_key("acme/demo", 8)
    assert "#7" in checklist_key("acme/demo", 7)


def test_progress_computation():
    items = build_checklist(
        make_issue(), link_with_file(), None, make_meta(), "", modules_with_tests()
    )
    state = {items[0].id: True, items[1].id: True}
    done, total, frac = progress(items, state)
    assert done == 2
    assert total == len(items)
    assert abs(frac - 2 / len(items)) < 1e-9


def test_progress_empty_list():
    assert progress([], {}) == (0, 0, 0.0)


def test_group_by_category_orders_and_filters():
    items = build_checklist(
        make_issue(), link_with_file(), None, make_meta(), "", modules_with_tests()
    )
    grouped = group_by_category(items)
    assert list(grouped.keys()) == ["understand", "setup", "implement", "verify", "share"]
    assert all(grouped[c] for c in grouped)


def test_to_dict_serialisable():
    import json

    items = build_checklist(
        make_issue(), link_with_file(), None, make_meta(), "", modules_with_tests()
    )
    json.dumps([i.to_dict() for i in items])
