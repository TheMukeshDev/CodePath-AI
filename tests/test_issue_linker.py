"""Tests for issue-to-code linking (Phase 4, deterministic, offline)."""

from analyzer.file_scanner import analyze_source
from analyzer.github_client import RepoMetadata
from analyzer.issue_client import IssueInfo
from analyzer.issue_linker import (
    IssueLinkResult,
    link_issue_to_files,
    summarize_link,
)

SOURCES = {
    "requests/api.py": (
        '"""High-level request API."""\n'
        "def get(url):\n"
        "    return _request(url)\n"
        "def _request(url):\n"
        "    return url\n"
    ),
    "requests/sessions.py": (
        "class Session:\n"
        "    def request(self, method):\n"
        "        return method\n"
    ),
    "requests/models.py": "class Response:\n    pass\n",
    "requests/utils.py": (
        '"""Shared helper utilities for encoding and validation."""\n'
        "def encode_params(data):\n"
        "    return data\n"
    ),
}


def make_modules():
    return {path: analyze_source(path, src) for path, src in SOURCES.items()}


def make_issue(title="", body="", number=7, labels=None):
    return IssueInfo(
        number=number,
        title=title,
        body=body,
        labels=list(labels or []),
        html_url=f"https://github.com/acme/demo/issues/{number}",
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
        language="Python",
    )


def test_explicit_path_is_confirmed():
    issue = make_issue(body="The bug is inside requests/api.py on line 3.")
    result = link_issue_to_files(issue, make_modules(), repo_meta=make_meta())
    assert result.confirmed_count == 1
    top = result.linked_files[0]
    assert top.path == "requests/api.py"
    assert top.is_confirmed
    assert any("requests/api.py" in reason for reason in top.reasons)


def test_basename_mention_is_confirmed():
    issue = make_issue(body="Please check sessions.py; the session logic is wrong.")
    result = link_issue_to_files(issue, make_modules(), repo_meta=make_meta())
    paths = [f.path for f in result.linked_files]
    assert "requests/sessions.py" in paths
    linked = next(f for f in result.linked_files if f.path == "requests/sessions.py")
    assert linked.is_confirmed


def test_module_reference_is_confirmed():
    issue = make_issue(body="The `requests.api` module returns the wrong thing.")
    result = link_issue_to_files(issue, make_modules(), repo_meta=make_meta())
    linked = next(
        (f for f in result.linked_files if f.path == "requests/api.py"), None
    )
    assert linked is not None and linked.is_confirmed


def test_class_reference_records_symbol_and_line():
    issue = make_issue(body="The Session class does not pass the method through.")
    result = link_issue_to_files(issue, make_modules(), repo_meta=make_meta())
    linked = next(
        (f for f in result.linked_files if f.path == "requests/sessions.py"), None
    )
    assert linked is not None
    assert linked.is_confirmed
    assert "Session" in linked.line_hints
    assert linked.line_hints["Session"] == 1
    assert any("Session" in s for s in linked.symbols)


def test_function_reference_records_symbol():
    issue = make_issue(body="The function `_request` should validate the url.")
    result = link_issue_to_files(issue, make_modules(), repo_meta=make_meta())
    linked = next(
        (f for f in result.linked_files if f.path == "requests/api.py"), None
    )
    assert linked is not None
    assert linked.is_confirmed
    assert any("_request" in s for s in linked.symbols)


def test_keyword_only_match_is_likely_not_confirmed():
    issue = make_issue(
        title="Encoding and validation helpers are messy",
        body="The encoding utilities need tidying.",
    )
    result = link_issue_to_files(
        issue, make_modules(), repo_meta=make_meta(), min_score=2.0
    )
    linked = next(
        (f for f in result.linked_files if f.path == "requests/utils.py"), None
    )
    assert linked is not None
    assert linked.confidence == "likely"
    assert linked.needs_investigation is True


def test_no_match_flags_investigation():
    issue = make_issue(title="Update the moon landing date", body="Totally unrelated.")
    result = link_issue_to_files(issue, make_modules(), repo_meta=make_meta())
    assert result.needs_investigation is True
    assert result.linked_files == []
    assert result.notes


def test_github_url_uses_commit_sha():
    issue = make_issue(body="Broken in requests/api.py")
    result = link_issue_to_files(issue, make_modules(), repo_meta=make_meta())
    top = result.linked_files[0]
    assert top.github_url is not None
    assert "abc123" in top.github_url
    assert top.github_url.endswith("requests/api.py") or "#L" in top.github_url


def test_no_repo_meta_means_no_url():
    issue = make_issue(body="Broken in requests/api.py")
    result = link_issue_to_files(issue, make_modules(), repo_meta=None)
    assert result.linked_files[0].github_url is None


def test_empty_modules_short_circuits():
    issue = make_issue(body="Anything")
    result = link_issue_to_files(issue, {}, repo_meta=make_meta())
    assert result.needs_investigation is True
    assert result.linked_files == []


def test_unparseable_modules_are_skipped():
    modules = make_modules()
    modules["broken.py"] = analyze_source("broken.py", "def x(:\n")
    issue = make_issue(body="broken.py fails")
    result = link_issue_to_files(issue, modules, repo_meta=make_meta())
    assert all(f.path != "broken.py" for f in result.linked_files)


def test_ordering_is_deterministic():
    issue = make_issue(body="requests/api.py and requests/sessions.py and Session")
    first = link_issue_to_files(issue, make_modules(), repo_meta=make_meta())
    second = link_issue_to_files(issue, make_modules(), repo_meta=make_meta())
    assert [f.path for f in first.linked_files] == [f.path for f in second.linked_files]
    scores = [f.score for f in first.linked_files]
    assert scores == sorted(scores, reverse=True)


def test_max_files_truncates_and_flags():
    sources = {f"pkg/mod{i}.py": "def fn():\n    pass\n" for i in range(20)}
    modules = {p: analyze_source(p, s) for p, s in sources.items()}
    body = " ".join(f"pkg/mod{i}.py" for i in range(20))
    issue = make_issue(body=body)
    result = link_issue_to_files(issue, modules, repo_meta=make_meta(), max_files=5)
    assert len(result.linked_files) == 5
    assert result.truncated is True


def test_summarize_link():
    issue = make_issue(body="Broken in requests/api.py")
    result = link_issue_to_files(issue, make_modules(), repo_meta=make_meta())
    text = summarize_link(result)
    assert "#7" in text


def test_word_boundary_prevents_false_positive():
    issue = make_issue(body="The prerequests handler is odd.")
    result = link_issue_to_files(issue, make_modules(), repo_meta=make_meta())
    linked = next(
        (f for f in result.linked_files if f.path == "requests/api.py"), None
    )
    # "prerequests" must not be treated as a mention of "requests".
    assert linked is None or not linked.is_confirmed
