"""Build a compact, bounded evidence package for the local AI model.

The model must never receive an entire repository. Instead this module selects
the most informative files, extracts short numbered snippets, and concatenates
static-analysis facts under a strict character budget sized for an 8 GB machine
running a small (≈3B parameter) local model.
"""

from __future__ import annotations

from typing import Any, Dict, List, Sequence, Tuple

from analyzer.code_map import EntryPointCandidate, ImportRelation
from analyzer.file_scanner import ModuleInfo

DEFAULT_MAX_CHARS = 12000
DEFAULT_MAX_SNIPPETS = 6
DEFAULT_SNIPPET_LINES = 40
DEFAULT_MAX_RELATIONSHIPS = 60

_TRUNCATION_MARKER = "\n[truncated]"


def truncate_text(text: str, max_chars: int) -> str:
    """Truncate *text* to at most *max_chars* characters."""
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    if max_chars <= len(_TRUNCATION_MARKER):
        return text[:max_chars]
    return text[: max_chars - len(_TRUNCATION_MARKER)] + _TRUNCATION_MARKER


def numbered_snippet(
    content: str,
    start_line: int = 1,
    max_lines: int = DEFAULT_SNIPPET_LINES,
) -> str:
    """Return *content* as text with 1-based line numbers, preserving original
    line numbers starting at *start_line*."""
    if not content:
        return ""
    lines = content.splitlines()
    selected = lines[:max_lines]
    rendered = []
    for offset, line in enumerate(selected):
        rendered.append(f"{start_line + offset:>4}: {line}")
    if len(lines) > max_lines:
        rendered.append("    ... (more lines omitted)")
    return "\n".join(rendered)


def select_important_files(
    modules: Dict[str, ModuleInfo],
    entry_points: Sequence[EntryPointCandidate],
    relations: Sequence[ImportRelation],
    limit: int = DEFAULT_MAX_SNIPPETS,
) -> List[str]:
    """Rank files by likely importance and return the top *limit* paths."""
    entry_paths = {ep.path for ep in entry_points}
    indegree: Dict[str, int] = {}
    for relation in relations:
        if relation.target_path:
            indegree[relation.target_path] = indegree.get(relation.target_path, 0) + 1

    scored: List[Tuple[int, str]] = []
    for path, module in modules.items():
        score = 0
        if path in entry_paths:
            score += 100
        if module.has_main_guard:
            score += 60
        basename = path.rsplit("/", 1)[-1]
        if basename == "__init__.py":
            score += 12
        if basename in {"main.py", "app.py", "cli.py", "__main__.py"}:
            score += 25
        score += len(module.classes) * 3
        score += len(module.functions) * 2
        score += indegree.get(path, 0) * 6
        depth = path.count("/")
        score += max(0, 8 - depth)
        scored.append((score, path))

    scored.sort(key=lambda item: (-item[0], item[1]))
    return [path for _score, path in scored[:limit]]


def build_evidence_package(
    repo_meta: Dict[str, Any],
    readme: str,
    modules: Dict[str, ModuleInfo],
    entry_points: Sequence[EntryPointCandidate],
    relations: Sequence[ImportRelation],
    file_contents: Dict[str, str],
    max_chars: int = DEFAULT_MAX_CHARS,
    max_snippets: int = DEFAULT_MAX_SNIPPETS,
    snippet_lines: int = DEFAULT_SNIPPET_LINES,
) -> Dict[str, Any]:
    """Assemble the evidence package as a JSON-compatible dict.

    The package contains both a structured form (for display and testing) and a
    pre-rendered text form (for the model), each bounded by *max_chars*.
    """
    important = select_important_files(
        modules, entry_points, relations, limit=max_snippets
    )

    snippets: List[Dict[str, Any]] = []
    for path in important:
        content = file_contents.get(path)
        if not content:
            continue
        snippet_text = numbered_snippet(content, 1, snippet_lines)
        snippets.append(
            {
                "path": path,
                "start_line": 1,
                "end_line": min(snippet_lines, len(content.splitlines())),
                "text": snippet_text,
            }
        )

    relationships = [
        {
            "source": r.source_path,
            "imported": r.imported_module,
            "target": r.target_path,
            "line": r.lineno,
            "resolution": r.resolution,
            "relative": r.is_relative,
        }
        for r in relations[:DEFAULT_MAX_RELATIONSHIPS]
    ]

    package: Dict[str, Any] = {
        "repository": dict(repo_meta),
        "readme_excerpt": truncate_text(readme or "", 2000),
        "files": [
            {
                "path": m.path,
                "module_name": m.module_name,
                "lines": m.line_count,
                "parseable": m.parseable,
                "classes": [c.name for c in m.classes],
                "functions": [f.qualified_name for f in m.functions if not f.is_method],
                "methods": [
                    f"{c.name}.{meth.name}"
                    for c in m.classes
                    for meth in c.methods
                ],
                "imports": len(m.imports),
            }
            for m in sorted(modules.values(), key=lambda m: m.path)
        ],
        "entry_points": [ep.to_dict() for ep in entry_points],
        "relationships": relationships,
        "snippets": snippets,
        "budget": {
            "max_chars": max_chars,
            "max_snippets": max_snippets,
            "snippet_lines": snippet_lines,
        },
    }

    package["facts_text"] = format_static_facts(package)
    package["text"] = format_evidence(package, max_chars=max_chars)
    return package


