"""Build a personalized, ordered learning path for a chosen issue.

A learning path answers a simple question for a beginner: *"what do I need to
understand, in what order, before I can confidently work on this issue?"*

Personalization uses only what the contributor wrote about themselves plus
static facts about the repository (language, imported frameworks, file layout).
Two rules matter:

* A topic is marked **reinforce** when the profile already mentions a related
  skill, and **to learn** otherwise. A missing skill is *never* treated as proof
  that the person lacks it — the path is a suggestion list, and the UI says so.
* External links point only at stable, canonical documentation sites. No link is
  ever fabricated from model output.

No model is involved; the path is deterministic and offline.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from analyzer.contribution_guide import classify_issue
from analyzer.file_scanner import ModuleInfo
from analyzer.github_client import RepoMetadata
from analyzer.issue_client import IssueInfo
from analyzer.issue_linker import IssueLinkResult
from analyzer.recommender import ContributorProfile

MAX_TOPICS = 9
MAX_EXERCISES = 4
MAX_CHECKS = 4

_DOC_LINKS = {
    "python": "https://docs.python.org/3/tutorial/",
    "pytest": "https://docs.pytest.org/en/stable/",
    "unittest": "https://docs.python.org/3/library/unittest.html",
    "typing": "https://docs.python.org/3/library/typing.html",
    "git": "https://git-scm.com/book/en/v2",
    "github": "https://docs.github.com/en/pull-requests",
    "flask": "https://flask.palletsprojects.com/",
    "django": "https://docs.djangoproject.com/",
    "fastapi": "https://fastapi.tiangolo.com/",
    "numpy": "https://numpy.org/doc/stable/",
    "pandas": "https://pandas.pydata.org/docs/",
    "requests": "https://requests.readthedocs.io/",
    "sqlalchemy": "https://docs.sqlalchemy.org/",
    "click": "https://click.palletsprojects.com/",
    "rich": "https://rich.readthedocs.io/",
    "pydantic": "https://docs.pydantic.dev/",
    "asyncio": "https://docs.python.org/3/library/asyncio.html",
}

_KNOWN_FRAMEWORKS = tuple(_DOC_LINKS.keys())

_FRAMEWORK_LABELS = {
    "python": "Python",
    "pytest": "pytest",
    "unittest": "unittest",
    "typing": "type hints",
    "git": "Git",
    "github": "GitHub pull requests",
    "flask": "Flask",
    "django": "Django",
    "fastapi": "FastAPI",
    "numpy": "NumPy",
    "pandas": "pandas",
    "requests": "requests",
    "sqlalchemy": "SQLAlchemy",
    "click": "Click",
    "rich": "Rich",
    "pydantic": "Pydantic",
    "asyncio": "asyncio",
}


@dataclass
class LearningTopic:
    order: int
    title: str
    why: str
    where: List[str] = field(default_factory=list)
    resources: List[str] = field(default_factory=list)
    exercises: List[str] = field(default_factory=list)
    readiness_checks: List[str] = field(default_factory=list)
    matched_skills: List[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        return "reinforce" if self.matched_skills else "to_learn"

    def to_dict(self) -> Dict[str, object]:
        return {
            "order": self.order,
            "title": self.title,
            "why": self.why,
            "where": list(self.where),
            "resources": list(self.resources),
            "exercises": list(self.exercises),
            "readiness_checks": list(self.readiness_checks),
            "matched_skills": list(self.matched_skills),
            "status": self.status,
        }


@dataclass
class LearningPath:
    repo_full_name: str
    issue_number: int
    issue_title: str
    topics: List[LearningTopic] = field(default_factory=list)
    next_steps: List[str] = field(default_factory=list)
    profile_notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        return {
            "repo_full_name": self.repo_full_name,
            "issue_number": self.issue_number,
            "issue_title": self.issue_title,
            "topics": [t.to_dict() for t in self.topics],
            "next_steps": list(self.next_steps),
            "profile_notes": list(self.profile_notes),
        }


def _norm_tokens(values: List[str]) -> List[str]:
    tokens: List[str] = []
    for value in values or []:
        for token in re.split(r"[^a-z0-9+#.]+", str(value).lower()):
            if token:
                tokens.append(token)
    return tokens


def _matched_topics(profile: ContributorProfile, *keys: str) -> List[str]:
    """Return the profile skills that relate to any of ``keys``."""

    if profile is None:
        return []
    skill_tokens = {s.lower() for s in profile.skills}
    skill_tokens |= set(_norm_tokens(profile.skills))
    hits: List[str] = []
    for key in keys:
        if key.lower() in skill_tokens:
            hits.append(key)
    return list(dict.fromkeys(hits))


def _detect_frameworks(modules: Optional[Dict[str, ModuleInfo]]) -> List[str]:
    found: List[str] = []
    if not modules:
        return found
    for module in modules.values():
        if not module.parseable:
            continue
        for imp in module.imports:
            root = ""
            if imp.module:
                root = imp.module.split(".")[0].lower()
            elif imp.names:
                root = imp.names[0].lower()
            if root in _KNOWN_FRAMEWORKS and root not in found:
                found.append(root)
        if "pytest" in found and "unittest" in found:
            break
    return found


def _test_files(modules: Optional[Dict[str, ModuleInfo]]) -> List[str]:
    if not modules:
        return []
    return sorted(p for p in modules if "test" in p.lower())


def _issue_mentions(issue: IssueInfo, *words: str) -> bool:
    haystack = f"{issue.title} {issue.body} {' '.join(issue.labels)}".lower()
    return any(word in haystack for word in words)


def _mk_topic(
    order: int,
    title: str,
    why: str,
    profile: Optional[ContributorProfile],
    where: Optional[List[str]] = None,
    resources: Optional[List[str]] = None,
    exercises: Optional[List[str]] = None,
    checks: Optional[List[str]] = None,
    skill_keys: Optional[List[str]] = None,
) -> LearningTopic:
    keys = skill_keys or []
    matched = _matched_topics(profile, *keys) if profile else []
    return LearningTopic(
        order=order,
        title=title,
        why=why,
        where=list(where or []),
        resources=list(resources or []),
        exercises=list(exercises or [])[:MAX_EXERCISES],
        readiness_checks=list(checks or [])[:MAX_CHECKS],
        matched_skills=matched,
    )


def build_learning_path(
    issue: IssueInfo,
    profile: Optional[ContributorProfile],
    link: IssueLinkResult,
    repo_meta: Optional[RepoMetadata] = None,
    modules: Optional[Dict[str, ModuleInfo]] = None,
) -> LearningPath:
    """Return an ordered, personalized learning path for ``issue``."""

    repo_full_name = (repo_meta.full_name if repo_meta else "") or issue.repo_full_name
    issue_kind = classify_issue(issue)
    frameworks = _detect_frameworks(modules)
    tests = _test_files(modules)

    language = (repo_meta.language if repo_meta else "") or "Python"
    primary = language.lower()
    primary_key = "python" if primary in {"python", ""} else primary

    topics: List[LearningTopic] = []
    order = 0

    order += 1
    topics.append(
        _mk_topic(
            order,
            f"{_FRAMEWORK_LABELS.get(primary_key, language)} for this repository",
            "Every change here is Python, so reading and writing Python with "
            "confidence is the foundation.",
            profile,
            where=[f.path for f in link.linked_files[:2]],
            resources=[_DOC_LINKS["python"]],
            exercises=[
                "Read one candidate file end to end and write a one-line summary "
                "of what it does.",
                "Explain aloud what a function in it takes and returns.",
            ],
            checks=["Can you read a function and say what it does?"],
            skill_keys=["python", primary_key],
        )
    )

    if issue_kind == "bug":
        order += 1
        topics.append(
            _mk_topic(
                order,
                "Reproducing and isolating a bug",
                "The issue reports a problem; you must be able to see it yourself "
                "before you can fix it.",
                profile,
                exercises=[
                    "Write down the exact steps that trigger the problem.",
                    "Run the code with those steps and capture any error text.",
                ],
                checks=["Can you make the problem happen on demand?"],
                skill_keys=["debugging", "testing"],
            )
        )
    elif issue_kind == "docs":
        order += 1
        topics.append(
            _mk_topic(
                order,
                "Writing clear documentation",
                "This is a documentation task, so clarity and accuracy matter more "
                "than code.",
                profile,
                exercises=[
                    "Identify the exact sentence or page that is wrong or missing.",
                    "Draft the corrected wording in your own words first.",
                ],
                checks=["Does your wording match how the project already writes?"],
                skill_keys=["docs", "documentation", "writing"],
            )
        )
    elif issue_kind == "feature":
        order += 1
        topics.append(
            _mk_topic(
                order,
                "Planning a focused code change",
                "This is a feature request; start by understanding the current "
                "behaviour before adding anything.",
                profile,
                exercises=[
                    "Trace where the requested behaviour would live.",
                    "Write the smallest change that would satisfy the request.",
                ],
                checks=["Can you describe the change in two sentences?"],
                skill_keys=["design", "architecture"],
            )
        )

    if link.linked_files:
        top = link.linked_files[0]
        detail = ", ".join(top.symbols[:4]) if top.symbols else "its functions and classes"
        order += 1
        topics.append(
            _mk_topic(
                order,
                f"Understanding `{top.path}`",
                "This is the most relevant file found for the issue; understanding "
                "it removes most of the guesswork.",
                profile,
                where=[top.path],
                exercises=[
                    "List the classes and functions in the file.",
                    "Find where the function named in the issue is defined and used.",
                ],
                checks=["Can you explain the role of this file in one sentence?"],
                skill_keys=[top.module_name.lower().split(".")[-1]],
            )
        )
        if detail:
            topics[-1].why += f" Pay attention to: {detail}."

    if tests or _issue_mentions(issue, "test"):
        order += 1
        topics.append(
            _mk_topic(
                order,
                "Running the project's tests",
                "Tests tell you whether your change broke anything — and how the "
                "project expects code to behave.",
                profile,
                where=tests[:3],
                resources=[_DOC_LINKS["pytest"]],
                exercises=[
                    "Run the test suite and note which tests pass already.",
                    "Add or adjust one test that reflects the issue.",
                ],
                checks=["Can you run the tests and read the result?"],
                skill_keys=["testing", "pytest", "unittest"],
            )
        )

    for framework in frameworks[:3]:
        if framework == primary_key:
            continue
        order += 1
        topics.append(
            _mk_topic(
                order,
                f"Working with {_FRAMEWORK_LABELS.get(framework, framework)}",
                f"The repository imports `{framework}`, so you will meet it while "
                "reading the code.",
                profile,
                resources=[_DOC_LINKS[framework]],
                exercises=[
                    f"Skim the {framework} docs for the concept the issue touches.",
                    "Find one place in the repo that uses it.",
                ],
                checks=[f"Can you recognise where {framework} is used?"],
                skill_keys=[framework],
            )
        )

    order += 1
    topics.append(
        _mk_topic(
            order,
            "Opening your first pull request",
            "The final step is sharing your change in a way maintainers can review.",
            profile,
            resources=[_DOC_LINKS["git"], _DOC_LINKS["github"]],
            exercises=[
                "Fork the repository and create a branch for your change.",
                "Write a short pull-request description that references the issue.",
            ],
            checks=["Do you know how to open a PR against a fork?"],
            skill_keys=["git", "github"],
        )
    )

    topics = topics[:MAX_TOPICS]

    profile_notes: List[str] = []
    if profile and profile.skills:
        profile_notes.append(
            "Your profile lists: " + ", ".join(profile.skills) + "."
        )
    profile_notes.append(
        "Topics marked 'reinforce' overlap with skills you mentioned; 'to learn' "
        "topics are suggestions — skip any you already know."
    )
    profile_notes.append(
        "Not mentioning a skill is not treated as evidence that you lack it."
    )

    next_steps = [
        "Work through the topics in order; each builds on the previous one.",
        "Open the issue thread and introduce yourself before large changes.",
        "Ask the maintainer any question listed in the contribution guide.",
        "Make the smallest change, run the tests, then open a pull request.",
    ]

    return LearningPath(
        repo_full_name=repo_full_name,
        issue_number=issue.number,
        issue_title=issue.title,
        topics=topics,
        next_steps=next_steps,
        profile_notes=profile_notes,
    )


def format_learning_path(path: LearningPath) -> str:
    lines = [
        f"# Learning path — issue #{path.issue_number}",
        f"Repository: {path.repo_full_name or '(unknown)'}",
        "",
    ]
    for topic in path.topics:
        status = "reinforce" if topic.status == "reinforce" else "to learn"
        lines.append(f"{topic.order}. {topic.title}  [{status}]")
        lines.append(f"   Why: {topic.why}")
        if topic.where:
            lines.append("   Where: " + ", ".join(topic.where))
        if topic.resources:
            lines.append("   Resources: " + ", ".join(topic.resources))
        for exercise in topic.exercises:
            lines.append(f"   - exercise: {exercise}")
        for check in topic.readiness_checks:
            lines.append(f"   - check: {check}")
        lines.append("")
    lines.append("## Next steps")
    lines.extend(f"- {step}" for step in path.next_steps)
    return "\n".join(lines)
