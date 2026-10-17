"""Tests for the optional Phase 4 AI helpers (no model required)."""

import json

from analyzer.contribution_guide import build_contribution_guide
from analyzer.file_scanner import analyze_source
from analyzer.issue_client import IssueInfo
from analyzer.issue_linker import IssueLinkResult
from analyzer.recommender import ContributorProfile
from ai.contribution_assistant import (
    MAX_EVIDENCE_CHARS,
    build_file_evidence,
    draft_contribution_plan,
    explain_file,
)
from ai.model_client import OllamaConnectionError

SOURCE = (
    '"""Entry point helpers."""\n'
    "\n"
    "class Greeter:\n"
    "    def hello(self):\n"
    "        return 'hi'\n"
    "\n"
    "def run(x):\n"
    "    return x\n"
)


def make_module():
    return analyze_source("app/main.py", SOURCE)


def make_issue():
    return IssueInfo(
        number=7,
        title="Fix app/main.py crash",
        body="It crashes in app/main.py. It should not.",
        labels=["bug"],
        html_url="https://github.com/acme/demo/issues/7",
        repo_full_name="acme/demo",
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
# File explanation
# --------------------------------------------------------------------------- #
def test_no_client_uses_fallback_with_real_facts():
    result = explain_file("app/main.py", module=make_module(), client=None)
    assert result.used_model is False
    assert "No local model" in (result.error or "")
    assert any("Greeter" in s for s in result.key_symbols)
    assert any("run" in s for s in result.key_symbols)


def test_valid_json_is_parsed():
    module = make_module()
    payload = {
        "summary": "This file defines a Greeter class and a run function.",
        "key_symbols": ["`Greeter` manages greetings", "`run` executes work"],
        "how_it_might_relate": "The issue names this file.",
        "beginner_notes": ["Read the class first."],
        "limitations": ["Runtime behaviour unknown."],
    }
    result = explain_file(
        "app/main.py", module=module, client=FakeClient(reply=json.dumps(payload))
    )
    assert result.parsed_ok is True
    assert result.used_model is True
    assert "Greeter" in result.summary
    assert result.dropped_symbols == []


def test_invented_symbols_are_dropped():
    module = make_module()
    payload = {
        "summary": "s",
        "key_symbols": ["`Greeter` is real", "`GhostClass` is invented"],
    }
    result = explain_file(
        "app/main.py", module=module, client=FakeClient(reply=json.dumps(payload))
    )
    assert any("Greeter" in s for s in result.key_symbols)
    assert any("GhostClass" in s for s in result.dropped_symbols)
    assert any("do not exist" in lim for lim in result.limitations)


def test_invalid_json_falls_back_and_keeps_raw():
    result = explain_file(
        "app/main.py", module=make_module(), client=FakeClient(reply="not json")
    )
    assert result.parsed_ok is False
    assert result.raw_text == "not json"
    assert result.limitations


def test_model_error_falls_back():
    result = explain_file(
        "app/main.py",
        module=make_module(),
        client=FakeClient(raise_error=OllamaConnectionError("down")),
    )
    assert result.used_model is False
    assert "down" in (result.error or "")


def test_file_evidence_is_bounded():
    evidence = build_file_evidence("app/main.py", make_module(), snippet="x" * 20000)
    assert len(evidence) <= MAX_EVIDENCE_CHARS + 64
    assert "truncated" in evidence


def test_file_prompt_marks_evidence_untrusted():
    client = FakeClient(reply=json.dumps({"summary": "s"}))
    explain_file("app/main.py", module=make_module(), client=client)
    assert "UNTRUSTED" in client.calls[0]["prompt"]
    assert client.calls[0]["system"]


def test_file_explanation_has_no_score_field():
    result = explain_file("app/main.py", module=make_module(), client=None)
    assert "match_score" not in result.to_dict()


# --------------------------------------------------------------------------- #
# Plan drafting
# --------------------------------------------------------------------------- #
def make_guide():
    issue = make_issue()
    link = IssueLinkResult(repo_full_name="acme/demo", issue_number=7)
    return build_contribution_guide(issue, link, ContributorProfile(skills=["python"]))


def test_no_client_plan_falls_back_to_guide():
    guide = make_guide()
    plan = draft_contribution_plan(guide, ContributorProfile(), client=None)
    assert plan.used_model is False
    assert plan.plan_steps == list(guide.investigation_sequence)[:10]
    assert plan.is_hypothesis is True


def test_valid_plan_json_is_parsed():
    payload = {
        "plan_steps": ["Reproduce", "Locate", "Fix", "Test"],
        "questions_to_ask": ["Which file?"],
        "things_to_watch": ["Unrelated edits"],
        "limitations": ["No tests found"],
    }
    plan = draft_contribution_plan(
        make_guide(), ContributorProfile(), client=FakeClient(reply=json.dumps(payload))
    )
    assert plan.parsed_ok is True
    assert plan.plan_steps == ["Reproduce", "Locate", "Fix", "Test"]


def test_plan_without_steps_falls_back():
    plan = draft_contribution_plan(
        make_guide(),
        ContributorProfile(),
        client=FakeClient(reply=json.dumps({"plan_steps": []})),
    )
    assert plan.parsed_ok is False
    assert plan.plan_steps  # came from the deterministic guide


def test_plan_model_error_falls_back():
    plan = draft_contribution_plan(
        make_guide(),
        ContributorProfile(),
        client=FakeClient(raise_error=OllamaConnectionError("down")),
    )
    assert plan.used_model is False
    assert "down" in (plan.error or "")


def test_plan_prompt_marks_guide_untrusted():
    client = FakeClient(reply=json.dumps({"plan_steps": ["a"]}))
    draft_contribution_plan(make_guide(), ContributorProfile(), client=client)
    assert "UNTRUSTED" in client.calls[0]["prompt"]
    assert "match_score" not in json.dumps(
        draft_contribution_plan(make_guide(), ContributorProfile(), client=None).to_dict()
    )
