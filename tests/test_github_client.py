"""Tests for the A-implementation GitHub client (no network access)."""

from unittest.mock import MagicMock, patch

import pytest
import requests as _requests

from analyzer.github_client import (
    DEFAULT_MAX_FILES,
    DEFAULT_MAX_FILE_BYTES,
    FileRetrievalResult,
    GitHubClient,
    GitHubError,
    InvalidRepoUrlError,
    NetworkError,
    RateLimitError,
    RepoMetadata,
    RepoNotFoundError,
    TruncatedTreeError,
    is_excluded_path,
    parse_github_url,
    validate_owner_repo,
)


# --------------------------------------------------------------------------- #
# URL parsing / validation
# --------------------------------------------------------------------------- #
def test_parse_github_url_basic():
    assert parse_github_url("https://github.com/psf/requests") == ("psf", "requests")


def test_parse_github_url_with_git_suffix():
    assert parse_github_url("https://github.com/psf/requests.git") == (
        "psf",
        "requests",
    )


def test_parse_github_url_without_scheme():
    assert parse_github_url("github.com/psf/requests") == ("psf", "requests")


def test_parse_github_url_trailing_slash():
    assert parse_github_url("https://github.com/psf/requests/") == (
        "psf",
        "requests",
    )


def test_parse_github_url_rejects_non_github_host():
    with pytest.raises(InvalidRepoUrlError):
        parse_github_url("https://gitlab.com/owner/repo")


def test_parse_github_url_rejects_extra_path():
    with pytest.raises(InvalidRepoUrlError):
        parse_github_url("https://github.com/owner/repo/tree/main")


def test_parse_github_url_rejects_traversal():
    with pytest.raises(InvalidRepoUrlError):
        parse_github_url("https://github.com/../etc")


def test_parse_github_url_rejects_empty():
    with pytest.raises(InvalidRepoUrlError):
        parse_github_url("")


def test_validate_owner_repo_accepts_normal_pair():
    assert validate_owner_repo("psf", "requests.git") == ("psf", "requests")


def test_validate_owner_repo_rejects_empty():
    with pytest.raises(InvalidRepoUrlError):
        validate_owner_repo("", "requests")


def test_validate_owner_repo_rejects_traversal():
    with pytest.raises(InvalidRepoUrlError):
        validate_owner_repo("..", "requests")


# --------------------------------------------------------------------------- #
# Path exclusion
# --------------------------------------------------------------------------- #
def test_is_excluded_path_flags_vendor_dirs():
    assert is_excluded_path("node_modules/pkg/index.js") is True
    assert is_excluded_path(".venv/lib/site.py") is True
    assert is_excluded_path("src/pkg/mod.py") is False


def test_is_excluded_path_flags_parent_traversal():
    assert is_excluded_path("../secrets.py") is True


# --------------------------------------------------------------------------- #
# HTTP behaviour (mocked)
# --------------------------------------------------------------------------- #
def _response(json_data=None, status=200, headers=None):
    resp = MagicMock()
    resp.status_code = status
    resp.headers = headers or {}
    resp.json.return_value = json_data if json_data is not None else {}
    return resp


def test_get_repository_returns_metadata():
    client = GitHubClient(token="t0ken")
    meta_payload = {
        "full_name": "psf/requests",
        "default_branch": "main",
        "description": "HTTP library",
        "html_url": "https://github.com/psf/requests",
        "stargazers_count": 10,
        "forks_count": 5,
        "open_issues_count": 2,
        "language": "Python",
        "license": {"spdx_id": "Apache-2.0"},
        "topics": ["http"],
    }
    with patch.object(GitHubClient, "_request") as mock_request:
        mock_request.side_effect = [
            _response(meta_payload),
            _response({"sha": "abc123"}),
        ]
        meta = client.get_repository("psf", "requests")

    assert isinstance(meta, RepoMetadata)
    assert meta.owner == "psf"
    assert meta.name == "requests"
    assert meta.default_branch == "main"
    assert meta.commit_sha == "abc123"
    assert meta.stars == 10
    assert meta.license_name == "Apache-2.0"
    assert meta.topics == ["http"]


def test_get_repository_404_raises_not_found():
    client = GitHubClient()
    with patch.object(GitHubClient, "_request", side_effect=RepoNotFoundError("nope")):
        with pytest.raises(RepoNotFoundError):
            client.get_repository("ghost", "repo")


def test_rate_limit_raises_rate_limit_error():
    client = GitHubClient()
    with patch.object(
        GitHubClient,
        "_request",
        side_effect=RateLimitError("limited", reset_at=123),
    ):
        with pytest.raises(RateLimitError):
            client.get_repository("psf", "requests")


def test_readme_decodes_base64_content():
    client = GitHubClient()
    payload = {
        "content": "SGVsbG8gQ29kZVBhdGg=",
        "encoding": "base64",
        "name": "README.md",
    }
    with patch.object(GitHubClient, "_request", return_value=_response(payload)):
        text = client.get_readme("psf", "requests")
    assert text == "Hello CodePath"


