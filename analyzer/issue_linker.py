"""Connect a GitHub issue to the repository modules it may concern.

This module is deliberately conservative. It produces **candidate** files with
the exact reason each candidate was surfaced, and it separates two very
different kinds of signal:

* **confirmed** — the issue text explicitly names a file path, module, class, or
  function that really exists in the analysed repository (a static, checkable
  fact).
* **likely** — the issue and a file only share keywords or vocabulary (a
  hypothesis that a human must verify).

The linker never claims that a file *is* the cause of an issue, never executes
repository code, and never invents paths. When nothing survives the threshold it
says so and flags the result as needing manual investigation.

Everything here is deterministic and offline; no model is involved.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set, Tuple

from analyzer.file_scanner import ModuleInfo
from analyzer.github_client import RepoMetadata
from analyzer.issue_client import IssueInfo

DEFAULT_MAX_LINKED_FILES = 8
DEFAULT_MIN_SCORE = 6.0

_CODE_TOKEN_RE = re.compile(r"`([^`\n]{1,80})`")
_PATH_TOKEN_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_./-]*\.(?:py|pyi)\b")
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{3,}")

_STOPWORDS = frozenset(
    """
    the and for with this that from have has had when your into should would
    could about there their them then than also using used user need needs
    want wants make makes made file files line lines code thing things
    please help issue issues problem problems where which while what does
    here how why not but can will all any our you are was were been being
    only some more most such like just very when then wish hope would
    """.split()
)

_WEIGHTS = {
    "path": 60.0,
    "basename": 40.0,
    "module": 30.0,
    "class": 25.0,
    "function": 18.0,
    "keyword": 2.0,
}
_MAX_KEYWORD_SCORE = 10.0


@dataclass
class LinkedFile:
    """A repository file that the issue may (or may not) concern."""

    path: str
    module_name: str
    score: float
    confidence: str  # "confirmed" | "likely"
    reasons: List[str] = field(default_factory=list)
    matched_terms: List[str] = field(default_factory=list)
    symbols: List[str] = field(default_factory=list)
    line_hints: Dict[str, int] = field(default_factory=dict)
    github_url: Optional[str] = None

    @property
    def is_confirmed(self) -> bool:
        return self.confidence == "confirmed"

    @property
    def needs_investigation(self) -> bool:
        return not self.is_confirmed

    def to_dict(self) -> Dict[str, object]:
        return {
            "path": self.path,
            "module_name": self.module_name,
            "score": round(self.score, 2),
            "confidence": self.confidence,
            "reasons": list(self.reasons),
            "matched_terms": list(self.matched_terms),
            "symbols": list(self.symbols),
            "line_hints": dict(self.line_hints),
            "github_url": self.github_url,
        }


@dataclass
class IssueLinkResult:
    repo_full_name: str
    issue_number: int
    linked_files: List[LinkedFile] = field(default_factory=list)
    unmatched_terms: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    truncated: bool = False

    @property
    def needs_investigation(self) -> bool:
        return not self.linked_files

    @property
    def confirmed_count(self) -> int:
        return sum(1 for f in self.linked_files if f.is_confirmed)

    def to_dict(self) -> Dict[str, object]:
        return {
            "repo_full_name": self.repo_full_name,
            "issue_number": self.issue_number,
            "linked_files": [f.to_dict() for f in self.linked_files],
            "unmatched_terms": list(self.unmatched_terms),
            "notes": list(self.notes),
            "truncated": self.truncated,
            "needs_investigation": self.needs_investigation,
        }


def _issue_text(issue: IssueInfo) -> str:
    return f"{issue.title}\n{issue.body}"


def _basename(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def _word_present(term: str, lowered_text: str) -> bool:
    """Return True when ``term`` appears as a whole token in ``lowered_text``."""

    if not term:
        return False
    pattern = r"(?<![A-Za-z0-9_])" + re.escape(term.lower()) + r"(?![A-Za-z0-9_])"
    return re.search(pattern, lowered_text) is not None


def _file_url(
    repo_meta: Optional[RepoMetadata], path: str, line: Optional[int] = None
) -> Optional[str]:
    if repo_meta is None or not repo_meta.html_url:
        return None
    ref = repo_meta.commit_sha or repo_meta.default_branch
    if not ref:
        return None
    url = f"{repo_meta.html_url.rstrip('/')}/blob/{ref}/{path}"
    if line:
        url += f"#L{line}"
    return url


def _extract_terms(
    issue: IssueInfo,
) -> Tuple[Set[str], Set[str], Set[str], Set[str], str]:
    text = _issue_text(issue)
    lowered = text.lower()

    paths = {m.group(0).strip("./") for m in _PATH_TOKEN_RE.finditer(text)}
    code_names = {m.group(1).strip() for m in _CODE_TOKEN_RE.finditer(text)}
    identifiers = {m.group(0) for m in _IDENT_RE.finditer(text)}
    keywords = {
        token.lower()
        for token in identifiers
        if token.lower() not in _STOPWORDS
    }
    return paths, code_names, identifiers, keywords, lowered


def _match_module(
    module: ModuleInfo,
    paths: Set[str],
    code_names: Set[str],
    identifiers: Set[str],
    keywords: Set[str],
    lowered_text: str,
) -> Tuple[float, bool, List[str], List[str], List[str], Dict[str, int]]:
    score = 0.0
    has_explicit = False
    reasons: List[str] = []
    matched: List[str] = []
    symbols: List[str] = []
    line_hints: Dict[str, int] = {}

    path = module.path
    basename = _basename(path)

    for candidate in paths:
        if candidate == path:
            score += _WEIGHTS["path"]
            has_explicit = True
            reasons.append(f"The issue body names this exact path (`{path}`).")
            matched.append(path)
        elif candidate == basename:
            score += _WEIGHTS["basename"]
            has_explicit = True
            reasons.append(
                f"The issue body names the file `{basename}` (this module: `{path}`)."
            )
            matched.append(basename)

    if module.module_name:
        if module.module_name in code_names or _word_present(
            module.module_name, lowered_text
        ):
            score += _WEIGHTS["module"]
            has_explicit = True
            reasons.append(
                f"The issue references the module `{module.module_name}`."
            )
            matched.append(module.module_name)

    for cls in module.classes:
        if cls.name in code_names or (
            len(cls.name) >= 4 and _word_present(cls.name, lowered_text)
        ):
            score += _WEIGHTS["class"]
            has_explicit = True
            reasons.append(
                f"The issue references class `{cls.name}` "
                f"(defined at `{path}:{cls.lineno}`)."
            )
            symbols.append(f"class {cls.name}")
            line_hints[cls.name] = cls.lineno
            matched.append(cls.name)

    for func in module.all_functions:
        if func.is_nested and func.name not in code_names:
            continue
        if func.name in code_names or (
            len(func.name) >= 5 and _word_present(func.name, lowered_text)
        ):
            score += _WEIGHTS["function"]
            has_explicit = True
            reasons.append(
                f"The issue references `{func.name}` "
                f"(defined at `{path}:{func.lineno}`)."
            )
            symbols.append(f"{'method' if func.is_method else 'function'} {func.name}")
            line_hints[func.name] = func.lineno
            matched.append(func.name)

    keyword_hits: List[str] = []
    haystack = " ".join(
        filter(
            None,
            [module.docstring or "", " ".join(module.top_level_names)],
        )
    ).lower()
    if haystack:
        for token in sorted(keywords):
            if token in haystack:
                keyword_hits.append(token)
    if keyword_hits:
        keyword_score = min(_MAX_KEYWORD_SCORE, _WEIGHTS["keyword"] * len(keyword_hits))
        score += keyword_score
        shown = ", ".join(f"`{t}`" for t in keyword_hits[:6])
        reasons.append(
            f"Shares vocabulary with this module's own text ({shown}); "
            "verify before assuming a connection."
        )
        matched.extend(keyword_hits)

    deduped_symbols = list(dict.fromkeys(symbols))
    deduped_matched = list(dict.fromkeys(matched))
    return (
        score,
        has_explicit,
        reasons,
        deduped_matched,
        deduped_symbols,
        line_hints,
    )


def link_issue_to_files(
    issue: IssueInfo,
    modules: Dict[str, ModuleInfo],
    repo_meta: Optional[RepoMetadata] = None,
    max_files: int = DEFAULT_MAX_LINKED_FILES,
    min_score: float = DEFAULT_MIN_SCORE,
) -> IssueLinkResult:
    """Return the repository files most likely relevant to ``issue``.

    ``modules`` maps repository paths to parsed :class:`ModuleInfo`. The result
    is sorted by descending score and then path, so it is fully reproducible.
    """

    repo_full_name = (repo_meta.full_name if repo_meta else "") or issue.repo_full_name
    result = IssueLinkResult(
        repo_full_name=repo_full_name, issue_number=issue.number
    )

    if not modules:
        result.notes.append(
            "No analysed modules were available, so no files could be linked. "
            "Run the codebase analysis first."
        )
        return result

    paths, code_names, identifiers, keywords, lowered_text = _extract_terms(issue)

    scored: List[LinkedFile] = []
    matched_terms: Set[str] = set()
    for path, module in modules.items():
        if not module.parseable:
            continue
        (
            score,
            has_explicit,
            reasons,
            matched,
            symbols,
            line_hints,
        ) = _match_module(
            module, paths, code_names, identifiers, keywords, lowered_text
        )
        if score < min_score:
            continue
        linked = LinkedFile(
            path=path,
            module_name=module.module_name,
            score=score,
            confidence="confirmed" if has_explicit else "likely",
            reasons=reasons,
            matched_terms=matched,
            symbols=symbols,
            line_hints=line_hints,
            github_url=_file_url(repo_meta, path, _first_line(line_hints)),
        )
        scored.append(linked)
        matched_terms.update(matched)

    scored.sort(key=lambda item: (-item.score, item.path))
    if len(scored) > max_files:
        result.truncated = True
        scored = scored[:max_files]

    result.linked_files = scored

    explicit_terms = set(paths) | set(code_names)
    result.unmatched_terms = sorted(
        term for term in explicit_terms if term and term not in matched_terms
    )[:12]

    if not scored:
        result.notes.append(
            "No file in the analysed repository mentions this issue's wording. "
            "This is common for feature requests and may mean the relevant code "
            "is not Python or was not retrieved. Treat the location as unknown."
        )
    elif result.confirmed_count == 0:
        result.notes.append(
            "No explicit file, class, or function reference was found; the files "
            "below share vocabulary only. Confirm by reading them first."
        )

    return result


def _first_line(line_hints: Dict[str, int]) -> Optional[int]:
    if not line_hints:
        return None
    return min(line_hints.values())


def summarize_link(result: IssueLinkResult) -> str:
    """A short, deterministic, human-readable summary of a link result."""

    if result.needs_investigation:
        return (
            f"Issue #{result.issue_number}: no candidate files found in the "
            "analysed repository."
        )
    confirmed = result.confirmed_count
    return (
        f"Issue #{result.issue_number}: {len(result.linked_files)} candidate "
        f"file(s), {confirmed} with an explicit reference."
    )
