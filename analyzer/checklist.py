"""Deterministic contribution checklist for a chosen issue.

The checklist turns the guide and learning path into a concrete, tickable list
of small steps. It adapts to the issue type (bug / documentation / feature), to
whether the repository ships tests, and to whether it publishes contribution
guidelines.

Checklist state (which boxes are ticked) is owned by the UI, not this module —
this module only produces the items and their stable identifiers, so progress
can be scoped per issue and never leaks between different issues.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from analyzer.contribution_guide import classify_issue
from analyzer.file_scanner import ModuleInfo
from analyzer.github_client import RepoMetadata
from analyzer.issue_client import IssueInfo
from analyzer.issue_linker import IssueLinkResult
from analyzer.recommender import ContributorProfile

CATEGORY_ORDER = ("understand", "setup", "implement", "verify", "share")

CATEGORY_LABELS = {
    "understand": "Understand",
    "setup": "Set up",
    "implement": "Implement",
    "verify": "Verify",
    "share": "Share",
}


@dataclass
class ChecklistItem:
    id: str
    text: str
    category: str

    def to_dict(self) -> Dict[str, str]:
        return {"id": self.id, "text": self.text, "category": self.category}


def checklist_key(repo_full_name: str, issue_number: int) -> str:
    """A stable key that scopes checklist progress to one issue."""

    repo = (repo_full_name or "unknown").lower().replace("/", "__")
    return f"{repo}#{issue_number}"


def _has_contributing(readme: str, modules: Optional[Dict[str, ModuleInfo]]) -> bool:
    if readme and "contribut" in readme.lower():
        return True
    if modules:
        for path in modules:
            lowered = path.lower()
            if "contributing" in lowered or "code_of_conduct" in lowered:
                return True
    return False


def _has_tests(modules: Optional[Dict[str, ModuleInfo]]) -> bool:
    if not modules:
        return False
    return any("test" in path.lower() for path in modules)


def build_checklist(
    issue: IssueInfo,
    link: IssueLinkResult,
    profile: Optional[ContributorProfile] = None,
    repo_meta: Optional[RepoMetadata] = None,
    readme: str = "",
    modules: Optional[Dict[str, ModuleInfo]] = None,
) -> List[ChecklistItem]:
    """Return an ordered, issue-specific checklist."""

    issue_kind = classify_issue(issue)
    items: List[ChecklistItem] = []

    def add(category: str, text: str) -> None:
        items.append(
            ChecklistItem(id=f"c{len(items) + 1}", text=text, category=category)
        )

    add("understand", "Read the whole issue, including every comment.")
    add("understand", "Open the issue in a browser tab so you can re-check it.")
    if link.linked_files:
        top = link.linked_files[0]
        add(
            "understand",
            f"Read `{top.path}`, the most relevant file found for this issue.",
        )
    else:
        add(
            "understand",
            "Ask a maintainer which file or module handles this issue.",
        )
    if issue_kind == "bug":
        add("understand", "Reproduce the bug and write down the exact steps.")
    elif issue_kind == "docs":
        add("understand", "Locate the exact documentation that is wrong or missing.")

    add("setup", "Fork the repository and clone your fork locally.")
    if _has_contributing(readme, modules):
        add("setup", "Read the repository's contributing guidelines.")
    add("setup", "Create a branch with a descriptive name for your change.")

    if issue_kind == "docs":
        add("implement", "Draft the corrected text in your own words.")
        add("implement", "Apply the documentation change in the right file.")
    elif issue_kind == "feature":
        add("implement", "Sketch the smallest change that satisfies the request.")
        add("implement", "Implement the change, keeping it focused and small.")
    else:
        add("implement", "Make the smallest change that addresses the issue.")
        add("implement", "Keep unrelated edits out of your diff.")

    if _has_tests(modules):
        add("verify", "Run the existing test suite and confirm the baseline.")
        add("verify", "Add or update a test that covers your change.")
    else:
        add("verify", "Manually confirm the issue's behaviour is fixed.")
    add("verify", "Re-read your diff and remove anything accidental.")

    add("share", "Commit with a clear message that references the issue.")
    add("share", "Push your branch to your fork.")
    add("share", "Open a pull request describing what you changed and why.")
    add("share", "Respond to review comments patiently.")

    return items


def progress(items: List[ChecklistItem], state: Optional[Dict[str, bool]]) -> Tuple[int, int, float]:
    """Return (done, total, fraction) for a checklist and its state map."""

    total = len(items)
    if total == 0:
        return 0, 0, 0.0
    state = state or {}
    done = sum(1 for item in items if state.get(item.id))
    return done, total, done / total


def group_by_category(items: List[ChecklistItem]) -> Dict[str, List[ChecklistItem]]:
    grouped: Dict[str, List[ChecklistItem]] = {c: [] for c in CATEGORY_ORDER}
    for item in items:
        grouped.setdefault(item.category, []).append(item)
    return {c: grouped[c] for c in CATEGORY_ORDER if grouped[c]}
