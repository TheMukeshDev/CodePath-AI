"""Safe GitHub REST API access for CodePath AI.

This module is responsible only for *retrieving* repository data from the
public GitHub REST API. It never clones, executes, or writes to a repository.
All downloaded content is treated as untrusted data.

Design goals:
    * Resolve and validate repository URLs and paths (no path traversal).
    * Work against the repository's real default branch and revision SHA.
    * Enforce per-file size limits and a maximum number of analyzed files.
    * Ignore binary files, oversized files, and common vendor/build directories.
    * Fail gracefully on rate limits, missing files, and network errors.
"""

from __future__ import annotations

import base64
import binascii
import json
from pathlib import Path
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import requests

_CACHE_FILE = Path(__file__).parent / ".github_cache.json"

def _read_persistent_cache() -> Dict[str, Any]:
    try:
        if _CACHE_FILE.exists():
            return json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}

def _write_persistent_cache(data: Dict[str, Any]) -> None:
    try:
        _CACHE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass

GITHUB_API = "https://api.github.com"
GITHUB_WEB = "https://github.com"

DEFAULT_TIMEOUT = 20
DEFAULT_MAX_RETRIES = 2  # extra attempts after the first (bounded)
DEFAULT_MAX_FILE_BYTES = 100 * 1024  # 100 KB
DEFAULT_MAX_FILES = 100

# Directories that are never part of the source the user wants to read.
EXCLUDED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "env",
    ".env",
    "node_modules",
    "dist",
    "build",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".eggs",
    "site-packages",
    "site",
    "vendor",
    ".idea",
    ".vscode",
    "docs/_build",
}

# Extensions we consider "source" for the Python-first implementation.
SUPPORTED_EXTENSIONS = (".py", ".pyi")

# Filenames that are most likely to be important; used to prioritise
# retrieval when the max-file limit truncates the repository.
_PRIORITY_BASENAMES = {
    "__main__.py": 0,
    "main.py": 1,
    "app.py": 2,
    "cli.py": 3,
    "manage.py": 4,
    "setup.py": 5,
    "__init__.py": 8,
}

_OWNER_REPO_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$")


# --------------------------------------------------------------------------- #
# Exceptions
# --------------------------------------------------------------------------- #
class GitHubError(Exception):
    """Base class for all GitHub client errors."""


class InvalidRepoUrlError(GitHubError):
    """Raised when a repository URL cannot be parsed or validated."""


class RepoNotFoundError(GitHubError):
    """Raised when a repository (or revision) does not exist / is private."""


class RateLimitError(GitHubError):
    """Raised when the GitHub API rate limit has been exceeded."""

    def __init__(self, message: str, reset_at: Optional[int] = None) -> None:
        super().__init__(message)
        self.reset_at = reset_at


class NetworkError(GitHubError):
    """Raised on transport-level failures (DNS, timeout, connection reset)."""


class TruncatedTreeError(GitHubError):
    """Raised when GitHub reports a truncated (incomplete) recursive tree."""


# --------------------------------------------------------------------------- #
# Data containers
# --------------------------------------------------------------------------- #
@dataclass
class RepoMetadata:
    owner: str
    name: str
    full_name: str
    default_branch: str
    commit_sha: str
    description: str = ""
    html_url: str = ""
    stars: int = 0
    forks: int = 0
    open_issues: int = 0
    language: str = ""
    license_name: str = ""
    topics: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "owner": self.owner,
            "name": self.name,
            "full_name": self.full_name,
            "default_branch": self.default_branch,
            "commit_sha": self.commit_sha,
            "description": self.description,
            "html_url": self.html_url,
            "stars": self.stars,
            "forks": self.forks,
            "open_issues": self.open_issues,
            "language": self.language,
            "license_name": self.license_name,
            "topics": list(self.topics),
        }


@dataclass
class RetrievedFile:
    """A single retrieved source file (or the reason it was skipped)."""

    path: str
    size: int = 0
    content: Optional[str] = None
    encoding: str = "utf-8"
    status: str = "ok"  # ok | too_large | binary | decode_error | missing

    @property
    def ok(self) -> bool:
        return self.status == "ok" and self.content is not None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "size": self.size,
            "encoding": self.encoding,
            "status": self.status,
        }