def test_file_tree_returns_entries_and_truncation_flag():
    client = GitHubClient()
    payload = {
        "truncated": True,
        "tree": [
            {"path": "a.py", "type": "blob", "sha": "s1", "size": 10},
            {"path": "dir/", "type": "tree", "sha": "s2", "size": 0},
        ],
    }
    with patch.object(GitHubClient, "_request", return_value=_response(payload)):
        entries, truncated = client.get_file_tree("psf", "requests", "main")
    assert truncated is True
    assert entries == [{"path": "a.py", "type": "blob", "sha": "s1", "size": 10}]


def test_fetch_python_files_respects_extension_and_limits():
    client = GitHubClient()
    meta = RepoMetadata(
        owner="psf",
        name="requests",
        full_name="psf/requests",
        default_branch="main",
        commit_sha="abc",
    )
    tree = [
        {"path": "app.py", "type": "blob", "sha": "sha-app", "size": 100},
        {"path": "notes.md", "type": "blob", "sha": "sha-md", "size": 100},
        {"path": "node_modules/x.py", "type": "blob", "sha": "sha-v", "size": 100},
        {"path": "big.py", "type": "blob", "sha": "sha-big", "size": 10**9},
    ]

    with patch.object(GitHubClient, "get_file_tree", return_value=(tree, False)):
        with patch.object(
            GitHubClient,
            "get_file_content",
            return_value=("print('hi')", "utf-8", "ok", 100),
        ):
            result = client.fetch_python_files("psf", "requests", metadata=meta)

    assert isinstance(result, FileRetrievalResult)
    assert [f.path for f in result.ok_files] == ["app.py"]
    reasons = {(s["path"], s["reason"]) for s in result.skipped}
    assert ("notes.md", "file_limit") not in reasons
    assert any(p == "big.py" and r == "too_large" for p, r in reasons)
    assert any(p == "node_modules/x.py" for p, r in reasons) is False
    assert result.file_limit_reached is False


def test_fetch_python_files_file_limit_reported():
    client = GitHubClient()
    meta = RepoMetadata(
        owner="psf",
        name="requests",
        full_name="psf/requests",
        default_branch="main",
        commit_sha="abc",
    )
    tree = [{"path": f"m{i}.py", "type": "blob", "sha": f"s{i}", "size": 10} for i in range(5)]

    with patch.object(GitHubClient, "get_file_tree", return_value=(tree, False)):
        with patch.object(
            GitHubClient,
            "get_file_content",
            return_value=("x = 1", "utf-8", "ok", 5),
        ):
            result = client.fetch_python_files(
                "psf", "requests", max_files=2, metadata=meta
            )

    assert len(result.ok_files) == 2
    assert result.file_limit_reached is True
    assert any(s["reason"] == "file_limit" for s in result.skipped)


def test_defaults_are_exported():
    assert isinstance(DEFAULT_MAX_FILES, int)
    assert isinstance(DEFAULT_MAX_FILE_BYTES, int)
    assert DEFAULT_MAX_FILES > 0
    assert DEFAULT_MAX_FILE_BYTES > 0


def test_errors_share_github_error_base():
    for exc in (
        InvalidRepoUrlError,
        RepoNotFoundError,
        RateLimitError,
        NetworkError,
        TruncatedTreeError,
    ):
        assert issubclass(exc, GitHubError)


# --------------------------------------------------------------------------- #
# Transport retries (bounded) and rate-limit handling
# --------------------------------------------------------------------------- #
def test_request_retries_transient_timeout_then_succeeds():
    session = MagicMock()
    session.request.side_effect = [
        _requests.exceptions.Timeout(),
        _response({"ok": True}),
    ]
    client = GitHubClient(session=session, max_retries=2)
    with patch("analyzer.github_client.time.sleep"):  # no real waiting
        resp = client._request("GET", "/rate_limit")
    assert resp.status_code == 200
    assert session.request.call_count == 2


def test_request_gives_up_after_max_retries():
    session = MagicMock()
    session.request.side_effect = _requests.exceptions.ConnectionError("boom")
    client = GitHubClient(session=session, max_retries=2)
    with patch("analyzer.github_client.time.sleep"):
        with pytest.raises(NetworkError):
            client._request("GET", "/rate_limit")
    assert session.request.call_count == 3  # 1 initial + 2 retries


def test_rate_limit_is_not_retried():
    session = MagicMock()
    session.request.return_value = _response(
        {"message": "rate limited"},
        status=403,
        headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "9999999999"},
    )
    client = GitHubClient(session=session, max_retries=3)
    with pytest.raises(RateLimitError):
        client._request("GET", "/repos/psf/requests")
    assert session.request.call_count == 1  # never retried


def test_http_500_is_not_retried():
    session = MagicMock()
    session.request.return_value = _response({"message": "oops"}, status=500)
    client = GitHubClient(session=session, max_retries=3)
    with pytest.raises(GitHubError):
        client._request("GET", "/repos/psf/requests")
    assert session.request.call_count == 1