def format_static_facts(package: Dict[str, Any], max_symbols: int = 220) -> str:
    """Render only the static-analysis facts (no source snippets)."""
    lines: List[str] = []
    repo = package.get("repository", {})
    lines.append(
        f"Repository: {repo.get('full_name', repo.get('name', 'unknown'))}"
    )
    if repo.get("description"):
        lines.append(f"Description: {repo['description']}")
    if repo.get("language"):
        lines.append(f"Primary language: {repo['language']}")
    lines.append(f"Default branch: {repo.get('default_branch', '?')}")
    lines.append(f"Revision: {repo.get('commit_sha', '?')}")
    lines.append("")

    if package.get("readme_excerpt"):
        lines.append("README excerpt:")
        lines.append(package["readme_excerpt"])
        lines.append("")

    lines.append("Detected entry points:")
    if package.get("entry_points"):
        for ep in package["entry_points"]:
            label = "EVIDENCE" if ep["confidence"] == "evidence" else "HEURISTIC"
            lines.append(f"- [{label}] `{ep['path']}`")
            for reason in ep["reasons"]:
                lines.append(f"    - {reason}")
    else:
        lines.append("- none detected")
    lines.append("")

    lines.append("Files and symbols:")
    for file_info in package.get("files", []):
        lines.append(
            f"- `{file_info['path']}` ({file_info['lines']} lines, "
            f"{len(file_info['classes'])} classes, {len(file_info['functions'])} functions)"
        )
        if not file_info.get("parseable", True):
            lines.append("    - NOT PARSEABLE (syntax error)")
        if file_info["classes"]:
            lines.append(f"    - classes: {', '.join(file_info['classes'][:10])}")
        if file_info["functions"]:
            lines.append(f"    - functions: {', '.join(file_info['functions'][:10])}")
    lines.append("")

    lines.append("Import relationships (static, not runtime calls):")
    if package.get("relationships"):
        for rel in package["relationships"]:
            target = rel.get("target") or "(external/unresolved)"
            lines.append(
                f"- `{rel['source']}` -> `{rel['imported']}` "
                f"[{rel['resolution']}] at line {rel['line']} -> {target}"
            )
    else:
        lines.append("- none")

    text = "\n".join(lines)
    return truncate_text(text, max_chars=20000)


def format_evidence(package: Dict[str, Any], max_chars: int = DEFAULT_MAX_CHARS) -> str:
    """Render the full evidence package (facts + snippets), bounded by chars."""
    parts: List[str] = [format_static_facts(package)]

    snippets = package.get("snippets", [])
    if snippets:
        parts.append("")
        parts.append("Source snippets (line-numbered):")
        for snippet in snippets:
            parts.append("")
            parts.append(
                f"--- {snippet['path']} (lines {snippet['start_line']}-"
                f"{snippet['end_line']}) ---"
            )
            parts.append(snippet["text"])

    combined = "\n".join(parts)
    return truncate_text(combined, max_chars=max_chars)


def build_context_for_question(
    package: Dict[str, Any],
    max_chars: int = DEFAULT_MAX_CHARS,
) -> str:
    """Return the model-ready evidence text for a package."""
    text = package.get("text")
    if text:
        return truncate_text(text, max_chars)
    return format_evidence(package, max_chars=max_chars)