@dataclass
class FileRetrievalResult:
    repo: RepoMetadata
    files: List[RetrievedFile] = field(default_factory=list)
    skipped: List[Dict[str, str]] = field(default_factory=list)
    tree_truncated: bool = False
    file_limit_reached: bool = False

    @property
    def ok_files(self) -> List[RetrievedFile]:
        return [f for f in self.files if f.ok]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "repo": self.repo.to_dict(),
            "files": [f.to_dict() for f in self.files],
            "skipped": list(self.skipped),
            "tree_truncated": self.tree_truncated,
            "file_limit_reached": self.file_limit_reached,
        }


# --------------------------------------------------------------------------- #
# URL parsing helpers
# --------------------------------------------------------------------------- #
def validate_owner_repo(owner: str, repo: str) -> Tuple[str, str]:
    """Validate and normalise a raw ``owner`` / ``repo`` pair.

    Raises :class:`InvalidRepoUrlError` for empty, malformed, or traversal-prone
    values. Used by both repository and issue retrieval.
    """
    if not owner or not repo:
        raise InvalidRepoUrlError("Repository owner and name are required.")
    owner = str(owner).strip()
    repo = str(repo).strip()
    if repo.endswith(".git"):
        repo = repo[: -len(".git")]
    if owner in (".", "..") or repo in (".", ".."):
        raise InvalidRepoUrlError("Path traversal is not allowed in repository names.")
    if not _OWNER_REPO_RE.match(owner) or not _OWNER_REPO_RE.match(repo):
        raise InvalidRepoUrlError(f"Invalid repository owner/name: '{owner}/{repo}'.")
    return owner, repo


def parse_github_url(url: str) -> Tuple[str, str]:
    """Parse a public GitHub repository URL into ``(owner, repo)``.

    Accepts ``https://github.com/owner/repo``, with or without a trailing
    ``.git`` or slash, and the bare ``github.com/owner/repo`` form. Raises
    :class:`InvalidRepoUrlError` for anything else (e.g. non-GitHub hosts,
    URLs containing extra path segments, or path traversal attempts).
    """
    if not url or not isinstance(url, str):
        raise InvalidRepoUrlError("Repository URL must be a non-empty string.")

    candidate = url.strip()
    candidate = re.sub(r"^https?://", "", candidate, flags=re.IGNORECASE)
    candidate = re.sub(r"^www\.", "", candidate, flags=re.IGNORECASE)
    candidate = candidate.split("#", 1)[0].split("?", 1)[0]
    candidate = candidate.rstrip("/")

    if candidate.lower().startswith("github.com/"):
        candidate = candidate[len("github.com/") :]
    elif "/" in candidate and not candidate.lower().startswith("github.com"):
        # A host other than github.com was supplied.
        host = candidate.split("/", 1)[0]
        if "." in host:
            raise InvalidRepoUrlError(
                f"Only github.com repositories are supported (got host '{host}')."
            )

    candidate = candidate.strip("/")
    if candidate.endswith(".git"):
        candidate = candidate[: -len(".git")]

    parts = candidate.split("/")
    if len(parts) != 2:
        raise InvalidRepoUrlError(
            "Expected a repository URL of the form https://github.com/owner/repo."
        )

    owner, repo = parts[0].strip(), parts[1].strip()
    if not _OWNER_REPO_RE.match(owner) or not _OWNER_REPO_RE.match(repo):
        raise InvalidRepoUrlError(f"Invalid owner or repository name in '{url}'.")
    if owner in (".", "..") or repo in (".", ".."):
        raise InvalidRepoUrlError("Path traversal is not allowed in repository URLs.")

    return owner, repo


def is_excluded_path(path: str) -> bool:
    """Return ``True`` if *path* lives in an excluded vendor/build directory."""
    if not path:
        return True
    normalised = path.replace("\\", "/").strip("/")
    if ".." in normalised.split("/"):
        return True
    parts = [p for p in normalised.split("/") if p]
    return any(part in EXCLUDED_DIRS for part in parts)


