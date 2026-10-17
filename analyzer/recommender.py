"""Deterministic contribution recommendation engine (Phase 3).

Given a contributor profile and a list of **real** issues, this module produces
a ranked, evidence-backed match for each issue. It is deliberately
deterministic: no randomness, no network, and no model calls.

Design rules
------------
* **Score and confidence are separate.** ``match_score`` (0–100) measures how
  well an issue fits the profile. ``confidence`` (0–1) measures how much
  evidence we actually had. A well-matched issue can still be low-confidence
  if its description is empty.
* **No fabrication.** Every piece of evidence cites something present in the
  supplied issue (a label, a title token, the URL) or in the profile. Nothing
  is invented.
* **Configurable weights.** All component weights live in
  :class:`MatchWeights`; scores renormalise automatically if weights change.
* **Stable ordering.** Equal scores break ties by issue number, so results are
  reproducible.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .issue_client import BEGINNER_LABELS, IssueInfo

EXPERIENCE_LEVELS = ("beginner", "intermediate", "advanced")
AVAILABILITY_LEVELS = ("few_hours", "weekend", "ongoing")

# Labels that make an issue a poor fit for a first contribution.
STEEP_LABELS = (
    "breaking change",
    "hard",
    "epic",
    "needs investigation",
    "performance",
    "security",
    "refactor",
    "urgent",
    "production",
)

# Words in a title/body that suggest a harder change.
HARD_WORDS = (
    "race condition",
    "deadlock",
    "memory leak",
    "regression",
    "refactor",
    "migration",
    "concurrency",
    "async",
    "performance",
    "optimize",
    "benchmark",
    "breaking",
    "latency",
    "thread",
    "cache invalidation",
)

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9+.#_-]*")


# --------------------------------------------------------------------------- #
# Contributor profile
# --------------------------------------------------------------------------- #
@dataclass
class ContributorProfile:
    """A small, local description of the person seeking a first contribution.

    Everything here is user-supplied text. It is tokenised for matching only
    and is never sent anywhere except to a local model the user explicitly runs.
    """

    skills: List[str] = field(default_factory=list)
    interests: List[str] = field(default_factory=list)
    experience: str = "beginner"
    availability: str = "few_hours"
    name: str = ""

    def __post_init__(self) -> None:
        self.skills = self._clean(self.skills)
        self.interests = self._clean(self.interests)
        if self.experience not in EXPERIENCE_LEVELS:
            self.experience = "beginner"
        if self.availability not in AVAILABILITY_LEVELS:
            self.availability = "few_hours"

    @staticmethod
    def _clean(values: Iterable[str]) -> List[str]:
        cleaned: List[str] = []
        for value in values or []:
            text = str(value).strip()
            if text and text.lower() not in {c.lower() for c in cleaned}:
                cleaned.append(text)
        return cleaned

    @classmethod
    def from_text(
        cls,
        skills_text: str = "",
        interests_text: str = "",
        experience: str = "beginner",
        availability: str = "few_hours",
        name: str = "",
    ) -> "ContributorProfile":
        """Parse comma/space separated UI input into a profile."""
        def split(raw: str) -> List[str]:
            parts: List[str] = []
            for chunk in re.split(r"[,;\n]+", raw or ""):
                for word in chunk.split():
                    if word:
                        parts.append(word)
            return parts

        return cls(
            skills=split(skills_text),
            interests=split(interests_text),
            experience=experience,
            availability=availability,
            name=name,
        )

    @property
    def skill_phrases(self) -> List[str]:
        return [s.lower() for s in self.skills]

    @property
    def interest_phrases(self) -> List[str]:
        return [s.lower() for s in self.interests]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "skills": list(self.skills),
            "interests": list(self.interests),
            "experience": self.experience,
            "availability": self.availability,
        }


# --------------------------------------------------------------------------- #
# Difficulty estimation (pure, issue-only)
# --------------------------------------------------------------------------- #
@dataclass
class DifficultyEstimate:
    level: str  # beginner | moderate | challenging | unknown
    score: float  # 0.0 (easy) .. 1.0 (hard)
    signals: List[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return {
            "beginner": "Beginner-friendly",
            "moderate": "Moderate",
            "challenging": "Challenging",
            "unknown": "Unclear from the issue alone",
        }.get(self.level, self.level)

    def to_dict(self) -> Dict[str, Any]:
        return {"level": self.level, "score": self.score, "signals": list(self.signals)}


def _difficulty_words(text: str) -> int:
    lowered = text.lower()
    return sum(1 for word in HARD_WORDS if word in lowered)


def estimate_difficulty(issue: IssueInfo) -> DifficultyEstimate:
    """Estimate effort from the issue's own text and labels only.

    This is a heuristic, not a promise. Signals are listed so a reviewer can
    see exactly why an estimate was produced.
    """
    signals: List[str] = []
    score = 0.5  # neutral default when evidence is thin
    labels = issue.labels_lower
    body_len = len(issue.body.strip())
    combined = f"{issue.title}\n{issue.body}".strip()

    beginner_labels = [label for label in labels if label in BEGINNER_LABELS]
    steep_labels = [label for label in labels if label in STEEP_LABELS]
    hard_hits = _difficulty_words(combined)

    if beginner_labels:
        score -= 0.35
        signals.append("label(s) present: " + ", ".join(beginner_labels))
    if steep_labels:
        score += 0.30
        signals.append("demanding label(s): " + ", ".join(steep_labels))
    if hard_hits:
        score += min(0.10 * hard_hits, 0.30)
        signals.append(f"{hard_hits} hard-topic term(s) in the text")

    if body_len == 0:
        signals.append("no description provided — scope cannot be judged")
        score = max(score, 0.45)
    elif body_len < 300:
        score -= 0.10
        signals.append("short description")
    elif body_len > 1500:
        score += 0.10
        signals.append("long description")

    if issue.comments >= 10:
        score += 0.05
        signals.append(f"{issue.comments} comments — active/older discussion")

    score = max(0.0, min(1.0, score))

    if beginner_labels and score < 0.4:
        level = "beginner"
    elif not beginner_labels and not steep_labels and body_len == 0:
        level = "unknown"
    elif score < 0.45:
        level = "beginner"
    elif score < 0.7:
        level = "moderate"
    else:
        level = "challenging"

    return DifficultyEstimate(level=level, score=round(score, 3), signals=signals)


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #
@dataclass
class MatchWeights:
    """Relative importance of each scoring component.

    Weights are renormalised to sum to 1.0, so changing one does not require
    manually rebalancing the rest.
    """

    skill: float = 0.30
    label_fit: float = 0.20
    difficulty_fit: float = 0.20
    scope: float = 0.15
    interest: float = 0.10
    recency: float = 0.05

    def as_normalised(self) -> Dict[str, float]:
        weights = {
            "skill": self.skill,
            "label_fit": self.label_fit,
            "difficulty_fit": self.difficulty_fit,
            "scope": self.scope,
            "interest": self.interest,
            "recency": self.recency,
        }
        positive = {k: max(0.0, v) for k, v in weights.items()}
        total = sum(positive.values())
        if total <= 0:
            return {k: 0.0 for k in positive}
        return {k: v / total for k, v in positive.items()}


def _tokens(text: str) -> List[str]:
    return _TOKEN_RE.findall((text or "").lower())


def _phrase_in_text(phrase: str, haystack_lower: str, tokens: Sequence[str]) -> bool:
    phrase = (phrase or "").strip().lower()
    if not phrase:
        return False
    if " " in phrase:
        return phrase in haystack_lower
    return phrase in tokens


def _issue_text(issue: IssueInfo) -> str:
    return f"{issue.title}\n{' '.join(issue.labels)}\n{issue.body}".lower()


def _parse_timestamp(value: str) -> Optional[datetime]:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _skill_score(profile: ContributorProfile, issue: IssueInfo) -> tuple[float, List[str]]:
    if not profile.skills:
        return 0.5, ["no skills declared — neutral skill score"]
    haystack = _issue_text(issue)
    tokens = set(_tokens(haystack))
    matched = [s for s in profile.skills if _phrase_in_text(s, haystack, tokens)]
    score = len(matched) / len(profile.skills)
    evidence = (
        [f"skill '{s}' appears in this issue" for s in matched]
        if matched
        else ["none of your declared skills were mentioned in this issue"]
    )
    return score, evidence


def _interest_score(profile: ContributorProfile, issue: IssueInfo) -> tuple[float, List[str]]:
    if not profile.interests:
        return 0.5, ["no interests declared — neutral interest score"]
    haystack = _issue_text(issue)
    tokens = set(_tokens(haystack))
    matched = [i for i in profile.interests if _phrase_in_text(i, haystack, tokens)]
    score = len(matched) / len(profile.interests)
    evidence = (
        [f"interest '{i}' appears in this issue" for i in matched]
        if matched
        else ["none of your declared interests were mentioned"]
    )
    return score, evidence


def _label_fit_score(profile: ContributorProfile, issue: IssueInfo) -> tuple[float, List[str]]:
    labels = issue.labels_lower
    beginner = [l for l in labels if l in BEGINNER_LABELS]
    steep = [l for l in labels if l in STEEP_LABELS]

    if steep:
        score = 0.15
        evidence = [f"label '{steep[0]}' usually signals a harder change"]
    elif beginner:
        score = 1.0
        evidence = [f"beginner label present: '{beginner[0]}'"]
    elif labels:
        score = 0.55
        evidence = ["labels present but none mark it as beginner-friendly"]
    else:
        score = 0.45
        evidence = ["no labels — nothing to judge label fit from"]

    if profile.experience == "advanced" and beginner:
        score = min(score, 0.7)
        evidence.append("beginner-labelled issues are usually easy for advanced contributors")
    return score, evidence


def _difficulty_fit_score(
    profile: ContributorProfile, difficulty: DifficultyEstimate
) -> tuple[float, List[str]]:
    target = {"beginner": 0.15, "intermediate": 0.5, "advanced": 0.75}.get(
        profile.experience, 0.3
    )
    delta = abs(difficulty.score - target)
    score = max(0.0, 1.0 - (delta * 1.6))
    evidence = [
        f"your level '{profile.experience}' vs estimated effort "
        f"'{difficulty.label.lower()}' (delta {delta:.2f})"
    ]
    if difficulty.signals:
        evidence.append("effort signals: " + "; ".join(difficulty.signals[:3]))
    return score, evidence


def _scope_score(profile: ContributorProfile, issue: IssueInfo) -> tuple[float, List[str]]:
    """Prefer self-contained issues — good first contributions are bounded."""
    evidence: List[str] = []
    score = 0.5

    body_len = len(issue.body.strip())
    if body_len == 0:
        evidence.append("no description — scope unknown")
    elif body_len < 500:
        score += 0.25
        evidence.append("short description suggests a bounded change")
    elif body_len > 1500:
        score -= 0.20
        evidence.append("long description suggests a larger change")

    if issue.path_hint:
        score += 0.15
        evidence.append(f"title hints at a specific file: {issue.path_hint}")

    if issue.comments == 0:
        score += 0.10
        evidence.append("no discussion yet — an untapped, self-contained task")
    elif issue.comments > 20:
        score -= 0.15
        evidence.append(f"{issue.comments} comments — likely a long-running thread")

    if profile.availability == "few_hours" and body_len > 1500:
        score -= 0.10
        evidence.append("large change but you only have a few hours")

    return max(0.0, min(1.0, score)), evidence


def _recency_score(issue: IssueInfo, now: Optional[datetime] = None) -> tuple[float, List[str]]:
    ref = now or datetime.now(timezone.utc)
    stamp = _parse_timestamp(issue.updated_at) or _parse_timestamp(issue.created_at)
    if stamp is None:
        return 0.4, ["no usable timestamp — neutral recency score"]
    days = (ref - stamp).total_seconds() / 86400.0
    if days < 0:
        days = 0.0
    score = max(0.0, 1.0 - (days / 120.0))
    return score, [f"last updated {int(days)} day(s) before evaluation time"]


@dataclass
class IssueRecommendation:
    issue: IssueInfo
    match_score: float  # 0..100
    confidence: float  # 0..1 — evidence sufficiency, NOT part of match_score
    difficulty: DifficultyEstimate
    components: Dict[str, float] = field(default_factory=dict)
    evidence: List[str] = field(default_factory=list)
    cautions: List[str] = field(default_factory=list)
    rank: int = 0

    @property
    def best_component(self) -> Optional[str]:
        if not self.components:
            return None
        return max(self.components.items(), key=lambda kv: (kv[1], kv[0]))[0]

    @property
    def url(self) -> str:
        return self.issue.html_url

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rank": self.rank,
            "issue": self.issue.to_dict(),
            "match_score": self.match_score,
            "confidence": self.confidence,
            "difficulty": self.difficulty.to_dict(),
            "components": dict(self.components),
            "evidence": list(self.evidence),
            "cautions": list(self.cautions),
            "url": self.url,
        }


def compute_confidence(issue: IssueInfo, difficulty: DifficultyEstimate) -> float:
    """How much evidence backs this recommendation (0..1).

    Deliberately independent of ``match_score``: an empty issue can match on
    labels alone, but we should not pretend we understand it well.
    """
    score = 0.20
    if issue.body.strip():
        score += 0.35
    elif difficulty.level == "unknown":
        score += 0.0
    if issue.labels:
        score += 0.15
    if len(issue.title) >= 25:
        score += 0.10
    if _parse_timestamp(issue.updated_at) or _parse_timestamp(issue.created_at):
        score += 0.10
    if issue.path_hint:
        score += 0.10
    return round(max(0.0, min(1.0, score)), 2)


def score_issue(
    issue: IssueInfo,
    profile: ContributorProfile,
    weights: Optional[MatchWeights] = None,
    now: Optional[datetime] = None,
) -> IssueRecommendation:
    """Score one issue against a profile. Pure and deterministic."""
    weights = weights or MatchWeights()
    norm = weights.as_normalised()

    skill_raw, skill_ev = _skill_score(profile, issue)
    label_raw, label_ev = _label_fit_score(profile, issue)
    difficulty = estimate_difficulty(issue)
    diff_raw, diff_ev = _difficulty_fit_score(profile, difficulty)
    scope_raw, scope_ev = _scope_score(profile, issue)
    interest_raw, interest_ev = _interest_score(profile, issue)
    recency_raw, recency_ev = _recency_score(issue, now=now)

    raw = {
        "skill": skill_raw,
        "label_fit": label_raw,
        "difficulty_fit": diff_raw,
        "scope": scope_raw,
        "interest": interest_raw,
        "recency": recency_raw,
    }

    weighted = sum(raw[name] * norm[name] for name in norm)
    match_score = round(max(0.0, min(1.0, weighted)) * 100.0, 1)
    confidence = compute_confidence(issue, difficulty)

    evidence: List[str] = []
    for name in ("skill", "label_fit", "difficulty_fit", "scope", "interest", "recency"):
        pts = raw[name] * norm[name] * 100.0
        for line in _ev_for(name, skill_ev, label_ev, diff_ev, scope_ev, interest_ev, recency_ev):
            evidence.append(f"[{name} {raw[name]:.2f} → {pts:.1f} pts] {line}")

    cautions: List[str] = []
    if not issue.body.strip():
        cautions.append("Issue has no description — scope is unverified.")
    if confidence < 0.5:
        cautions.append("Low evidence: treat this match as a starting point, not a verdict.")
    if not issue.html_url:
        cautions.append("No issue URL supplied, so this entry cannot be verified.")
    if issue.is_pull_request:
        cautions.append("This entry is a pull request, not an issue.")

    return IssueRecommendation(
        issue=issue,
        match_score=match_score,
        confidence=confidence,
        difficulty=difficulty,
        components={k: round(v, 4) for k, v in raw.items()},
        evidence=evidence,
        cautions=cautions,
    )


def _ev_for(
    name: str,
    skill_ev: List[str],
    label_ev: List[str],
    diff_ev: List[str],
    scope_ev: List[str],
    interest_ev: List[str],
    recency_ev: List[str],
) -> List[str]:
    mapping = {
        "skill": skill_ev,
        "label_fit": label_ev,
        "difficulty_fit": diff_ev,
        "scope": scope_ev,
        "interest": interest_ev,
        "recency": recency_ev,
    }
    return mapping.get(name, [])


def recommend(
    issues: Sequence[IssueInfo],
    profile: ContributorProfile,
    weights: Optional[MatchWeights] = None,
    top: Optional[int] = None,
    now: Optional[datetime] = None,
) -> List[IssueRecommendation]:
    """Score and rank issues. Deterministic: score desc, then issue number asc."""
    weights = weights or MatchWeights()
    scored = [score_issue(issue, profile, weights=weights, now=now) for issue in issues]
    scored.sort(key=lambda rec: (-rec.match_score, rec.issue.number))
    for index, rec in enumerate(scored, start=1):
        rec.rank = index
    if top is not None:
        return scored[: max(0, int(top))]
    return scored


def format_recommendation(rec: IssueRecommendation) -> str:
    """Plain-text rendering for logs or a compact UI line."""
    url = rec.url or "(no URL supplied)"
    return (
        f"#{rec.issue.number} — {rec.issue.title}\n"
        f"  score {rec.match_score:.1f}/100 · confidence {rec.confidence:.2f} · "
        f"{rec.difficulty.label} · {url}"
    )
