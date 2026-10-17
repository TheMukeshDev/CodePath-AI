"""Tests for the optional local-AI issue explainer (Phase 3, no model needed)."""

import json

import pytest

from analyzer.issue_client import IssueInfo
from analyzer.recommender import ContributorProfile, score_issue
from ai.issue_explainer import (
    MAX_EVIDENCE_CHARS,
    SCORE_NOTE,
    build_issue_evidence,
    explain_issue,
    fallback_explanation,
)
from ai.model_client import OllamaConnectionError


def make_issue(number=1, title="Fix typo in docs.py", body="Please fix the typo.", labels=None):
    return IssueInfo(
        number=number,
        title=title,
        body=body,
        labels=list(labels or ["good first issue"]),
        html_url="https://github.com/acme/demo/issues/1",
        repo_full_name="acme/demo",
        updated_at="2026-10-10T00:00:00Z",
        created_at="2026-10-01T00:00:00Z",
        comments=0,
    )


class FakeClient:
    def __init__(self, reply=None, raise_error=None):
        self.reply = reply
        self.raise_error = raise_error
        self.calls = []

    def generate(self, prompt, system=None, model=None, timeout=None):
        self.calls.append({"prompt": prompt, "system": system, "model": model})
        if self.raise_error:
            raise self.raise_error
        return self.reply


# --------------------------------------------------------------------------- #
# Evidence building
# --------------------------------------------------------------------------- #
def test_evidence_contains_real_fields_only():
    issue = make_issue()
    profile = ContributorProfile(skills=["docs"])
    rec = score_issue(issue, profile)
    evidence = build_issue_evidence(issue, profile, rec)
    assert "acme/demo" in evidence
    assert "good first issue" in evidence
    assert "https://github.com/acme/demo/issues/1" in evidence
    assert len(evidence) <= MAX_EVIDENCE_CHARS + 32


def test_evidence_is_bounded():
    huge = make_issue(body="x" * 50000)
    profile = ContributorProfile()
    evidence = build_issue_evidence(huge, profile, None)
    assert len(evidence) <= MAX_EVIDENCE_CHARS + 32
    assert "truncated" in evidence


# --------------------------------------------------------------------------- #
# Fallback
# --------------------------------------------------------------------------- #
def test_fallback_has_no_score_and_is_marked_deterministic():
    issue = make_issue()
    profile = ContributorProfile(skills=["docs"])
    rec = score_issue(issue, profile)
    fb = fallback_explanation(issue, profile, rec, error="no model")
    assert fb.used_model is False
    assert fb.parsed_ok is False
    assert fb.error == "no model"
    assert not hasattr(fb, "match_score")
    assert fb.score_note == SCORE_NOTE
    assert fb.suggested_first_steps


def test_no_client_returns_fallback():
    issue = make_issue()
    profile = ContributorProfile()
    result = explain_issue(issue, profile, client=None)
    assert result.used_model is False
    assert "No local model" in (result.error or "")


# --------------------------------------------------------------------------- #
# Model-backed path
# --------------------------------------------------------------------------- #
def test_valid_json_is_parsed():
    issue = make_issue()
    profile = ContributorProfile(skills=["docs"])
    rec = score_issue(issue, profile)
    payload = {
        "summary": "Fix a documentation typo.",
        "why_it_matches": "It mentions docs, which you know.",
        "skills_to_use": ["docs"],
        "skills_to_learn": [],
        "suggested_first_steps": ["Open docs.py", "Fix the typo"],
        "relevant_files": ["docs.py"],
        "limitations": ["No test linked."],
    }
    client = FakeClient(reply=json.dumps(payload))
    result = explain_issue(issue, profile, rec, client=client)
    assert result.parsed_ok is True
    assert result.used_model is True
    assert result.summary == "Fix a documentation typo."
    assert result.suggested_first_steps == ["Open docs.py", "Fix the typo"]


def test_json_in_markdown_fence_is_parsed():
    issue = make_issue()
    profile = ContributorProfile()
    reply = "Here you go:\n```json\n{\"summary\": \"ok\"}\n```\n"
    result = explain_issue(issue, profile, client=FakeClient(reply=reply))
    assert result.parsed_ok is True
    assert result.summary == "ok"


def test_invalid_json_falls_back_and_keeps_raw():
    issue = make_issue()
    profile = ContributorProfile()
    result = explain_issue(issue, profile, client=FakeClient(reply="not json at all"))
    assert result.parsed_ok is False
    assert result.raw_text == "not json at all"
    assert result.limitations


def test_model_error_falls_back():
    issue = make_issue()
    profile = ContributorProfile()
    result = explain_issue(
        issue, profile, client=FakeClient(raise_error=OllamaConnectionError("down"))
    )
    assert result.used_model is False
    assert "down" in (result.error or "")


# --------------------------------------------------------------------------- #
# Anti-fabrication and score immutability
# --------------------------------------------------------------------------- #
def test_unknown_files_are_dropped():
    issue = make_issue()
    profile = ContributorProfile()
    reply = json.dumps(
        {
            "summary": "s",
            "relevant_files": ["docs.py", "secrets/evil.py"],
        }
    )
    result = explain_issue(
        issue,
        profile,
        client=FakeClient(reply=reply),
        known_paths=["docs.py", "src/main.py"],
    )
    assert result.relevant_files == ["docs.py"]
    assert result.dropped_files == ["secrets/evil.py"]
    assert any("not present in the repository" in lim for lim in result.limitations)


def test_without_known_paths_files_are_kept():
    issue = make_issue()
    profile = ContributorProfile()
    reply = json.dumps({"summary": "s", "relevant_files": ["docs.py"]})
    result = explain_issue(issue, profile, client=FakeClient(reply=reply))
    assert result.relevant_files == ["docs.py"]


def test_deterministic_score_is_never_in_explanation_output():
    issue = make_issue()
    profile = ContributorProfile(skills=["docs"])
    rec = score_issue(issue, profile)
    original_score = rec.match_score
    reply = json.dumps({"summary": "s", "match_score": 999})
    result = explain_issue(issue, profile, rec, client=FakeClient(reply=reply))
    # The model cannot change the deterministic score...
    assert rec.match_score == original_score
    # ...and no score field leaks into the explanation payload.
    assert "match_score" not in result.to_dict()
    assert "999" not in json.dumps(result.to_dict())


def test_prompt_wraps_evidence_as_untrusted():
    issue = make_issue()
    profile = ContributorProfile()
    client = FakeClient(reply=json.dumps({"summary": "s"}))
    explain_issue(issue, profile, client=client)
    sent = client.calls[0]["prompt"]
    assert "UNTRUSTED" in sent
    assert client.calls[0]["system"]
