"""Optional local-AI helpers for Phase 4 (contribution guidance).

Two things can be asked of the local model:

* explain a single file that the linker flagged as relevant, and
* draft an ordered contribution plan for a chosen issue.

Every entry point here has a deterministic fallback, so the feature works with
no model installed. The same non-negotiables as Phase 3 apply:

* **No fabrication.** Model output is validated. File paths are checked against
  the real path; named symbols wrapped in backticks are checked against the
  symbols that actually exist in the parsed file. Anything invented is dropped
  and reported.
* **Untrusted text.** File contents and the contribution guide are embedded in
  explicitly delimited untrusted blocks and never treated as instructions.
* **Bounded.** Evidence is truncated to a fixed character budget.
* **No scoring.** This module never produces or edits any numeric score.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from analyzer.contribution_guide import ContributionGuide, format_guide
from analyzer.file_scanner import ModuleInfo
from analyzer.recommender import ContributorProfile
from ai.model_client import OllamaClient, OllamaError
from ai.prompts import (
    FILE_EXPLAIN_SYSTEM_PROMPT,
    PLAN_SYSTEM_PROMPT,
    build_file_explanation_prompt,
    build_plan_prompt,
)
from ai.structured_output import (
    clean_list,
    clean_text,
    extract_json_object,
    strip_control,
)

MAX_EVIDENCE_CHARS = 6000
MAX_SYMBOL_ITEMS = 12
MAX_STEP_ITEMS = 10


# --------------------------------------------------------------------------- #
# Result containers
# --------------------------------------------------------------------------- #
@dataclass
class FileExplanation:
    file_path: str
    summary: str = ""
    key_symbols: List[str] = field(default_factory=list)
    how_it_might_relate: str = ""
    beginner_notes: List[str] = field(default_factory=list)
    limitations: List[str] = field(default_factory=list)
    dropped_symbols: List[str] = field(default_factory=list)
    parsed_ok: bool = False
    used_model: bool = False
    error: Optional[str] = None
    raw_text: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file_path": self.file_path,
            "summary": self.summary,
            "key_symbols": list(self.key_symbols),
            "how_it_might_relate": self.how_it_might_relate,
            "beginner_notes": list(self.beginner_notes),
            "limitations": list(self.limitations),
            "dropped_symbols": list(self.dropped_symbols),
            "parsed_ok": self.parsed_ok,
            "used_model": self.used_model,
            "error": self.error,
        }


@dataclass
class PlanDraft:
    issue_number: int
    plan_steps: List[str] = field(default_factory=list)
    questions_to_ask: List[str] = field(default_factory=list)
    things_to_watch: List[str] = field(default_factory=list)
    limitations: List[str] = field(default_factory=list)
    parsed_ok: bool = False
    used_model: bool = False
    error: Optional[str] = None
    raw_text: str = ""

    @property
    def is_hypothesis(self) -> bool:
        """Plans are always suggestions, never guarantees."""

        return True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "issue_number": self.issue_number,
            "plan_steps": list(self.plan_steps),
            "questions_to_ask": list(self.questions_to_ask),
            "things_to_watch": list(self.things_to_watch),
            "limitations": list(self.limitations),
            "parsed_ok": self.parsed_ok,
            "used_model": self.used_model,
            "error": self.error,
            "is_hypothesis": True,
        }


# --------------------------------------------------------------------------- #
# Evidence builders
# --------------------------------------------------------------------------- #
def build_file_evidence(
    file_path: str, module: Optional[ModuleInfo], snippet: Optional[str] = None
) -> str:
    """Compose bounded static facts (and an optional snippet) for one file."""

    lines: List[str] = [f"## File", f"path: {file_path}"]
    if module is None:
        lines.append("(no static analysis available for this file)")
    else:
        lines.append(f"module_name: {module.module_name}")
        lines.append(f"parseable: {module.parseable}")
        if module.docstring:
            lines.append("docstring: " + clean_text(module.docstring, 600))
        if module.classes:
            lines.append("classes:")
            for cls in module.classes[:20]:
                bases = f" (bases: {', '.join(cls.bases)})" if cls.bases else ""
                methods = ", ".join(m.name for m in cls.methods[:12])
                lines.append(
                    f"  - {cls.name} at line {cls.lineno}{bases}; "
                    f"methods: {methods or '(none)'}"
                )
        if module.functions:
            lines.append("top-level functions:")
            for func in module.functions[:25]:
                args = ", ".join(func.args)
                lines.append(f"  - {func.name}({args}) at line {func.lineno}")
        if module.imports:
            lines.append("imports:")
            for imp in module.imports[:25]:
                lines.append(f"  - {imp.statement.strip()} (line {imp.lineno})")
    if snippet:
        lines.append("")
        lines.append("## Source snippet (line-numbered, untrusted)")
        lines.append(strip_control(snippet))

    text = "\n".join(lines)
    if len(text) > MAX_EVIDENCE_CHARS:
        text = text[:MAX_EVIDENCE_CHARS] + "\n… [evidence truncated]"
    return text


def _module_symbol_names(module: Optional[ModuleInfo]) -> List[str]:
    if module is None:
        return []
    names: List[str] = []
    for cls in module.classes:
        names.append(cls.name)
        for method in cls.methods:
            names.append(method.name)
    for func in module.functions:
        names.append(func.name)
    names.extend(module.top_level_names)
    return list(dict.fromkeys(n for n in names if n))


# --------------------------------------------------------------------------- #
# Fallbacks
# --------------------------------------------------------------------------- #
def _fallback_file_explanation(
    file_path: str,
    module: Optional[ModuleInfo],
    relate_hint: str = "",
    error: Optional[str] = None,
) -> FileExplanation:
    if module is None:
        return FileExplanation(
            file_path=file_path,
            summary=f"No static analysis is available for `{file_path}`.",
            how_it_might_relate=relate_hint,
            limitations=["This file was not parsed, so no symbols are known."],
            error=error,
        )

    classes = ", ".join(cls.name for cls in module.classes) or "none"
    functions = ", ".join(func.name for func in module.functions) or "none"
    summary = clean_text(module.docstring, 400) if module.docstring else (
        f"`{file_path}` defines {len(module.classes)} class(es) and "
        f"{len(module.functions)} top-level function(s)."
    )

    key_symbols: List[str] = []
    for cls in module.classes[:6]:
        key_symbols.append(f"class {cls.name} (line {cls.lineno})")
    for func in module.functions[:6]:
        key_symbols.append(f"function {func.name} (line {func.lineno})")

    notes = [
        f"Read the file from the top; it defines classes: {classes}.",
        f"Top-level functions include: {functions}.",
        "Line numbers refer to the file at the analysed revision.",
    ]
    if module.imports:
        notes.append(
            "It imports " + ", ".join(
                (imp.module or ", ".join(imp.names)) for imp in module.imports[:5]
            ) + "."
        )

    return FileExplanation(
        file_path=file_path,
        summary=summary,
        key_symbols=key_symbols,
        how_it_might_relate=relate_hint
        or "Relationship to the issue is not established; verify by reading the file.",
        beginner_notes=notes,
        limitations=[
            "Generated without a local model (deterministic fallback).",
            "Runtime behaviour cannot be determined from static analysis.",
        ],
        parsed_ok=False,
        used_model=False,
        error=error,
    )


def _fallback_plan(
    guide: ContributionGuide, error: Optional[str] = None
) -> PlanDraft:
    watch = [r.text for r in guide.risks if r.is_hypothesis]
    watch += [f"Fact to respect: {r.text}" for r in guide.risks if not r.is_hypothesis]
    limitations = list(guide.unknowns) or ["Nothing further identified."]
    return PlanDraft(
        issue_number=guide.issue_number,
        plan_steps=list(guide.investigation_sequence)[:MAX_STEP_ITEMS],
        questions_to_ask=list(guide.questions)[:MAX_SYMBOL_ITEMS],
        things_to_watch=watch[:MAX_SYMBOL_ITEMS],
        limitations=limitations,
        parsed_ok=False,
        used_model=False,
        error=error,
    )


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
def _validate_symbols(
    raw: List[str], known: Sequence[str]
) -> tuple[List[str], List[str]]:
    """Keep entries whose backticked names exist; drop invented ones."""

    if not known:
        return raw, []
    known_lower = {name.lower() for name in known}
    kept: List[str] = []
    dropped: List[str] = []
    for entry in raw:
        quoted = [
            token.strip()
            for token in entry.split("`")
            if token.strip() and " " not in token.strip()
        ]
        invented = [
            token
            for token in quoted
            if token.lower().split("(")[0].strip() not in known_lower
        ]
        if invented:
            dropped.append(entry)
        else:
            kept.append(entry)
    return kept, dropped


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def explain_file(
    file_path: str,
    module: Optional[ModuleInfo] = None,
    snippet: Optional[str] = None,
    relate_hint: str = "",
    client: Optional[OllamaClient] = None,
    model: Optional[str] = None,
    repo_full_name: str = "",
    system_prompt: str = FILE_EXPLAIN_SYSTEM_PROMPT,
    timeout: Optional[int] = None,
) -> FileExplanation:
    """Explain one file with the local model, falling back deterministically."""

    known = _module_symbol_names(module)
    if client is None:
        return _fallback_file_explanation(
            file_path, module, relate_hint, error="No local model configured."
        )

    evidence = build_file_evidence(file_path, module, snippet)
    prompt = build_file_explanation_prompt(
        repo_full_name=repo_full_name or "(unspecified)",
        file_path=file_path,
        evidence_text=evidence,
    )

    try:
        raw = client.generate(prompt, system=system_prompt, model=model, timeout=timeout)
    except OllamaError as exc:
        return _fallback_file_explanation(file_path, module, relate_hint, error=str(exc))
    except Exception as exc:  # pragma: no cover - last-resort guard
        return _fallback_file_explanation(
            file_path, module, relate_hint, error=f"Unexpected model error: {exc}"
        )

    data = extract_json_object(raw)
    if not data:
        result = _fallback_file_explanation(
            file_path,
            module,
            relate_hint,
            error="Model did not return parseable JSON; showing static facts.",
        )
        result.used_model = True
        result.raw_text = (raw or "")[:4000]
        result.limitations.append("Model output was not valid JSON and was discarded.")
        return result

    result = FileExplanation(
        file_path=file_path, parsed_ok=True, used_model=True
    )
    result.summary = clean_text(data.get("summary"), 800)
    result.how_it_might_relate = clean_text(data.get("how_it_might_relate"), 800)
    result.beginner_notes = clean_list(data.get("beginner_notes"))
    result.limitations = clean_list(data.get("limitations"))

    raw_symbols = clean_list(data.get("key_symbols"), limit=MAX_SYMBOL_ITEMS)
    kept, dropped = _validate_symbols(raw_symbols, known)
    result.key_symbols = kept
    result.dropped_symbols = dropped
    if dropped:
        result.limitations.append(
            "Some symbols named by the model do not exist in this file and were "
            "removed: " + ", ".join(dropped[:5])
        )
    if not result.summary:
        result.summary = f"`{file_path}`"
    result.raw_text = (raw or "")[:4000]
    return result


def draft_contribution_plan(
    guide: ContributionGuide,
    profile: Optional[ContributorProfile] = None,
    client: Optional[OllamaClient] = None,
    model: Optional[str] = None,
    system_prompt: str = PLAN_SYSTEM_PROMPT,
    timeout: Optional[int] = None,
) -> PlanDraft:
    """Draft an ordered contribution plan, falling back to the deterministic guide."""

    if client is None:
        return _fallback_plan(guide, error="No local model configured.")

    profile_text = "{}"
    if profile is not None:
        profile_text = clean_text(str(profile.to_dict()), 1200)

    prompt = build_plan_prompt(
        repo_full_name=guide.repo_full_name or "(unspecified)",
        issue_number=guide.issue_number,
        guide_text=format_guide(guide),
        profile_text=profile_text,
    )

    try:
        raw = client.generate(prompt, system=system_prompt, model=model, timeout=timeout)
    except OllamaError as exc:
        return _fallback_plan(guide, error=str(exc))
    except Exception as exc:  # pragma: no cover - last-resort guard
        return _fallback_plan(guide, error=f"Unexpected model error: {exc}")

    data = extract_json_object(raw)
    if not data:
        result = _fallback_plan(
            guide,
            error="Model did not return parseable JSON; showing the deterministic guide.",
        )
        result.used_model = True
        result.raw_text = (raw or "")[:4000]
        result.limitations.append("Model output was not valid JSON and was discarded.")
        return result

    steps = clean_list(data.get("plan_steps"), limit=MAX_STEP_ITEMS, item_max=300)
    if not steps:
        return _fallback_plan(
            guide, error="Model returned no usable plan steps; showing the guide."
        )

    return PlanDraft(
        issue_number=guide.issue_number,
        plan_steps=steps,
        questions_to_ask=clean_list(data.get("questions_to_ask")),
        things_to_watch=clean_list(data.get("things_to_watch")),
        limitations=clean_list(data.get("limitations")),
        parsed_ok=True,
        used_model=True,
        raw_text=(raw or "")[:4000],
    )
