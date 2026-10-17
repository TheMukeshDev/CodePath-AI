"""Tests for the deterministic contribution guide (Phase 4)."""

from analyzer.contribution_guide import (
    classify_issue,
    build_contribution_guide,
    format_guide,
)
from analyzer.file_scanner import analyze_source
from analyzer.github_client import RepoMetadata
from analyzer.issue_client import IssueInfo
from analyzer.issue_linker import IssueLinkResult, link_issue_to_files


def make_issue(number=7, title="Fix crash", body="", labels=None):
    return IssueInfo(
        number=number,
        title=title,
        body=body,
        labels=list(labels or []),
        html_url=f"https://github.com/acme/demo/issues/{number}",
        repo_full_name="acme/demo",
        state="open",
        author="alice",
        comments=2,
    )


def make_meta():
    return RepoMetadata(
        owner="acme",
        name="demo",
        full_name="acme/demo",
        default_branch="main",
        commit_sha="abc123",
        html_url="https://github.com/acme/demo",
        language="Python",
    )


def make_modules():
    return {
        "app/main.py": analyze_source(
            "app/main.py", '"""Entry point."""\ndef run():\n    return 1\n'
        )
    }


def empty_link():
    return IssueLinkResult(repo_full_name="acme/demo", issue_number=7)


def link_for(body):
    issue = make_issue(body=body)
    return link_issue_to_files(issue, make_modules(), repo_meta=make_meta())


# --------------------------------------------------------------------------- #
# Classification
# --------------------------------------------------------------------------- #
def test_classify_bug_docs_feature():
    assert classify_issue(make_issue(title="Fix crash on startup")) == "bug"
    assert classify_issue(make_issue(title="Update README typo")) == "docs"
    assert classify_issue(make_issue(title="Add support for yaml")) == "feature"
    assert classify_issue(make_issue(title="Something odd")) == "general"


# --------------------------------------------------------------------------- #
# Acceptance criteria extraction
# --------------------------------------------------------------------------- #
def test_checkbox_criteria_extracted():
    issue = make_issue(
        body="Acceptance criteria:\n- [ ] No crash on empty input\n- [ ] Tests added\n"
    )
    guide = build_contribution_guide(issue, empty_link())
    texts = [a.text for a in guide.acceptance_criteria]
    assert "No crash on empty input" in texts
    assert "Tests added" in texts
    assert all(a.source for a in guide.acceptance_criteria)


def test_must_should_sentences_extracted():
    issue = make_issue(body="The parser should ignore blank lines. It must not raise.")
    guide = build_contribution_guide(issue, empty_link())
    texts = " ".join(a.text for a in guide.acceptance_criteria)
    assert "should ignore blank lines" in texts
    assert "must not raise" in texts


def test_no_criteria_sets_note_and_unknown():
    issue = make_issue(body="Something is broken somewhere.")
    guide = build_contribution_guide(issue, empty_link())
    assert guide.acceptance_criteria == []
    assert "did not state explicit acceptance criteria" in guide.acceptance_note
    assert any("definition of done" in u.lower() for u in guide.unknowns)


def test_missing_body_reports_no_description():
    issue = make_issue(body="")
    guide = build_contribution_guide(issue, empty_link())
    assert "no written description" in guide.problem.lower()
    assert any("expected behaviour" in u.lower() for u in guide.unknowns)


# --------------------------------------------------------------------------- #
# Facts vs hypotheses
# --------------------------------------------------------------------------- #
def test_facts_include_real_issue_fields():
    issue = make_issue(body="Crash in app/main.py", labels=["bug"])
    guide = build_contribution_guide(
        issue, link_for("Crash in app/main.py"), repo_meta=make_meta(), modules=make_modules()
    )
    joined = " ".join(guide.facts)
    assert "#7" in joined
    assert "open" in joined
    assert "Python" in joined


def test_keyword_only_links_become_hypotheses():
    from analyzer.issue_linker import LinkedFile

    issue = make_issue(body="Something is off.")
    link = IssueLinkResult(
        repo_full_name="acme/demo",
        issue_number=7,
        linked_files=[
            LinkedFile(
                path="app/main.py",
                module_name="app.main",
                score=8.0,
                confidence="likely",
            )
        ],
    )
    guide = build_contribution_guide(issue, link)
    assert any("may be involved" in h for h in guide.hypotheses)
    assert any(r.is_hypothesis for r in guide.risks)


def test_untrusted_text_risk_always_present():
    issue = make_issue(body="x")
    guide = build_contribution_guide(issue, empty_link())
    assert any("untrusted" in r.text.lower() for r in guide.risks)


def test_objective_derived_from_title():
    issue = make_issue(title="Fix crash", body="x")
    guide = build_contribution_guide(issue, empty_link())
    assert "Fix crash" in guide.objective


# --------------------------------------------------------------------------- #
# Investigation sequence adapts to kind
# --------------------------------------------------------------------------- #
def test_bug_guide_starts_with_reproduction():
    issue = make_issue(title="Crash on startup", body="It crashes.", labels=["bug"])
    guide = build_contribution_guide(issue, empty_link())
    assert "Reproduce" in guide.investigation_sequence[0]


def test_docs_guide_starts_with_reading_docs():
    issue = make_issue(title="Fix README typo", body="Typo.")
    guide = build_contribution_guide(issue, empty_link())
    assert "documentation" in guide.investigation_sequence[0].lower()


def test_confirmed_file_named_in_sequence():
    issue = make_issue(title="Fix bug", body="Broken in app/main.py.")
    link = link_for("Broken in app/main.py.")
    guide = build_contribution_guide(issue, link)
    assert any("app/main.py" in step for step in guide.investigation_sequence)


# --------------------------------------------------------------------------- #
# Formatting
# --------------------------------------------------------------------------- #
def test_format_guide_has_sections():
    issue = make_issue(title="Fix crash", body="Crash in app/main.py.")
    guide = build_contribution_guide(issue, link_for("Crash in app/main.py."))
    text = format_guide(guide)
    assert "## Problem" in text
    assert "## Acceptance criteria" in text
    assert "## Candidate files" in text
    assert "## Risks" in text


def test_guide_to_dict_is_serialisable():
    import json

    issue = make_issue(title="Fix crash", body="Crash in app/main.py.")
    guide = build_contribution_guide(issue, link_for("Crash in app/main.py."))
    json.dumps(guide.to_dict())


def test_profile_name_note_only_with_profile():
    issue = make_issue(title="Fix crash", body="x")
    from analyzer.recommender import ContributorProfile

    guide = build_contribution_guide(
        issue, empty_link(), ContributorProfile(name="Sam", skills=["python"]), readme="hi"
    )
    assert any("Sam" in note for note in guide.notes)
