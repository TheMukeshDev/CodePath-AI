"""UI smoke tests for the Phase 4 Contribution Guide tab.

These use Streamlit's AppTest harness to execute the real rendering code, which
catches integration problems (duplicate widget keys, bad widget arguments) that
pure unit tests cannot. No network or model is used.
"""

import pytest

from analyzer.file_scanner import analyze_source
from analyzer.github_client import RepoMetadata
from analyzer.issue_client import IssueInfo
from analyzer.recommender import ContributorProfile, score_issue

pytest.importorskip("streamlit.testing.v1")

from streamlit.testing.v1 import AppTest  # noqa: E402


def _fixtures():
    meta = RepoMetadata(
        owner="acme",
        name="demo",
        full_name="acme/demo",
        default_branch="main",
        commit_sha="abc123",
        html_url="https://github.com/acme/demo",
        language="Python",
    )
    issue = IssueInfo(
        number=7,
        title="Fix app/main.py crash",
        body="It crashes in app/main.py. It should not.",
        labels=["bug"],
        html_url="https://github.com/acme/demo/issues/7",
        repo_full_name="acme/demo",
    )
    profile = ContributorProfile(skills=["python"])
    rec = score_issue(issue, profile)
    modules = {
        "app/main.py": analyze_source("app/main.py", "def run():\n    return 1\n")
    }
    contents = {"app/main.py": "def run():\n    return 1\n"}
    return meta, modules, contents, rec, profile


def _harness(meta, modules, contents, rec, profile):
    import streamlit as st

    import app as cap
    from ai.model_client import OllamaConnectionError

    class _NoModelClient:
        """Stand-in for OllamaClient that always reports the model unavailable.

        This keeps the UI tests deterministic and offline even when a real
        Ollama server happens to be running on the developer's machine.
        """

        def __init__(self, *args, **kwargs):
            pass

        def generate(self, *args, **kwargs):
            raise OllamaConnectionError("Local model unavailable in tests.")

    original_client = cap.OllamaClient
    cap.OllamaClient = _NoModelClient
    try:
        st.session_state.setdefault("meta", meta)
        st.session_state.setdefault("modules", modules)
        st.session_state.setdefault("file_contents", contents)
        st.session_state.setdefault("issue_recs", [rec])
        st.session_state.setdefault("issue_profile", profile)
        cap.render_guide_tab()
    finally:
        cap.OllamaClient = original_client


def _run_app():
    meta, modules, contents, rec, profile = _fixtures()
    at = AppTest.from_function(
        _harness,
        default_timeout=30,
        args=(meta, modules, contents, rec, profile),
    )
    at.run()
    return at


def test_guide_tab_renders_without_error():
    at = _run_app()
    assert not at.exception


def test_build_button_produces_guide_ui():
    at = _run_app()
    build = [b for b in at.button if b.key == "p4_build"]
    assert build
    build[0].click().run()
    assert not at.exception
    # The file inspector and the checklist both appear.
    assert any(s.key.startswith("p4_file_") for s in at.selectbox)
    assert len(at.checkbox) > 0


def test_explain_without_model_falls_back_without_error():
    at = _run_app()
    [b for b in at.button if b.key == "p4_build"][0].click().run()
    explain = [b for b in at.button if b.key and b.key.startswith("p4_explain_")]
    assert explain
    explain[0].click().run()
    assert not at.exception
