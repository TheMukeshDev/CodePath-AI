"""Shared pytest configuration.

The GitHub client keeps a small on-disk JSON cache next to the package
(``analyzer/.github_cache.json``). Without isolation, one test can populate that
file and change the behaviour of a later test (for example, a cached repository
would let ``get_repository`` succeed even when the mocked request raises a rate
limit). This fixture points every test at a throwaway cache path so results are
independent of run order and never touch the real project cache.
"""

import pytest

import analyzer.github_client as github_client


@pytest.fixture(autouse=True)
def _isolate_github_disk_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(
        github_client, "_CACHE_FILE", tmp_path / ".github_cache.json"
    )
    yield
