"""Simple, dependency-free caching for CodePath AI.

The cache prevents re-retrieving and re-parsing unchanged files. Keys always
include the repository identity and the exact revision (commit SHA), so cached
results from one repository or revision can never leak into another.

No database is used: the in-memory variant is a dict and the optional disk
variant writes one JSON file per key inside a directory.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from typing import Any, Callable, Dict, Optional, Tuple

_SAFE_KEY_RE = re.compile(r"[^A-Za-z0-9._@-]+")


def make_key(repository: str, revision: str, path: str) -> str:
    """Build a cache key isolated by repository and revision.

    ``repository`` should be ``owner/name`` and ``revision`` should be a commit
    SHA or branch ref. The returned key is filesystem-safe.
    """
    raw = f"{repository}@{revision}:{path}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    prefix = _SAFE_KEY_RE.sub("_", f"{repository}@{revision}")[:60]
    return f"{prefix}--{digest}"


class AnalysisCache:
    """A tiny in-memory key/value cache with revision-scoped keys."""

    def __init__(self, maxsize: int = 512) -> None:
        self.maxsize = maxsize
        self._store: Dict[str, Any] = {}

    def get(self, repository: str, revision: str, path: str) -> Optional[Any]:
        return self._store.get(make_key(repository, revision, path))

    def set(self, repository: str, revision: str, path: str, value: Any) -> None:
        if len(self._store) >= self.maxsize:
            # Evict the oldest inserted entry (dicts preserve insertion order).
            for oldest in next(iter(self._store)):
                del self._store[oldest]
                break
        self._store[make_key(repository, revision, path)] = value

    def get_or_compute(
        self,
        repository: str,
        revision: str,
        path: str,
        compute: Callable[[], Any],
    ) -> Any:
        key = make_key(repository, revision, path)
        if key in self._store:
            return self._store[key]
        value = compute()
        self.set(repository, revision, path, value)
        return value

    def clear(self) -> None:
        self._store.clear()

    def __len__(self) -> int:
        return len(self._store)

    def __contains__(self, key: str) -> bool:
        return key in self._store


class DiskCache(AnalysisCache):
    """An :class:`AnalysisCache` that also persists JSON-serialisable values.

    Values must be JSON-serialisable. Corrupt or unreadable files are treated
    as cache misses so a bad cache can never break an analysis.
    """

    def __init__(self, directory: str, maxsize: int = 512) -> None:
        super().__init__(maxsize=maxsize)
        self.directory = directory
        os.makedirs(self.directory, exist_ok=True)

    def _path_for(self, repository: str, revision: str, path: str) -> str:
        return os.path.join(self.directory, make_key(repository, revision, path) + ".json")

    def get(self, repository: str, revision: str, path: str) -> Optional[Any]:
        value = super().get(repository, revision, path)
        if value is not None:
            return value
        file_path = self._path_for(repository, revision, path)
        if not os.path.exists(file_path):
            return None
        try:
            with open(file_path, "r", encoding="utf-8") as handle:
                value = json.load(handle)
        except (OSError, ValueError):
            return None
        self._store[make_key(repository, revision, path)] = value
        return value

    def set(self, repository: str, revision: str, path: str, value: Any) -> None:
        super().set(repository, revision, path, value)
        file_path = self._path_for(repository, revision, path)
        try:
            with open(file_path, "w", encoding="utf-8") as handle:
                json.dump(value, handle)
        except (OSError, TypeError, ValueError):
            # Caching is best-effort; failures must never surface to the user.
            pass


class TTLCache:
    """A small time-to-live cache for transient, non-critical lookups.

    Used by Phase 3 issue discovery so repeated searches against the same
    repository/label combination do not re-hit the GitHub API. Expired entries
    are treated as misses and pruned lazily, so no background thread is needed.

    Values are returned as-is; callers should treat entries as read-only
    snapshots rather than mutable state.
    """

    def __init__(self, ttl_seconds: float = 300.0, maxsize: int = 256) -> None:
        self.ttl_seconds = float(ttl_seconds)
        self.maxsize = int(maxsize)
        self._store: Dict[str, Tuple[float, Any]] = {}
        self._clock: Callable[[], float] = time.monotonic

    def _expired(self, expires_at: float) -> bool:
        return self._clock() >= expires_at

    def get(self, key: str) -> Optional[Any]:
        entry = self._store.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if self._expired(expires_at):
            del self._store[key]
            return None
        return value

    def set(self, key: str, value: Any, ttl_seconds: Optional[float] = None) -> None:
        ttl = self.ttl_seconds if ttl_seconds is None else float(ttl_seconds)
        if len(self._store) >= self.maxsize:
            self._evict()
        self._store[key] = (self._clock() + ttl, value)

    def get_or_compute(
        self, key: str, compute: Callable[[], Any], ttl_seconds: Optional[float] = None
    ) -> Any:
        cached = self.get(key)
        if cached is not None:
            return cached
        value = compute()
        self.set(key, value, ttl_seconds=ttl_seconds)
        return value

    def _evict(self) -> None:
        """Drop expired entries first, otherwise the oldest-inserted one."""
        expired = [k for k, (expires_at, _v) in self._store.items() if self._expired(expires_at)]
        for key in expired:
            del self._store[key]
        while len(self._store) >= self.maxsize:
            for oldest in self._store:
                del self._store[oldest]
                break

    def clear(self) -> None:
        self._store.clear()

    def __len__(self) -> int:
        return len(self._store)

    def __contains__(self, key: str) -> bool:
        return self.get(key) is not None
