"""Tests for Phase 3 issue discovery (no network access)."""

from unittest.mock import MagicMock, patch

import pytest

from analyzer.github_client import GitHubClient, RateLimitError, RepoNotFoundError
from analyzer.issue_client import (
    IssueClient,
    IssueInfo,
    build_issue_query,
    normalise_labels,
    parse_search_items,
)


def _api_response(payload):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = payload
    return resp


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #
def test_build_issue_query_default_open_only_issues():
    query = build_issue_query("psf", "requests")
    assert "repo:psf/requests" in query
    assert "is:issue" in query
    assert "is:open" in query


def test_build_issue_query_includes_labels_and_keywords():
    query = build_issue_query(
        "psf", "requests", labels=["good first issue"], keywords="docs"
    )
    assert 'label:"good first issue"' in query
    assert query.endswith("docs")


def test_build_issue_query_rejects_bad_repo():
    from analyzer.github_client import InvalidRepoUrlError

    with pytest.raises(InvalidRepoUrlError):
        build_issue_query("..", "repo")


def test_normalise_labels_handles_objects_and_strings():
    labels = normalise_labels([{"name": "bug"}, "docs", {"name": "bug"}, {"no": "name"}])
    assert labels == ["bug", "docs"]


def test_parse_search_items_skips_pull_requests():
    items = [
        {"number": 1, "title": "Real issue", "user": {"login": "a"}},
        {"number": 2, "title": "A PR", "pull_request": {"url": "x"}},
    ]
    issues, skipped = parse_search_items(items, "psf/requests")
    assert [i.number for i in issues] == [1]
    assert skipped == 1


def test_parse_search_items_drops_malformed_entries():
    items = [
        {"title": "no number"},
        {"number": "not-int", "title": "bad"},
        {"number": 5},
        {"number": 6, "title": "good", "body": "b"},
    ]
    issues, skipped = parse_search_items(items, "psf/requests")
    assert [i.number for i in issues] == [6]
    assert skipped == 0


def test_parse_search_items_bounds_body_length():
    from analyzer.issue_client import MAX_BODY_CHARS

    issues, _ = parse_search_items(
        [{"number": 1, "title": "t", "body": "x" * (MAX_BODY_CHARS + 500)}],
        "psf/requests",
    )
    assert len(issues[0].body) <= MAX_BODY_CHARS + 1


def test_issue_info_flags():
    issue = IssueInfo(number=1, title="Fix docs.py", body="", labels=["Good First Issue"])
    assert issue.is_beginner_friendly is True
    assert issue.has_body is False
    assert issue.path_hint == "docs.py"


# --------------------------------------------------------------------------- #
# Client behaviour (mocked HTTP)
# --------------------------------------------------------------------------- #
def test_search_issues_parses_results():
    payload = {
        "total_count": 2,
        "incomplete_results": False,
        "items": [
            {
                "number": 10,
                "title": "Add docs",
                "body": "please",
                "labels": [{"name": "good first issue"}],
                "state": "open",
                "html_url": "https://github.com/psf/requests/issues/10",
                "user": {"login": "alice"},
                "comments": 0,
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-02-01T00:00:00Z",
            }
        ],
    }
    client = IssueClient(cache=None)
    with patch.object(GitHubClient, "_request", return_value=_api_response(payload)):
        result = client.search_issues("psf", "requests", labels=["good first issue"])

    assert result.total_count == 2
    assert result.issues[0].number == 10
    assert result.issues[0].is_beginner_friendly is True
    assert result.cached is False


def test_search_issues_uses_cache_on_second_call():
    payload = {"total_count": 1, "items": [{"number": 1, "title": "x"}]}
    client = IssueClient()
    with patch.object(GitHubClient, "_request", return_value=_api_response(payload)) as mock:
        first = client.search_issues("psf", "requests")
        second = client.search_issues("psf", "requests")
    assert mock.call_count == 1
    assert first.cached is False
    assert second.cached is True


def test_search_issues_clamps_limit():
    payload = {"total_count": 0, "items": []}
    client = IssueClient()
    with patch.object(GitHubClient, "_request", return_value=_api_response(payload)) as mock:
        client.search_issues("psf", "requests", limit=10_000)
    params = mock.call_args.kwargs["params"]
    assert params["per_page"] <= 100


def test_empty_result_is_not_an_error():
    """Zero open issues must return an empty result, never raise or look like a failure."""
    payload = {"total_count": 0, "incomplete_results": False, "items": []}
    client = IssueClient(cache=None)
    with patch.object(GitHubClient, "_request", return_value=_api_response(payload)):
        result = client.search_issues("noob-mukesh", "mukeshrobot")

    assert result.issues == []
    assert result.total_count == 0
    assert result.is_empty is True
    assert result.cached is False


def test_nonempty_result_is_not_empty():
    payload = {"total_count": 1, "items": [{"number": 1, "title": "x"}]}
    client = IssueClient(cache=None)
    with patch.object(GitHubClient, "_request", return_value=_api_response(payload)):
        result = client.search_issues("psf", "requests")
    assert result.is_empty is False


def test_search_issues_propagates_rate_limit():
    client = IssueClient()
    with patch.object(GitHubClient, "_request", side_effect=RateLimitError("slow down")):
        with pytest.raises(RateLimitError):
            client.search_issues("psf", "requests")


def test_get_issue_returns_single_issue():
    payload = {"number": 7, "title": "Fix", "user": {"login": "bob"}}
    client = IssueClient()
    with patch.object(GitHubClient, "_request", return_value=_api_response(payload)):
        issue = client.get_issue("psf", "requests", 7)
    assert isinstance(issue, IssueInfo)
    assert issue.number == 7


def test_fetch_good_first_issues_targets_beginner_label():
    payload = {"total_count": 0, "items": []}
    client = IssueClient()
    with patch.object(GitHubClient, "_request", return_value=_api_response(payload)) as mock:
        client.fetch_good_first_issues("psf", "requests")
    query = mock.call_args.kwargs["params"]["q"]
    assert 'label:"good first issue"' in query


def test_get_issue_not_found_propagates():
    client = IssueClient()
    with patch.object(GitHubClient, "_request", side_effect=RepoNotFoundError("gone")):
        with pytest.raises(RepoNotFoundError):
            client.get_issue("psf", "requests", 1)
