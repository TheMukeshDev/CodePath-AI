"""Opt-in *live* integration tests against real public GitHub repositories.

These tests hit the real GitHub REST API and are therefore disabled by default
so the normal suite stays hermetic and offline. Enable them explicitly:

    set CODEPATH_LIVE=1
    .venv\\Scripts\\python.exe -m pytest tests/test_live_integration.py -v

An optional ``GITHUB_TOKEN`` / ``GH_TOKEN`` (or a ``.env`` file) raises the
rate limit from 60 to 5,000 requests/hour. The token is never required and
never logged.

No repository code is downloaded *and executed* here: source is only parsed
with :mod:`ast`, exactly as the application does.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from analyzer.code_map import detect_entry_points, resolve_imports
from analyzer.file_scanner import analyze_files
from analyzer.github_client import (
    GitHubClient,
    InvalidRepoUrlError,
    parse_github_url,
)
from analyzer.issue_client import IssueClient

pytestmark = pytest.mark.skipif(
    os.environ.get("CODEPATH_LIVE") != "1",
    reason="live GitHub tests are opt-in; set CODEPATH_LIVE=1 to run",
)

MUKESHROBOT = "https://github.com/noob-mukesh/mukeshrobot"
REQUESTS = "https://github.com/psf/requests"


def _token() -> str:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token and token.strip():
        return token.strip()
    env_path = Path(__file__).resolve().parent.parent / ".env"
    try:
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("GITHUB_TOKEN") and "=" in line:
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


_TOKEN = _token()


@pytest.fixture(scope="module")
def client() -> GitHubClient:
    return GitHubClient(token=_TOKEN or None, timeout=30)


@pytest.mark.parametrize(
    "url,expected",
    [
        (MUKESHROBOT, ("noob-mukesh", "mukeshrobot")),
        (REQUESTS, ("psf", "requests")),
    ],
)
def test_url_parsing(url, expected):
    assert parse_github_url(url) == expected


def test_invalid_url_rejected():
    with pytest.raises(InvalidRepoUrlError):
        parse_github_url("https://gitlab.com/owner/repo")


@pytest.mark.parametrize("url", [MUKESHROBOT, REQUESTS])
def test_live_repository_workflow(client, url):
    owner, repo = parse_github_url(url)

    meta = client.get_repository(owner, repo)
    assert meta.full_name.lower() == f"{owner}/{repo}".lower()
    assert meta.default_branch
    assert meta.html_url

    readme = client.get_readme(owner, repo, ref=meta.default_branch)
    assert isinstance(readme, str)

    tree, truncated = client.get_file_tree(
        owner, repo, meta.commit_sha or meta.default_branch
    )
    assert isinstance(tree, list)
    assert isinstance(truncated, bool)
    assert any(entry["path"].endswith(".py") for entry in tree)

    retrieval = client.fetch_python_files(
        owner, repo, ref=meta.commit_sha or meta.default_branch, max_files=8
    )
    assert retrieval.ok_files, "expected at least one retrievable Python file"

    contents = {f.path: f.content for f in retrieval.ok_files if f.content}
    modules = analyze_files(contents)
    assert modules
    assert any(m.parseable for m in modules.values())

    entry_points = detect_entry_points(modules)
    relations = resolve_imports(modules)
    assert isinstance(entry_points, list)
    assert isinstance(relations, list)


def test_live_missing_repository_is_reported(client):
    from analyzer.github_client import RepoNotFoundError

    with pytest.raises(RepoNotFoundError):
        client.get_repository("noob-mukesh", "definitely-not-a-real-repo-xyz-123")


def test_live_issue_discovery_and_empty_state():
    owner, repo = parse_github_url(MUKESHROBOT)
    issue_client = IssueClient(token=_TOKEN or None, timeout=30)
    result = issue_client.search_issues(owner, repo)
    # A successful search may legitimately return zero open issues; that is an
    # empty state, not a failure. We only assert the contract is well-formed.
    assert result.repo_full_name.lower() == "noob-mukesh/mukeshrobot"
    assert isinstance(result.issues, list)
    assert result.total_count >= len(result.issues)


def test_live_ranks_real_requests_issues():
    owner, repo = parse_github_url(REQUESTS)
    issue_client = IssueClient(token=_TOKEN or None, timeout=30)
    result = issue_client.search_issues(owner, repo, limit=5)
    assert isinstance(result.issues, list)
    for issue in result.issues:
        assert not issue.is_pull_request
        assert issue.number > 0
        assert issue.title
