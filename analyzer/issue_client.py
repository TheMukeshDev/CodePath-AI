"""GitHub issue discovery for CodePath AI (Phase 3).

This module only *reads* open issues through the GitHub Search API. It never
comments, reacts, or modifies anything, and it never executes repository code.

Security posture
----------------
* Issue titles, bodies, labels, and authors are **untrusted data**. They are
  stored verbatim (bounded in length) and are never interpreted as instructions
  or as code.
* Everything is bounded: page size, issue count, and body/title length.
* All HTTP reuses :class:`analyzer.github_client.GitHubClient`, so token
  handling, timeouts, path validation, and typed rate-limit errors are shared.
* Search results are cached briefly via :class:`analyzer.cache.TTLCache` so
  repeated UI clicks do not re-hit the API.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .cache import TTLCache
from .github_client import GitHubClient, GitHubError, validate_owner_repo

DEFAULT_ISSUE_LIMIT = 25
MAX_ISSUE_LIMIT = 100
MAX_TITLE_CHARS = 400
MAX_BODY_CHARS = 1600
DEFAULT_SEARCH_TTL_SECONDS = 300.0

# Labels that mark an issue as approachable for a first-time contributor.
BEGINNER_LABELS = ("good first issue", "good-first-issue", "beginner", "easy", "help wanted")


# --------------------------------------------------------------------------- #
# Data containers
# --------------------------------------------------------------------------- #
@dataclass
class IssueInfo:
    """A single issue, normalised and bounded.

    ``body`` and ``title`` are raw, truncated copies of what a human wrote.
    Treat them as untrusted data.
    """

    number: int
    title: str
    body: str
    labels: List[str] = field(default_factory=list)
    state: str = "open"
    html_url: str = ""
    author: str = ""
    comments: int = 0
    created_at: str = ""
    updated_at: str = ""
    repo_full_name: str = ""
    is_pull_request: bool = False

    @property
    def labels_lower(self) -> List[str]:
        return [label.strip().lower() for label in self.labels]

    @property
    def is_beginner_friendly(self) -> bool:
        return any(label in BEGINNER_LABELS for label in self.labels_lower)

    @property
    def has_body(self) -> bool:
        return bool(self.body.strip())

    @property
    def path_hint(self) -> str:
        """Best-effort file/dir hint from the title, or ``""`` if none.

        This is only a textual hint from untrusted input; it is never used to
        read files.
        """
        text = self.title.strip()
        for token in text.replace(",", " ").split():
            if any(token.endswith(ext) for ext in (".py", ".md", ".rst", ".toml", ".yml", ".yaml")):
                return token.strip("()[]")
        return ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "number": self.number,
            "title": self.title,
            "body": self.body,
            "labels": list(self.labels),
            "state": self.state,
            "html_url": self.html_url,
            "author": self.author,
            "comments": self.comments,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "repo_full_name": self.repo_full_name,
            "is_pull_request": self.is_pull_request,
        }


@dataclass
class IssueSearchResult:
    repo_full_name: str
    query: str
    issues: List[IssueInfo] = field(default_factory=list)
    total_count: int = 0
    incomplete: bool = False
    cached: bool = False
    skipped_pull_requests: int = 0

    @property
    def beginner_friendly(self) -> List[IssueInfo]:
        return [issue for issue in self.issues if issue.is_beginner_friendly]

    @property
    def is_empty(self) -> bool:
        """True only when the search succeeded but returned no open issues.

        An empty result is a valid outcome, not an error: failures (rate limits,
        network problems, missing repositories) raise typed exceptions instead.
        """

        return not self.issues

    def to_dict(self) -> Dict[str, Any]:
        return {
            "repo_full_name": self.repo_full_name,
            "query": self.query,
            "issues": [issue.to_dict() for issue in self.issues],
            "total_count": self.total_count,
            "incomplete": self.incomplete,
            "cached": self.cached,
            "skipped_pull_requests": self.skipped_pull_requests,
        }


# --------------------------------------------------------------------------- #
# Helpers (pure functions, easy to test without HTTP)
# --------------------------------------------------------------------------- #
def _truncate(text: Optional[str], limit: int) -> str:
    if not text:
        return ""
    text = text.replace("\x00", "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def normalise_labels(raw: Iterable[Any]) -> List[str]:
    """Extract bounded label names from GitHub label objects or plain strings."""
    labels: List[str] = []
    for item in raw or []:
        if isinstance(item, str):
            name = item
        elif isinstance(item, dict):
            name = str(item.get("name") or "")
        else:
            name = str(getattr(item, "name", "") or "")
        name = _truncate(name, 60)
        if name and name not in labels:
            labels.append(name)
    return labels


def build_issue_query(
    owner: str,
    repo: str,
    labels: Optional[Sequence[str]] = None,
    state: str = "open",
    keywords: Optional[str] = None,
) -> str:
    """Build a GitHub Search API ``q`` string for real issues (not PRs).

    Uses qualifiers rather than free-text matching for structure, so results
    are predictable. Text keywords are escaped to avoid breaking the query.
    """
    owner, repo = validate_owner_repo(owner, repo)
    parts = [f"repo:{owner}/{repo}", "is:issue"]
    parts.append("is:open" if (state or "open").lower() != "closed" else "is:closed")

    for label in labels or []:
        cleaned = str(label).strip().replace('"', "")
        if cleaned:
            parts.append(f'label:"{cleaned}"')

    if keywords:
        cleaned_kw = str(keywords).replace('"', "").strip()
        if cleaned_kw:
            parts.append(cleaned_kw)

    return " ".join(parts)


def parse_search_items(
    items: Sequence[Dict[str, Any]], repo_full_name: str
) -> tuple[List[IssueInfo], int]:
    """Convert raw GitHub Search API items into bounded :class:`IssueInfo`.

    Returns ``(issues, skipped_pull_requests)``. Defensive: any item missing a
    usable ``number`` or ``title`` is dropped rather than raising.
    """
    issues: List[IssueInfo] = []
    skipped_prs = 0
    for item in items or []:
        if not isinstance(item, dict):
            continue
        if item.get("pull_request") is not None:
            skipped_prs += 1
            continue
        number = item.get("number")
        title = item.get("title")
        if not isinstance(number, int) or not title:
            continue

        html_url = str(item.get("html_url") or "")
        author = ""
        user = item.get("user")
        if isinstance(user, dict):
            author = str(user.get("login") or "")
        elif isinstance(user, str):
            author = user

        issues.append(
            IssueInfo(
                number=number,
                title=_truncate(title, MAX_TITLE_CHARS),
                body=_truncate(item.get("body") or "", MAX_BODY_CHARS),
                labels=normalise_labels(item.get("labels") or []),
                state=str(item.get("state") or "open"),
                html_url=html_url,
                author=author,
                comments=int(item.get("comments") or 0),
                created_at=str(item.get("created_at") or ""),
                updated_at=str(item.get("updated_at") or ""),
                repo_full_name=repo_full_name,
                is_pull_request=False,
            )
        )
    return issues, skipped_prs


def _cache_key(
    owner: str, repo: str, labels: Optional[Sequence[str]], state: str, limit: int, keywords: str
) -> str:
    label_part = ",".join(sorted((labels or []))).lower()
    return f"issues|{owner.lower()}/{repo.lower()}|{label_part}|{state.lower()}|{limit}|{keywords.lower()}"


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #
class IssueClient(GitHubClient):
    """Read-only issue search on top of :class:`GitHubClient`.

    Subclassing reuses the shared session, token handling, timeouts, and typed
    error mapping (``RateLimitError``, ``RepoNotFoundError``, ``GitHubError``).
    """

    def __init__(
        self,
        token: Optional[str] = None,
        session: Any = None,
        timeout: int = 20,
        api_base: str = "https://api.github.com",
        cache: Optional[TTLCache] = None,
        ttl_seconds: float = DEFAULT_SEARCH_TTL_SECONDS,
    ) -> None:
        super().__init__(token=token, session=session, timeout=timeout, api_base=api_base)
        self.cache = cache if cache is not None else TTLCache(ttl_seconds=ttl_seconds)
        self.ttl_seconds = ttl_seconds

    def search_issues(
        self,
        owner: str,
        repo: str,
        labels: Optional[Sequence[str]] = None,
        state: str = "open",
        limit: int = DEFAULT_ISSUE_LIMIT,
        keywords: Optional[str] = None,
        sort: str = "updated",
        use_cache: bool = True,
    ) -> IssueSearchResult:
        """Fetch up to *limit* open issues, bounded and cached.

        Raises :class:`GitHubError` subclasses on API failures. Returns an
        (possibly empty) result on success so the caller can render a clear
        "no issues matched" state instead of an exception.
        """
        owner, repo = validate_owner_repo(owner, repo)
        limit = max(1, min(int(limit), MAX_ISSUE_LIMIT))
        state = (state or "open").lower()
        labels = [str(label).strip() for label in (labels or []) if str(label).strip()]
        repo_full_name = f"{owner}/{repo}"
        query = build_issue_query(owner, repo, labels=labels, state=state, keywords=keywords)

        key = _cache_key(owner, repo, labels, state, limit, keywords or "")
        if use_cache:
            cached = self.cache.get(key)
            if cached is not None:
                # Return a copy so flagging it as cached never mutates the
                # stored snapshot (or a previously returned result).
                return replace(cached, cached=True)

        params = {
            "q": query,
            "per_page": limit,
            "sort": sort,
            "order": "desc",
        }
        data = self._request("GET", "/search/issues", params=params).json()
        items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items, list):
            items = []

        issues, skipped_prs = parse_search_items(items, repo_full_name)
        # Hard bound even if the API returns more than requested.
        issues = issues[:limit]

        result = IssueSearchResult(
            repo_full_name=repo_full_name,
            query=query,
            issues=issues,
            total_count=int((data or {}).get("total_count") or 0) if isinstance(data, dict) else len(issues),
            incomplete=bool((data or {}).get("incomplete_results")) if isinstance(data, dict) else False,
            cached=False,
            skipped_pull_requests=skipped_prs,
        )

        if use_cache:
            self.cache.set(key, result, ttl_seconds=self.ttl_seconds)
        return result

    def fetch_good_first_issues(
        self,
        owner: str,
        repo: str,
        limit: int = DEFAULT_ISSUE_LIMIT,
        extra_labels: Optional[Sequence[str]] = None,
    ) -> IssueSearchResult:
        """Convenience wrapper restricted to beginner-friendly labels."""
        labels = ["good first issue", *(extra_labels or [])]
        return self.search_issues(owner, repo, labels=labels, limit=limit)

    def get_issue(
        self, owner: str, repo: str, number: int
    ) -> IssueInfo:
        """Fetch a single issue by number (used to hydrate a selected issue)."""
        owner, repo = validate_owner_repo(owner, repo)
        data = self._request("GET", f"/repos/{owner}/{repo}/issues/{int(number)}").json()
        issues, _skipped = parse_search_items([data], f"{owner}/{repo}")
        if not issues:
            raise GitHubError(f"Issue #{number} could not be read (it may be a pull request).")
        return issues[0]