def _priority_key(path: str) -> Tuple[int, int, str]:
    basename = path.rsplit("/", 1)[-1]
    rank = _PRIORITY_BASENAMES.get(basename, 50)
    depth = path.count("/")
    return (rank, depth, path)


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #
class GitHubClient:
    """A small, defensive wrapper around the public GitHub REST API."""

    def __init__(
        self,
        token: Optional[str] = None,
        session: Optional[requests.Session] = None,
        timeout: int = DEFAULT_TIMEOUT,
        api_base: str = GITHUB_API,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> None:
        self.token = token or None
        self.timeout = timeout
        self.api_base = api_base.rstrip("/")
        self.session = session or requests.Session()
        self.max_retries = max(0, int(max_retries))

    # -- low level ---------------------------------------------------------- #
    def _headers(self) -> Dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "CodePath-AI",
        }
        if self.token:
            # The token is never logged or returned to callers.
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        url = path if path.startswith("http") else f"{self.api_base}{path}"
        # Bounded retries for *transient transport* failures only. Rate limits,
        # 404s, and other HTTP errors are NOT retried (they are handled below).
        last_exc: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            try:
                response = self.session.request(
                    method,
                    url,
                    headers=self._headers(),
                    timeout=self.timeout,
                    **kwargs,
                )
                break
            except requests.exceptions.Timeout as exc:
                last_exc = NetworkError(f"Request to GitHub timed out: {url}")
            except requests.exceptions.RequestException as exc:
                last_exc = NetworkError(f"Network error contacting GitHub: {exc}")
            if attempt < self.max_retries:
                time.sleep(0.5 * (attempt + 1))
        else:
            raise last_exc if last_exc is not None else GitHubError(
                f"Request to GitHub failed: {url}"
            )

        if response.status_code in (403, 429):
            remaining = response.headers.get("X-RateLimit-Remaining")
            if response.status_code == 429 or remaining == "0":
                reset = response.headers.get("X-RateLimit-Reset")
                reset_at = int(reset) if reset and reset.isdigit() else None
                raise RateLimitError(
                    "GitHub API rate limit exceeded. "
                    "Provide a token or wait before retrying.",
                    reset_at=reset_at,
                )
            raise NetworkError(f"GitHub returned HTTP {response.status_code}.")
        if response.status_code == 404:
            raise RepoNotFoundError(
                "Not found (repository, revision, or file may be private/missing)."
            )
        if response.status_code >= 400:
            raise GitHubError(f"GitHub API error {response.status_code} for {url}.")
        return response

    @staticmethod
    def _decode_blob(data: Dict[str, Any]) -> Tuple[Optional[str], str, str]:
        """Decode a base64 Git blob into text safely.

        Returns ``(text, encoding, status)`` where status is one of
        ``ok``, ``binary`` or ``decode_error``.
        """
        raw_b64 = data.get("content")
        if data.get("encoding") == "base64" and raw_b64 is not None:
            try:
                raw = base64.b64decode(raw_b64, validate=False)
            except (binascii.Error, ValueError):
                return None, "utf-8", "decode_error"
        elif isinstance(data.get("content"), str):
            raw = data["content"].encode("utf-8", errors="replace")
        else:
            return None, "utf-8", "decode_error"

        if b"\x00" in raw:
            return None, "utf-8", "binary"

        for encoding in ("utf-8", "utf-8-sig", "latin-1"):
            try:
                return raw.decode(encoding), encoding, "ok"
            except (UnicodeDecodeError, LookupError):
                continue
        return raw.decode("utf-8", errors="replace"), "utf-8-replace", "ok"

    # -- repository metadata ----------------------------------------------- #
    def get_repository(
        self, owner: str, repo: str, include_sha: bool = True
    ) -> RepoMetadata:
        """Fetch repository metadata (and the default-branch commit SHA)."""
        owner, repo = validate_owner_repo(owner, repo)
        cache_key = f"{owner.lower()}/{repo.lower()}"
        persisted = _read_persistent_cache().get("repos", {}).get(cache_key)
        cached_meta = RepoMetadata(**persisted) if persisted else None

        try:
            data = self._request("GET", f"/repos/{owner}/{repo}").json()
        except RateLimitError:
            if cached_meta is not None:
                return cached_meta
            raise

        default_branch = data.get("default_branch") or "main"
        commit_sha = ""
        if include_sha:
            try:
                commit_sha = self.get_commit_sha(owner, repo, default_branch)
            except (RepoNotFoundError, RateLimitError):
                commit_sha = cached_meta.commit_sha if cached_meta else ""

        license_info = data.get("license") or {}
        meta = RepoMetadata(
            owner=owner,
            name=repo,
            full_name=data.get("full_name", f"{owner}/{repo}"),
            default_branch=default_branch,
            commit_sha=commit_sha,
            description=data.get("description") or "",
            html_url=data.get("html_url") or f"{GITHUB_WEB}/{owner}/{repo}",
            stars=int(data.get("stargazers_count") or 0),
            forks=int(data.get("forks_count") or 0),
            open_issues=int(data.get("open_issues_count") or 0),
            language=data.get("language") or "",
            license_name=(license_info.get("spdx_id") or license_info.get("name") or "")
            if isinstance(license_info, dict)
            else "",
            topics=list(data.get("topics") or []),
        )
        cache = _read_persistent_cache()
        cache.setdefault("repos", {})[cache_key] = meta.to_dict()
        _write_persistent_cache(cache)
        return meta

    def get_commit_sha(self, owner: str, repo: str, ref: str) -> str:
        data = self._request("GET", f"/repos/{owner}/{repo}/commits/{ref}").json()
        return str(data.get("sha") or "")

    # -- README ------------------------------------------------------------- #
    def get_readme(
        self, owner: str, repo: str, ref: Optional[str] = None, max_bytes: int = 40000
    ) -> str:
        params = {"ref": ref} if ref else {}
        try:
            data = self._request(
                "GET", f"/repos/{owner}/{repo}/readme", params=params
            ).json()
        except RateLimitError:
            branch = ref or "HEAD"
            for candidate in ("README.md", "readme.md", "README.rst", "README.txt", "README"):
                try:
                    resp = self.session.get(
                        f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{candidate}",
                        timeout=self.timeout,
                    )
                    if resp.status_code == 200:
                        return resp.text[:max_bytes]
                except Exception:
                    pass
            raise
        except RepoNotFoundError:
            return ""
        text, _encoding, status = self._decode_blob(data)
        if status != "ok" or text is None:
            return ""
        return text[:max_bytes]

    # -- file tree ---------------------------------------------------------- #
    def get_file_tree(
        self, owner: str, repo: str, ref: str
    ) -> Tuple[List[Dict[str, Any]], bool]:
        """Return ``(blob_entries, truncated)`` for a revision.

        Only regular-file (``blob``) entries are returned. The ``truncated``
        flag mirrors GitHub's own signal that the recursive tree was cut off.
        """
        owner, repo = validate_owner_repo(owner, repo)
        cache_key = f"{owner.lower()}/{repo.lower()}:{ref}"
        persisted = _read_persistent_cache().get("trees", {}).get(cache_key)
        cached_tree = (persisted["entries"], bool(persisted.get("truncated", False))) if persisted else None

        try:
            data = self._request(
                "GET",
                f"/repos/{owner}/{repo}/git/trees/{ref}",
                params={"recursive": "1"},
            ).json()
        except RateLimitError:
            if cached_tree is not None:
                return cached_tree
            raise

        entries = [
            entry
            for entry in data.get("tree", [])
            if entry.get("type") == "blob" and entry.get("path")
        ]
        result = (entries, bool(data.get("truncated", False)))
        cache = _read_persistent_cache()
        cache.setdefault("trees", {})[cache_key] = {
            "entries": entries,
            "truncated": bool(data.get("truncated", False))
        }
        _write_persistent_cache(cache)
        return result

    # -- single file -------------------------------------------------------- #
    def get_file_content(
        self,
        owner: str,
        repo: str,
        blob_sha: str,
        max_bytes: int = DEFAULT_MAX_FILE_BYTES,
        path: Optional[str] = None,
        ref: Optional[str] = None,
    ) -> Tuple[Optional[str], str, str, int]:
        """Fetch and decode a blob.

        Returns ``(text, encoding, status, size)``. ``status`` is one of
        ``ok``, ``too_large``, ``binary``, ``decode_error`` or ``missing``.
        The size limit is enforced *before* decoding.
        """
        # When unauthenticated without a token, prefer raw.githubusercontent.com
        # to avoid burning through GitHub REST API's 60 req/hr rate limit.
        if not self.token and path and ref:
            raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{path}"
            try:
                resp = self.session.get(raw_url, timeout=self.timeout)
                if resp.status_code == 200:
                    raw_bytes = resp.content
                    if len(raw_bytes) > max_bytes:
                        return None, "utf-8", "too_large", len(raw_bytes)
                    try:
                        text = raw_bytes.decode("utf-8")
                        return text, "utf-8", "ok", len(raw_bytes)
                    except UnicodeDecodeError:
                        try:
                            text = raw_bytes.decode("latin-1")
                            return text, "latin-1", "ok", len(raw_bytes)
                        except Exception:
                            return None, "binary", "binary", len(raw_bytes)
                elif resp.status_code == 404:
                    return None, "utf-8", "missing", 0
            except Exception:
                pass  # Fall back to REST API below

        try:
            data = self._request(
                "GET", f"/repos/{owner}/{repo}/git/blobs/{blob_sha}"
            ).json()
        except RateLimitError:
            # Fall back to raw if rate limited
            if path and ref:
                raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{path}"
                try:
                    resp = self.session.get(raw_url, timeout=self.timeout)
                    if resp.status_code == 200:
                        raw_bytes = resp.content
                        if len(raw_bytes) > max_bytes:
                            return None, "utf-8", "too_large", len(raw_bytes)
                        try:
                            text = raw_bytes.decode("utf-8")
                            return text, "utf-8", "ok", len(raw_bytes)
                        except UnicodeDecodeError:
                            return None, "binary", "binary", len(raw_bytes)
                    elif resp.status_code == 404:
                        return None, "utf-8", "missing", 0
                except Exception:
                    pass
            raise
        except RepoNotFoundError:
            return None, "utf-8", "missing", 0

        size = int(data.get("size") or 0)
        if size > max_bytes:
            return None, "utf-8", "too_large", size

        text, encoding, status = self._decode_blob(data)
        if status != "ok" or text is None:
            return None, encoding, status, size
        if len(text.encode("utf-8", errors="ignore")) > max_bytes:
            return None, encoding, "too_large", size
        return text, encoding, "ok", size

    # -- high level retrieval ---------------------------------------------- #
    def fetch_python_files(
        self,
        owner: str,
        repo: str,
        ref: Optional[str] = None,
        max_files: int = DEFAULT_MAX_FILES,
        max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
        extensions: Sequence[str] = SUPPORTED_EXTENSIONS,
        metadata: Optional[RepoMetadata] = None,
    ) -> FileRetrievalResult:
        """Retrieve up to *max_files* supported source files from a revision.

        Retrieval is deliberately defensive: excluded directories, binary
        files, and oversized files are skipped and reported rather than
        raising. This keeps a single bad file from aborting an analysis.
        """
        meta = metadata or self.get_repository(owner, repo)
        revision = ref or meta.default_branch or meta.commit_sha or "HEAD"

        entries, tree_truncated = self.get_file_tree(owner, repo, revision)

        candidates: List[Dict[str, Any]] = []
        skipped: List[Dict[str, str]] = []
        for entry in entries:
            path = entry["path"]
            if is_excluded_path(path):
                continue
            if not path.endswith(tuple(extensions)):
                continue
            size = int(entry.get("size") or 0)
            if size > max_file_bytes:
                skipped.append({"path": path, "reason": "too_large"})
                continue
            candidates.append(entry)

        candidates.sort(key=lambda e: _priority_key(e["path"]))

        result = FileRetrievalResult(repo=meta, tree_truncated=tree_truncated)

        selected = candidates[:max_files]
        if len(candidates) > max_files:
            result.file_limit_reached = True
            for entry in candidates[max_files:]:
                skipped.append({"path": entry["path"], "reason": "file_limit"})

        for entry in selected:
            path = entry["path"]
            blob_sha = entry.get("sha")
            if not blob_sha:
                skipped.append({"path": path, "reason": "missing_sha"})
                continue
            text, encoding, status, size = self.get_file_content(
                owner, repo, blob_sha, max_bytes=max_file_bytes, path=path, ref=revision
            )
            result.files.append(
                RetrievedFile(
                    path=path,
                    size=size or int(entry.get("size") or 0),
                    content=text,
                    encoding=encoding,
                    status=status,
                )
            )
            if status != "ok":
                skipped.append({"path": path, "reason": status})

        result.skipped = skipped
        return result
TruncatedTreeError = GitHubError

# ensure correct aliases
InvalidGitHubURL = InvalidRepoUrlError
RepoNotFound = RepoNotFoundError
RateLimitExceeded = RateLimitError
