"""Tests for the analysis cache, focused on revision isolation."""

from __future__ import annotations

import os

from analyzer.cache import AnalysisCache, DiskCache, TTLCache, make_key


def test_revision_isolation():
    cache = AnalysisCache()
    cache.set("acme/demo", "sha1", "main.py", {"v": 1})
    assert cache.get("acme/demo", "sha1", "main.py") == {"v": 1}
    # Same repository, different revision must not leak.
    assert cache.get("acme/demo", "sha2", "main.py") is None


def test_repository_isolation():
    cache = AnalysisCache()
    cache.set("acme/demo", "sha1", "main.py", {"v": 1})
    assert cache.get("other/repo", "sha1", "main.py") is None


def test_get_or_compute_computes_once():
    cache = AnalysisCache()
    calls = []

    def compute():
        calls.append(1)
        return 42

    assert cache.get_or_compute("r", "s", "p", compute) == 42
    assert cache.get_or_compute("r", "s", "p", compute) == 42
    assert len(calls) == 1


def test_make_key_is_filesystem_safe():
    key = make_key("acme/demo", "sha1", "pkg/mod.py")
    assert "/" not in key and "\\" not in key
    assert key == make_key("acme/demo", "sha1", "pkg/mod.py")
    assert key != make_key("acme/demo", "sha2", "pkg/mod.py")


def test_disk_cache_round_trip(tmp_path):
    cache = DiskCache(str(tmp_path))
    cache.set("acme/demo", "sha1", "main.py", {"v": 1})
    # New instance shares nothing in memory but reads from disk.
    fresh = DiskCache(str(tmp_path))
    assert fresh.get("acme/demo", "sha1", "main.py") == {"v": 1}
    assert fresh.get("acme/demo", "sha2", "main.py") is None
    assert os.listdir(str(tmp_path))


# --------------------------------------------------------------------------- #
# TTLCache (Phase 3)
# --------------------------------------------------------------------------- #
def test_ttl_cache_stores_and_expires():
    cache = TTLCache(ttl_seconds=100.0)
    now = {"t": 0.0}
    cache._clock = lambda: now["t"]  # deterministic time

    cache.set("k", "value")
    assert cache.get("k") == "value"

    now["t"] = 50.0
    assert cache.get("k") == "value"

    now["t"] = 101.0
    assert cache.get("k") is None
    assert "k" not in cache


def test_ttl_cache_get_or_compute_caches():
    cache = TTLCache(ttl_seconds=100.0)
    calls = []

    def compute():
        calls.append(1)
        return 7

    assert cache.get_or_compute("k", compute) == 7
    assert cache.get_or_compute("k", compute) == 7
    assert len(calls) == 1


def test_ttl_cache_evicts_when_full():
    cache = TTLCache(ttl_seconds=100.0, maxsize=2)
    now = {"t": 0.0}
    cache._clock = lambda: now["t"]
    cache.set("a", 1)
    cache.set("b", 2)
    cache.set("c", 3)  # should evict the oldest ("a")
    assert cache.get("a") is None
    assert cache.get("b") == 2
    assert cache.get("c") == 3
