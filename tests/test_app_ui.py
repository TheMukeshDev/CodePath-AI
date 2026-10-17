"""Automated UI tests for the main CodePath AI Streamlit application.

Uses Streamlit's ``AppTest`` harness to execute the real ``app.py`` rendering
code. These catch integration problems (duplicate widget keys, bad widget
arguments, session-state crashes) that unit tests cannot. No network and no
model are used: GitHub access is replaced by a fake client.

``AppTest.from_function`` executes only the function body, so every harness
below imports ``app`` and defines its own helpers rather than relying on
closures from this test module.
"""

from __future__ import annotations

import pytest

from analyzer.github_client import InvalidRepoUrlError

pytest.importorskip("streamlit.testing.v1")

from streamlit.testing.v1 import AppTest  # noqa: E402

import app as cap  # noqa: E402


def _run_main():
    import app as cap

    cap.main()


def test_app_starts_without_repository():
    at = AppTest.from_function(_run_main, default_timeout=30)
    at.run()
    assert not at.exception
    assert any("Enter a public GitHub repository URL" in i.value for i in at.info)


def test_sidebar_exposes_repository_input():
    at = AppTest.from_function(_run_main, default_timeout=30)
    at.run()
    assert not at.exception
    keys = {t.key for t in at.text_input}
    assert "repo_url_input" in keys
    assert "github_token_input" in keys
    button_keys = {b.key for b in at.button}
    assert {"fetch_repo_btn", "analyze_btn", "ai_btn"} <= button_keys


def _fetch_harness(seed_full_name, new_url):
    import streamlit as st

    import app as cap
    from analyzer.github_client import RepoMetadata

    def meta(owner, name):
        return RepoMetadata(
            owner=owner,
            name=name,
            full_name=f"{owner}/{name}",
            default_branch="main",
            commit_sha="deadbeef",
            html_url=f"https://github.com/{owner}/{name}",
            language="Python",
        )

    owner, name = seed_full_name.split("/")
    st.session_state.meta = meta(owner, name)
    st.session_state.issue_recs = ["keep-me"]
    st.session_state.issue_search = "stale"
    st.session_state.issue_profile = "stale"
    st.session_state.guide_bundle = {"stale": True}
    st.session_state.checklist_state = {"k": {"i": True}}
    st.session_state["chk::k::i"] = True

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def get_repository(self, o, r):
            return meta(o, r)

        def get_readme(self, o, r, ref=None):
            return "# Demo"

        def get_file_tree(self, o, r, ref=None):
            return ([{"path": "main.py", "type": "blob", "sha": "s1", "size": 10}], False)

    cap._client = lambda token: _FakeClient()
    cap.fetch_repository(new_url, "")
    st.write("fetch-complete")


def test_fetch_repository_resets_issue_state_for_new_repo():
    at = AppTest.from_function(
        _fetch_harness,
        default_timeout=30,
        args=("old/repo", "https://github.com/new/repo"),
    )
    at.run()
    assert not at.exception
    ss = at.session_state
    assert ss["meta"].full_name == "new/repo"
    assert ss["issue_recs"] is None
    assert ss["issue_search"] is None
    assert ss["issue_profile"] is None
    assert ss["guide_bundle"] is None
    assert ss["checklist_state"] == {}
    assert "chk::k::i" not in ss


def test_fetch_same_repository_keeps_issue_state():
    at = AppTest.from_function(
        _fetch_harness,
        default_timeout=30,
        args=("same/repo", "https://github.com/same/repo"),
    )
    at.run()
    assert not at.exception
    assert at.session_state["issue_recs"] == ["keep-me"]


def test_fetch_repository_rejects_invalid_url():
    class _NeverClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("client must not be created for an invalid URL")

    original = cap._client
    cap._client = lambda token: _NeverClient()
    try:
        with pytest.raises(InvalidRepoUrlError):
            cap.fetch_repository("https://gitlab.com/owner/repo", "")
    finally:
        cap._client = original
