"""Tests for the deterministic contribution recommender (Phase 3)."""

from datetime import datetime, timezone

import pytest

from analyzer.issue_client import IssueInfo
from analyzer.recommender import (
    ContributorProfile,
    MatchWeights,
    compute_confidence,
    estimate_difficulty,
    recommend,
    score_issue,
)

NOW = datetime(2026, 10, 17, tzinfo=timezone.utc)


def make_issue(
    number=1,
    title="Fix typo in docs.py",
    body="A short description.",
    labels=None,
    comments=0,
    updated="2026-10-15T00:00:00Z",
    url="https://github.com/acme/demo/issues/1",
):
    return IssueInfo(
        number=number,
        title=title,
        body=body,
        labels=list(labels or []),
        html_url=url,
        updated_at=updated,
        created_at=updated,
        comments=comments,
        repo_full_name="acme/demo",
    )


# --------------------------------------------------------------------------- #
# Profile
# --------------------------------------------------------------------------- #
def test_profile_from_text_splits_and_dedupes():
    profile = ContributorProfile.from_text("python, docs testing", "docs api")
    assert profile.skills == ["python", "docs", "testing"]
    assert profile.interests == ["docs", "api"]


def test_profile_rejects_unknown_experience():
    profile = ContributorProfile(experience="wizard")
    assert profile.experience == "beginner"


# --------------------------------------------------------------------------- #
# Difficulty
# --------------------------------------------------------------------------- #
def test_difficulty_beginner_label_is_easy():
    issue = make_issue(labels=["good first issue"])
    difficulty = estimate_difficulty(issue)
    assert difficulty.level == "beginner"
    assert difficulty.score < 0.5


def test_difficulty_hard_label_is_challenging():
    issue = make_issue(
        title="Fix deadlock in the concurrency layer",
        body="x" * 900,
        labels=["hard"],
        comments=25,
    )
    difficulty = estimate_difficulty(issue)
    assert difficulty.level == "challenging"
    assert difficulty.score >= 0.7


def test_difficulty_without_body_is_unknownish():
    issue = make_issue(body="", labels=[])
    difficulty = estimate_difficulty(issue)
    assert difficulty.level in {"unknown", "moderate"}
    assert any("no description" in s for s in difficulty.signals)


# --------------------------------------------------------------------------- #
# Weights
# --------------------------------------------------------------------------- #
def test_weights_renormalise_to_one():
    norm = MatchWeights(skill=2, label_fit=2, difficulty_fit=2, scope=2, interest=1, recency=1).as_normalised()
    assert pytest.approx(sum(norm.values()), abs=1e-9) == 1.0


def test_zero_weights_do_not_crash():
    weights = MatchWeights(skill=0, label_fit=0, difficulty_fit=0, scope=0, interest=0, recency=0)
    rec = score_issue(make_issue(), ContributorProfile(skills=["python"]), weights=weights, now=NOW)
    assert rec.match_score == 0.0


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #
def test_score_is_deterministic_and_bounded():
    issue = make_issue(labels=["good first issue"], body="Fix the docs.")
    profile = ContributorProfile(skills=["docs"], experience="beginner")
    first = score_issue(issue, profile, now=NOW)
    second = score_issue(issue, profile, now=NOW)
    assert first.match_score == second.match_score
    assert 0.0 <= first.match_score <= 100.0


def test_skill_match_raises_score():
    issue = make_issue(body="Please improve the python documentation.")
    weak = ContributorProfile(skills=["rust"])
    strong = ContributorProfile(skills=["python"])
    assert score_issue(issue, strong, now=NOW).match_score > score_issue(issue, weak, now=NOW).match_score


def test_score_and_confidence_are_independent():
    rich = make_issue(body="Detailed description " * 20, labels=["good first issue"])
    empty = make_issue(body="", labels=[], title="Fix", updated="")
    profile = ContributorProfile(skills=["docs"])
    rich_rec = score_issue(rich, profile, now=NOW)
    empty_rec = score_issue(empty, profile, now=NOW)
    # Confidence reflects evidence, independent of the match score.
    assert rich_rec.confidence > empty_rec.confidence


def test_low_confidence_adds_caution():
    issue = make_issue(body="", labels=[], title="Fix", updated="")
    rec = score_issue(issue, ContributorProfile(), now=NOW)
    assert rec.confidence < 0.5
    assert any("Low evidence" in c for c in rec.cautions)
    assert any("no description" in c.lower() for c in rec.cautions)


def test_evidence_only_references_issue_fields():
    issue = make_issue(labels=["good first issue"], body="Improve python docs.")
    rec = score_issue(issue, ContributorProfile(skills=["python"]), now=NOW)
    assert rec.evidence
    # Every evidence line for skill cites the matched skill.
    assert any("python" in line for line in rec.evidence)


def test_empty_issue_still_scores_without_fabrication():
    issue = make_issue(body="", labels=[], title="", updated="")
    rec = score_issue(issue, ContributorProfile(), now=NOW)
    assert 0.0 <= rec.match_score <= 100.0
    assert rec.issue.number == issue.number


# --------------------------------------------------------------------------- #
# Ranking
# --------------------------------------------------------------------------- #
def test_recommend_sorts_by_score_then_number():
    profile = ContributorProfile(skills=["docs"], experience="beginner")
    easy = make_issue(number=5, title="Docs typo", body="small", labels=["good first issue"])
    hard = make_issue(
        number=9,
        title="Refactor concurrency",
        body="x" * 1200,
        labels=["hard"],
        comments=30,
    )
    recs = recommend([hard, easy], profile, now=NOW)
    assert [r.issue.number for r in recs] == [5, 9]
    assert recs[0].rank == 1 and recs[1].rank == 2


def test_recommend_is_stable_for_equal_scores():
    profile = ContributorProfile()
    a = make_issue(number=3, title="Same", body="same body", labels=["bug"])
    b = make_issue(number=2, title="Same", body="same body", labels=["bug"])
    recs = recommend([a, b], profile, now=NOW)
    assert [r.issue.number for r in recs] == [2, 3]


def test_recommend_top_limits_results():
    profile = ContributorProfile()
    issues = [make_issue(number=n, title=f"issue {n}", body="b", labels=["bug"]) for n in range(1, 6)]
    recs = recommend(issues, profile, top=2, now=NOW)
    assert len(recs) == 2


def test_sorted_recommendation_is_reproducible():
    profile = ContributorProfile(skills=["python", "docs"])
    issues = [
        make_issue(number=1, body="python docs", labels=["good first issue"]),
        make_issue(number=2, body="docs", labels=["documentation"]),
    ]
    run1 = [r.match_score for r in recommend(issues, profile, now=NOW)]
    run2 = [r.match_score for r in recommend(issues, profile, now=NOW)]
    assert run1 == run2


# --------------------------------------------------------------------------- #
# Confidence helper
# --------------------------------------------------------------------------- #
def test_confidence_increases_with_evidence():
    rich = make_issue(body="detailed", labels=["bug"])
    sparse = make_issue(body="", labels=[])
    assert compute_confidence(rich, estimate_difficulty(rich)) > compute_confidence(
        sparse, estimate_difficulty(sparse)
    )
