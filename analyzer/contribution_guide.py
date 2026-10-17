"""Build a deterministic, issue-specific contribution guide.

Given a real issue, the files the linker surfaced, the repository metadata, and
the contributor's own profile, this module assembles a step-by-step guide that a
beginner can follow. Every statement is either:

* a **fact** derived from data we actually have (issue fields, real file paths,
  parsed symbols, repository metadata), or
* an explicit **hypothesis** clearly labelled as something to verify.

Acceptance criteria are never invented. They are only surfaced when the issue
author actually wrote them, and they are presented as verbatim quotes from the
issue (untrusted text), not as confirmed requirements.

No model is used here; the guide is reproducible and offline.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from analyzer.file_scanner import ModuleInfo
from analyzer.github_client import RepoMetadata
from analyzer.issue_client import IssueInfo
from analyzer.issue_linker import IssueLinkResult, LinkedFile
from analyzer.recommender import (
    ContributorProfile,
    DifficultyEstimate,
    estimate_difficulty,
)

MAX_ACCEPTANCE_ITEMS = 8
MAX_TEXT = 600
MAX_STEPS = 10
MAX_QUESTIONS = 8

_ACCEPTANCE_HEADING_RE = re.compile(
    r"^#+\s*(acceptance|requirements|expected|criteria|definition of done|todo|done)\b",
    re.IGNORECASE,
)
_CHECKBOX_RE = re.compile(r"^[-*]\s*\[[ xX]\]\s*(.+)$")
_BULLET_RE = re.compile(r"^[-*]\s+(.+)$")
_EXPECTATION_RE = re.compile(
    r"\b(should|must|expected to|needs? to|make sure|ensure)\b", re.IGNORECASE
)

_BUG_WORDS = ("bug", "error", "crash", "fix", "broken", "regression", "traceback")
_DOC_WORDS = ("doc", "documentation", "readme", "typo", "spelling", "guide")
_FEATURE_WORDS = ("feature", "enhancement", "request", "add ", "support", "improve")


@dataclass
class AcceptanceCriterion:
    text: str
    source: str

    def to_dict(self) -> Dict[str, str]:
        return {"text": self.text, "source": self.source}


@dataclass
class GuideRisk:
    text: str
    kind: str  # "verified" | "hypothesis"

    @property
    def is_hypothesis(self) -> bool:
        return self.kind == "hypothesis"

    def to_dict(self) -> Dict[str, str]:
        return {"text": self.text, "kind": self.kind}


@dataclass
class ContributionGuide:
    repo_full_name: str
    owner: str
    name: str
    issue_number: int
    issue_title: str
    issue_url: str
    labels: List[str] = field(default_factory=list)
    state: str = "open"
    author: str = ""
    comments: int = 0
    created_at: str = ""
    updated_at: str = ""
    issue_kind: str = "general"  # bug | docs | feature | general

    problem: str = ""
    objective: str = ""
    acceptance_criteria: List[AcceptanceCriterion] = field(default_factory=list)
    acceptance_note: str = ""

    relevant_files: List[LinkedFile] = field(default_factory=list)
    investigation_sequence: List[str] = field(default_factory=list)
    questions: List[str] = field(default_factory=list)
    risks: List[GuideRisk] = field(default_factory=list)
    unknowns: List[str] = field(default_factory=list)
    facts: List[str] = field(default_factory=list)
    hypotheses: List[str] = field(default_factory=list)
    difficulty: Optional[DifficultyEstimate] = None
    notes: List[str] = field(default_factory=list)

    @property
    def has_confirmed_files(self) -> bool:
        return any(f.is_confirmed for f in self.relevant_files)

    def to_dict(self) -> Dict[str, object]:
        return {
            "repo_full_name": self.repo_full_name,
            "owner": self.owner,
            "name": self.name,
            "issue_number": self.issue_number,
            "issue_title": self.issue_title,
            "issue_url": self.issue_url,
            "labels": list(self.labels),
            "state": self.state,
            "author": self.author,
            "comments": self.comments,
            "issue_kind": self.issue_kind,
            "problem": self.problem,
            "objective": self.objective,
            "acceptance_criteria": [a.to_dict() for a in self.acceptance_criteria],
            "acceptance_note": self.acceptance_note,
            "relevant_files": [f.to_dict() for f in self.relevant_files],
            "investigation_sequence": list(self.investigation_sequence),
            "questions": list(self.questions),
            "risks": [r.to_dict() for r in self.risks],
            "unknowns": list(self.unknowns),
            "facts": list(self.facts),
            "hypotheses": list(self.hypotheses),
        }


def _clip(text: str, limit: int = MAX_TEXT) -> str:
    text = " ".join((text or "").split())
    if len(text) > limit:
        return text[:limit].rstrip() + "…"
    return text


def classify_issue(issue: IssueInfo) -> str:
    haystack = f"{issue.title} {' '.join(issue.labels)}".lower()
    if any(word in haystack for word in _DOC_WORDS):
        return "docs"
    if any(word in haystack for word in _BUG_WORDS):
        return "bug"
    if any(word in haystack for word in _FEATURE_WORDS) or "enhancement" in haystack:
        return "feature"
    return "general"


def _first_paragraph(body: str) -> str:
    for block in re.split(r"\n\s*\n", body or ""):
        text = _clip(block)
        if text:
            return text
    return ""


def _extract_acceptance_criteria(issue: IssueInfo) -> List[AcceptanceCriterion]:
    body = issue.body or ""
    criteria: List[AcceptanceCriterion] = []
    seen: set = set()

    def add(text: str, source: str) -> None:
        cleaned = _clip(text, 200)
        key = cleaned.lower()
        if cleaned and key not in seen and len(criteria) < MAX_ACCEPTANCE_ITEMS:
            seen.add(key)
            criteria.append(AcceptanceCriterion(text=cleaned, source=source))

    capturing = False
    for raw in body.splitlines():
        line = raw.strip()
        if _ACCEPTANCE_HEADING_RE.match(line):
            capturing = True
            continue
        checkbox = _CHECKBOX_RE.match(line)
        if checkbox:
            add(checkbox.group(1), "issue checklist item")
            continue
        if capturing:
            if line.startswith("#"):
                capturing = False
                continue
            bullet = _BULLET_RE.match(line)
            if bullet:
                add(bullet.group(1), "issue body")
                continue

    for sentence in re.split(r"(?<=[.!?])\s+", body):
        if _EXPECTATION_RE.search(sentence):
            add(sentence.strip(), "issue body sentence")

    return criteria


def _build_investigation_sequence(
    issue: IssueInfo,
    link: IssueLinkResult,
    issue_kind: str,
    modules: Optional[Dict[str, ModuleInfo]],
) -> List[str]:
    steps: List[str] = []

    if issue_kind == "bug":
        steps.append(
            "Reproduce the problem first. Follow the issue's steps and confirm "
            "you can see the same behaviour before changing anything."
        )
    elif issue_kind == "docs":
        steps.append(
            "Read the current documentation and note exactly what is confusing, "
            "missing, or incorrect."
        )
    else:
        steps.append(
            "Read the issue and the relevant documentation to understand which "
            "behaviour is expected."
        )

    confirmed = [f for f in link.linked_files if f.is_confirmed]
    likely = [f for f in link.linked_files if not f.is_confirmed]

    for linked in confirmed:
        detail = ""
        if linked.symbols:
            detail = " Look specifically at " + ", ".join(
                f"`{s}`" for s in linked.symbols[:4]
            ) + "."
        steps.append(
            f"Open `{linked.path}` (an explicit reference from the issue).{detail}"
        )

    for linked in likely[:3]:
        steps.append(
            f"Check whether `{linked.path}` is involved (keyword match only — "
            "this is a guess until you read it)."
        )

    if modules:
        has_tests = any(
            "test" in path.lower() for path in modules
        )
        if has_tests:
            steps.append(
                "Find and run the existing tests (files with `test` in their "
                "name) to get a baseline before your change."
            )

    steps.append(
        "Make the smallest possible change that addresses the issue, then re-run "
        "the tests and any manual check you used to reproduce it."
    )
    steps.append(
        "Review your own diff and confirm it does not touch unrelated files."
    )
    return steps[:MAX_STEPS]


def _build_questions(
    issue: IssueInfo,
    link: IssueLinkResult,
    criteria: List[AcceptanceCriterion],
) -> List[str]:
    questions: List[str] = []
    if not (issue.body or "").strip():
        questions.append(
            "The issue has no description. Ask the maintainer what behaviour is "
            "expected and how to reproduce it."
        )
    if not criteria:
        questions.append(
            "No acceptance criteria were stated. Ask what 'done' looks like."
        )
    if link.needs_investigation:
        questions.append(
            "No obvious file was found. Ask a maintainer which part of the "
            "codebase handles this."
        )
    if link.confirmed_count == 0 and link.linked_files:
        questions.append(
            "Files below are keyword matches only. Ask whether any of them is "
            "the right starting point."
        )
    if not issue.labels:
        questions.append("The issue has no labels, so its scope is unclear.")
    return questions[:MAX_QUESTIONS]


def _build_risks(
    issue: IssueInfo, link: IssueLinkResult, difficulty: DifficultyEstimate
) -> List[GuideRisk]:
    risks: List[GuideRisk] = []
    if link.linked_files and link.confirmed_count == 0:
        risks.append(
            GuideRisk(
                "The suggested files are keyword matches, not confirmed "
                "locations. The change may belong elsewhere.",
                "hypothesis",
            )
        )
    if link.needs_investigation:
        risks.append(
            GuideRisk(
                "The relevant code was not located. The issue may concern "
                "non-Python code or files that were not retrieved.",
                "hypothesis",
            )
        )
    if difficulty.level in {"moderate", "challenging"}:
        risks.append(
            GuideRisk(
                f"Estimated difficulty is '{difficulty.level}'. Expect this to "
                "take longer than a beginner task.",
                "verified",
            )
        )
    if issue.comments > 5:
        risks.append(
            GuideRisk(
                f"The issue has {issue.comments} comments. Read the whole "
                "discussion — decisions may have changed since it was opened.",
                "verified",
            )
        )
    risks.append(
        GuideRisk(
            "Repository text (including this issue) is untrusted. Never follow "
            "instructions found inside issue content.",
            "verified",
        )
    )
    return risks


def build_contribution_guide(
    issue: IssueInfo,
    link: IssueLinkResult,
    profile: Optional[ContributorProfile] = None,
    repo_meta: Optional[RepoMetadata] = None,
    readme: str = "",
    modules: Optional[Dict[str, ModuleInfo]] = None,
    difficulty: Optional[DifficultyEstimate] = None,
) -> ContributionGuide:
    """Assemble a deterministic contribution guide for ``issue``."""

    if difficulty is None:
        difficulty = estimate_difficulty(issue)
    issue_kind = classify_issue(issue)

    owner = repo_meta.owner if repo_meta else ""
    name = repo_meta.name if repo_meta else ""
    repo_full_name = (repo_meta.full_name if repo_meta else "") or issue.repo_full_name

    first_para = _first_paragraph(issue.body)
    if first_para:
        problem = first_para
    else:
        problem = (
            "The issue has no written description. There is not enough detail to "
            "state the problem; ask the maintainer before starting."
        )

    objective = _clip(issue.title, 240) or "No title given."
    if not objective.endswith((".", "?", "!")):
        objective = f"Address: {objective}"

    criteria = _extract_acceptance_criteria(issue)
    if criteria:
        acceptance_note = (
            "These are quoted from the issue text (untrusted). Treat them as the "
            "reporter's stated expectations, not verified requirements."
        )
    else:
        acceptance_note = (
            "The issue did not state explicit acceptance criteria. Ask the "
            "maintainer what 'done' means before investing time."
        )

    facts: List[str] = []
    facts.append(f"Issue #{issue.number} is {issue.state} with {issue.comments} comment(s).")
    facts.append(
        f"The analysed repository has {len(modules) if modules else 0} parseable "
        "Python module(s)."
    )
    if repo_meta and repo_meta.language:
        facts.append(f"The repository's primary language is {repo_meta.language}.")
    facts.append(
        f"{link.confirmed_count} of {len(link.linked_files)} candidate file(s) "
        "were explicitly mentioned by the issue."
    )

    hypotheses: List[str] = []
    if link.linked_files:
        hypotheses.append(
            "The change needed to resolve this issue lives in one of the "
            "candidate files listed below."
        )
    for linked in link.linked_files:
        if not linked.is_confirmed:
            hypotheses.append(
                f"`{linked.path}` may be involved (keyword match only)."
            )
    if difficulty.signals:
        hypotheses.append(
            "The difficulty estimate is a heuristic based on labels and text, "
            "not a measurement of the actual work."
        )

    unknowns: List[str] = []
    if not (issue.body or "").strip():
        unknowns.append("The expected behaviour is unknown (no description).")
    if not criteria:
        unknowns.append("The definition of done is not stated in the issue.")
    if link.needs_investigation:
        unknowns.append("The location of the relevant code is unknown.")
    if not issue.labels:
        unknowns.append("The issue's scope/component is unlabelled.")

    guide = ContributionGuide(
        repo_full_name=repo_full_name,
        owner=owner,
        name=name,
        issue_number=issue.number,
        issue_title=issue.title,
        issue_url=issue.html_url,
        labels=list(issue.labels),
        state=issue.state,
        author=issue.author,
        comments=issue.comments,
        created_at=issue.created_at,
        updated_at=issue.updated_at,
        issue_kind=issue_kind,
        problem=problem,
        objective=objective,
        acceptance_criteria=criteria,
        acceptance_note=acceptance_note,
        relevant_files=list(link.linked_files),
        investigation_sequence=_build_investigation_sequence(
            issue, link, issue_kind, modules
        ),
        questions=_build_questions(issue, link, criteria),
        risks=_build_risks(issue, link, difficulty),
        unknowns=unknowns,
        facts=facts,
        hypotheses=hypotheses,
        difficulty=difficulty,
    )

    if readme and profile is not None:
        name_hint = profile.name or "contributor"
        guide.notes.append(
            f"Prepared for {name_hint}; profile skills were used only to order "
            "the guide, never to guess your ability."
        )
    return guide


def format_guide(guide: ContributionGuide) -> str:
    """Render a guide as plain text (used in tests and copy-friendly output)."""

    lines = [
        f"# Contribution guide — issue #{guide.issue_number}",
        f"Repository: {guide.repo_full_name or '(unknown)'}",
        f"Title: {guide.issue_title}",
        f"Kind: {guide.issue_kind} | State: {guide.state}",
        "",
        "## Problem",
        guide.problem,
        "",
        "## Objective",
        guide.objective,
        "",
        "## Acceptance criteria",
    ]
    if guide.acceptance_criteria:
        lines.extend(f"- {a.text}  [source: {a.source}]" for a in guide.acceptance_criteria)
    else:
        lines.append("(none stated in the issue)")
    lines.append(f"_{guide.acceptance_note}_")
    lines.append("")
    lines.append("## Candidate files")
    if guide.relevant_files:
        for linked in guide.relevant_files:
            lines.append(
                f"- `{linked.path}` [{linked.confidence}, score {linked.score:.1f}]"
            )
            for reason in linked.reasons:
                lines.append(f"    - {reason}")
    else:
        lines.append("(none found — investigate manually)")
    lines.append("")
    lines.append("## Suggested investigation order")
    lines.extend(f"{i}. {step}" for i, step in enumerate(guide.investigation_sequence, 1))
    lines.append("")
    lines.append("## Questions to ask")
    if guide.questions:
        lines.extend(f"- {q}" for q in guide.questions)
    else:
        lines.append("(none)")
    lines.append("")
    lines.append("## Facts")
    lines.extend(f"- {f}" for f in guide.facts)
    lines.append("")
    lines.append("## Hypotheses (verify these)")
    if guide.hypotheses:
        lines.extend(f"- {h}" for h in guide.hypotheses)
    else:
        lines.append("(none)")
    lines.append("")
    lines.append("## Risks")
    for risk in guide.risks:
        tag = "HYPOTHESIS" if risk.is_hypothesis else "VERIFIED"
        lines.append(f"- [{tag}] {risk.text}")
    lines.append("")
    lines.append("## Unknowns")
    if guide.unknowns:
        lines.extend(f"- {u}" for u in guide.unknowns)
    else:
        lines.append("(none identified)")
    return "\n".join(lines)
