"""Optional AI explanation for a single contribution candidate (Phase 3).

This module wraps a **local** Ollama model. Every function has a deterministic
fallback, so the feature degrades gracefully when Ollama is absent or the model
returns something unusable.

Non-negotiables
---------------
* **No score.** This module never produces, edits, or exposes a numeric match
  score. The deterministic score from :mod:`analyzer.recommender` is the single
  source of truth; the model only explains in prose.
* **No fabrication.** Model output is passed through a validator. ``relevant_files``
  entries are filtered against paths the caller actually knows about, so an
  invented file path is dropped and reported rather than surfaced.
* **Untrusted text.** Issue title/body/labels and profile text are embedded in
  explicitly delimited untrusted blocks; the system prompt instructs the model
  to treat them as data, never as instructions.
* **Bounded.** Evidence is truncated to a fixed budget before it is sent.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from analyzer.issue_client import IssueInfo
from analyzer.recommender import ContributorProfile, IssueRecommendation
from ai.model_client import OllamaClient, OllamaError
from ai.prompts import ISSUE_SYSTEM_PROMPT, build_issue_explanation_prompt

MAX_EVIDENCE_CHARS = 6000
MAX_FIELD_CHARS = 600
MAX_ITEMS = 12
SCORE_NOTE = "Deterministic match score is computed separately and is not affected by this text."


# --------------------------------------------------------------------------- #
# Result container
# --------------------------------------------------------------------------- #
@dataclass
class IssueExplanation:
    issue_number: int
    summary: str = ""
    why_it_matches: str = ""
    skills_to_use: List[str] = field(default_factory=list)
    skills_to_learn: List[str] = field(default_factory=list)
    suggested_first_steps: List[str] = field(default_factory=list)
    relevant_files: List[str] = field(default_factory=list)
    limitations: List[str] = field(default_factory=list)
    parsed_ok: bool = False
    used_model: bool = False
    error: Optional[str] = None
    raw_text: str = ""
    dropped_files: List[str] = field(default_factory=list)

    @property
    def score_note(self) -> str:
        return SCORE_NOTE

    def to_dict(self) -> Dict[str, Any]:
        return {
            "issue_number": self.issue_number,
            "summary": self.summary,
            "why_it_matches": self.why_it_matches,
            "skills_to_use": list(self.skills_to_use),
            "skills_to_learn": list(self.skills_to_learn),
            "suggested_first_steps": list(self.suggested_first_steps),
            "relevant_files": list(self.relevant_files),
            "limitations": list(self.limitations),
            "parsed_ok": self.parsed_ok,
            "used_model": self.used_model,
            "error": self.error,
            "dropped_files": list(self.dropped_files),
            "score_note": SCORE_NOTE,
        }


# --------------------------------------------------------------------------- #
# Input sanitisation
# --------------------------------------------------------------------------- #
def _clean_text(value: Any, limit: int = MAX_FIELD_CHARS) -> str:
    if value is None:
        return ""
    text = str(value).replace("\x00", " ").strip()
    text = re.sub(r"\s+", " ", text)
    return text[:limit]


def _clean_list(value: Any, limit: int = MAX_ITEMS, item_max: int = 200) -> List[str]:
    if not isinstance(value, (list, tuple)):
        return []
    out: List[str] = []
    for item in value:
        text = _clean_text(item, item_max)
        if text and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def build_issue_evidence(
    issue: IssueInfo,
    profile: ContributorProfile,
    recommendation: Optional[IssueRecommendation] = None,
) -> str:
    """Compose the bounded, untrusted evidence block for one issue.

    Only real, supplied fields are included. Nothing is inferred here.
    """
    labels = ", ".join(issue.labels) or "(none)"
    difficulty = recommendation.difficulty.label if recommendation else "unknown"
    lines = [
        "## Issue",
        f"repository: {issue.repo_full_name or '(unspecified)'}",
        f"number: {issue.number}",
        f"title: {issue.title}",
        f"url: {issue.html_url or '(none supplied)'}",
        f"state: {issue.state}",
        f"labels: {labels}",
        f"author: {issue.author or '(unknown)'}",
        f"comments: {issue.comments}",
        f"created: {issue.created_at or '(unknown)'}",
        f"updated: {issue.updated_at or '(unknown)'}",
        f"estimated effort: {difficulty}",
        f"body:\n{issue.body or '(no description provided)'}",
        "",
        "## Contributor profile (user supplied)",
        f"skills: {', '.join(profile.skills) or '(none declared)'}",
        f"interests: {', '.join(profile.interests) or '(none declared)'}",
        f"experience: {profile.experience}",
        f"availability: {profile.availability}",
    ]
    if recommendation is not None:
        lines += [
            "",
            "## Deterministic analysis (already computed — do not recompute)",
            f"match_score: {recommendation.match_score} / 100",
            f"confidence: {recommendation.confidence}",
            f"components: {json.dumps(recommendation.components)}",
            "cautions: " + ("; ".join(recommendation.cautions) or "none"),
        ]
    text = "\n".join(lines)
    if len(text) > MAX_EVIDENCE_CHARS:
        text = text[:MAX_EVIDENCE_CHARS] + "\n… [evidence truncated]"
    return text


# --------------------------------------------------------------------------- #
# Parsing / validation
# --------------------------------------------------------------------------- #
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)


def _extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    """Pull the first balanced JSON object out of a model response."""
    if not text:
        return None
    candidate = text.strip()

    fenced = _FENCE_RE.search(candidate)
    if fenced:
        candidate = fenced.group(1).strip()

    try:
        parsed = json.loads(candidate)
        if isinstance(parsed, dict):
            return parsed
    except (ValueError, TypeError):
        pass

    start = candidate.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(candidate)):
        char = candidate[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                chunk = candidate[start : index + 1]
                try:
                    parsed = json.loads(chunk)
                    return parsed if isinstance(parsed, dict) else None
                except (ValueError, TypeError):
                    return None
    return None


def _coerce_explanation(
    data: Dict[str, Any],
    issue: IssueInfo,
    known_paths: Optional[Sequence[str]] = None,
) -> IssueExplanation:
    """Validate model output into a safe :class:`IssueExplanation`."""
    explanation = IssueExplanation(issue_number=issue.number, parsed_ok=True, used_model=True)

    explanation.summary = _clean_text(data.get("summary"), 800)
    explanation.why_it_matches = _clean_text(data.get("why_it_matches"), 1200)
    explanation.skills_to_use = _clean_list(data.get("skills_to_use"))
    explanation.skills_to_learn = _clean_list(data.get("skills_to_learn"))
    explanation.suggested_first_steps = _clean_list(data.get("suggested_first_steps"), limit=10, item_max=300)
    explanation.limitations = _clean_list(data.get("limitations"))

    raw_files = _clean_list(data.get("relevant_files"), limit=MAX_ITEMS, item_max=300)
    if known_paths:
        allowed = {str(p).replace("\\", "/").strip().lower() for p in known_paths}
        for path in raw_files:
            normalised = path.replace("\\", "/").strip().lower()
            if normalised in allowed:
                explanation.relevant_files.append(path)
            else:
                explanation.dropped_files.append(path)
        if explanation.dropped_files:
            explanation.limitations.append(
                "Some file paths suggested by the model were not present in the "
                "repository and were removed: " + ", ".join(explanation.dropped_files[:5])
            )
    else:
        explanation.relevant_files = raw_files

    if not explanation.summary:
        explanation.summary = issue.title
    return explanation


def fallback_explanation(
    issue: IssueInfo,
    profile: ContributorProfile,
    recommendation: Optional[IssueRecommendation] = None,
    error: Optional[str] = None,
) -> IssueExplanation:
    """Deterministic explanation built only from fields we actually have."""
    steps = [
        f"Open the issue: {issue.html_url or f'(issue #{issue.number} in the repository)'}",
        "Fork the repository and create a branch for your change.",
    ]
    if recommendation and recommendation.evidence:
        steps.append("Read the evidence above to see why this issue was suggested.")
    if issue.path_hint:
        steps.append(f"Start by inspecting `{issue.path_hint}` (hint taken from the issue title).")
    steps += [
        "Make the smallest change that resolves the issue.",
        "Run the project's tests, then open a pull request referencing the issue.",
    ]

    difficulties = (
        recommendation.difficulty.signals if recommendation else ["not evaluated"]
    )
    return IssueExplanation(
        issue_number=issue.number,
        summary=_clean_text(issue.title, 800) or f"Issue #{issue.number}",
        why_it_matches=(
            "Based on the available information, this issue matches your profile "
            f"on the components scored above (experience level: {profile.experience}). "
            "An AI explanation was not available."
        ),
        skills_to_use=[s for s in profile.skills][:MAX_ITEMS],
        skills_to_learn=[],
        suggested_first_steps=steps[:10],
        relevant_files=[issue.path_hint] if issue.path_hint else [],
        limitations=[
            "Generated without a local model (deterministic fallback).",
            *(recommendation.cautions if recommendation else []),
            "Effort signals: " + ("; ".join(difficulties) or "none"),
        ],
        parsed_ok=False,
        used_model=False,
        error=error,
        raw_text="",
    )


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def explain_issue(
    issue: IssueInfo,
    profile: ContributorProfile,
    recommendation: Optional[IssueRecommendation] = None,
    client: Optional[OllamaClient] = None,
    model: Optional[str] = None,
    known_paths: Optional[Sequence[str]] = None,
    system_prompt: str = ISSUE_SYSTEM_PROMPT,
    timeout: Optional[int] = None,
) -> IssueExplanation:
    """Explain one issue with the local model, falling back deterministically.

    Never raises. ``recommendation`` is passed through only to enrich the
    evidence with already-computed facts; its score is never modified.
    """
    if client is None:
        return fallback_explanation(
            issue, profile, recommendation, error="No local model configured."
        )

    evidence = build_issue_evidence(issue, profile, recommendation)
    prompt = build_issue_explanation_prompt(
        profile_text=json.dumps(profile.to_dict(), ensure_ascii=False),
        repo_full_name=issue.repo_full_name or "(unspecified)",
        evidence_text=evidence,
    )

    try:
        raw = client.generate(prompt, system=system_prompt, model=model, timeout=timeout)
    except OllamaError as exc:
        return fallback_explanation(issue, profile, recommendation, error=str(exc))
    except Exception as exc:  # pragma: no cover - last-resort guard
        return fallback_explanation(
            issue, profile, recommendation, error=f"Unexpected model error: {exc}"
        )

    data = _extract_json_object(raw or "")
    if not data:
        explanation = fallback_explanation(
            issue,
            profile,
            recommendation,
            error="Model did not return parseable JSON; showing a deterministic explanation.",
        )
        explanation.used_model = True
        explanation.raw_text = (raw or "")[:4000]
        explanation.why_it_matches = (
            "The model response could not be parsed into JSON, so no AI "
            "interpretation is shown. The deterministic score above still applies."
        )
        explanation.limitations.append("Model output was not valid JSON and was discarded.")
        return explanation

    explanation = _coerce_explanation(data, issue, known_paths=known_paths)
    explanation.raw_text = raw[:4000]
    explanation.used_model = True
    if not explanation.summary:
        explanation.summary = issue.title
    return explanation
