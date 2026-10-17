"""Entry-point detection and module relationship mapping.

Everything here is derived from static analysis of the retrieved source files.
No code is executed. Results are plain JSON-compatible Python objects so a
visualisation can be layered on later without a graph database.

Important caveat baked into the output: an import statement proves that a
module is *referenced*, not that any function from it is *called* at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .file_scanner import ModuleInfo

# Filenames conventionally used for application/CLI entry points.
COMMON_ENTRY_FILENAMES = {
    "main.py",
    "app.py",
    "cli.py",
    "run.py",
    "wsgi.py",
    "asgi.py",
    "manage.py",
    "__main__.py",
}

_FRAMEWORK_HINTS = {
    "streamlit": "Streamlit",
    "flask": "Flask",
    "fastapi": "FastAPI",
    "django": "Django",
    "click": "Click (CLI)",
    "typer": "Typer (CLI)",
    "argparse": "argparse (CLI)",
}


# --------------------------------------------------------------------------- #
# Entry points
# --------------------------------------------------------------------------- #
@dataclass
class EntryPointCandidate:
    path: str
    module_name: str
    confidence: str  # "evidence" | "heuristic"
    reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "module_name": self.module_name,
            "confidence": self.confidence,
            "reasons": list(self.reasons),
        }


def _basename(path: str) -> str:
    return path.replace("\\", "/").rsplit("/", 1)[-1]


def _imported_root_names(module: ModuleInfo) -> Dict[str, int]:
    """Map imported top-level module names to the line they appear on."""
    found: Dict[str, int] = {}
    for imp in module.imports:
        if imp.module:
            root = imp.module.split(".")[0]
            found.setdefault(root, imp.lineno)
        for name in imp.names:
            root = name.split(".")[0]
            found.setdefault(root, imp.lineno)
    return found


def detect_entry_points(
    modules: Dict[str, ModuleInfo]
) -> List[EntryPointCandidate]:
    """Identify likely entry points with an explicit reason for each.

    Only a ``__main__`` guard or a ``__main__.py`` module is treated as
    *evidence*. Filename and framework conventions are labelled heuristics.
    """
    candidates: List[EntryPointCandidate] = []

    for path, module in modules.items():
        if not module.parseable:
            continue
        basename = _basename(path)
        evidence: List[str] = []
        heuristics: List[str] = []

        if module.has_main_guard:
            lines = ", ".join(str(n) for n in module.main_guard_lines)
            evidence.append(
                f"Contains an `if __name__ == \"__main__\":` guard (line {lines})."
            )

        if basename == "__main__.py":
            evidence.append(
                "Named `__main__.py`, which Python runs for `python -m package`."
            )

        if basename in COMMON_ENTRY_FILENAMES and basename != "__main__.py":
            heuristics.append(
                f"Filename `{basename}` follows a common entry-point convention."
            )

        imported = _imported_root_names(module)
        for root, lineno in imported.items():
            framework = _FRAMEWORK_HINTS.get(root)
            if framework and basename in {"app.py", "main.py", "manage.py", "cli.py"}:
                heuristics.append(
                    f"Imports `{root}` ({framework}) at line {lineno}, suggesting a "
                    "framework entry module."
                )

        if not evidence and not heuristics:
            continue

        confidence = "evidence" if evidence else "heuristic"
        candidates.append(
            EntryPointCandidate(
                path=path,
                module_name=module.module_name or path,
                confidence=confidence,
                reasons=evidence + heuristics,
            )
        )

    candidates.sort(key=lambda c: (c.confidence != "evidence", c.path))
    return candidates


# --------------------------------------------------------------------------- #
# Import resolution
# --------------------------------------------------------------------------- #
@dataclass
class ImportRelation:
    source_module: str
    source_path: str
    imported_module: str
    lineno: int
    statement: str
    resolution: str  # "exact" | "partial" | "unresolved"
    target_path: Optional[str] = None
    is_relative: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_module": self.source_module,
            "source_path": self.source_path,
            "imported_module": self.imported_module,
            "lineno": self.lineno,
            "statement": self.statement,
            "resolution": self.resolution,
            "target_path": self.target_path,
            "is_relative": self.is_relative,
        }


def _build_index(
    modules: Dict[str, ModuleInfo]
) -> Tuple[Dict[str, str], set]:
    module_index: Dict[str, str] = {}
    package_names: set = set()
    for path, module in modules.items():
        name = module.module_name
        if not name:
            continue
        module_index[name] = path
        if _basename(path) == "__init__.py":
            package_names.add(name)
    for name in list(module_index):
        parts = name.split(".")
        for i in range(1, len(parts)):
            package_names.add(".".join(parts[:i]))
    return module_index, package_names


def _is_package_path(path: str) -> bool:
    return _basename(path) == "__init__.py"


def _package_of(module: ModuleInfo) -> str:
    name = module.module_name
    if _is_package_path(module.path):
        return name
    if "." in name:
        return name.rsplit(".", 1)[0]
    return ""


def _resolve(
    name: str,
    module_index: Dict[str, str],
    package_names: set,
) -> Tuple[str, Optional[str]]:
    """Resolve a dotted module name to a repository file.

    Returns ``(resolution, target_path)`` where resolution is one of
    ``exact``, ``partial`` or ``unresolved``.
    """
    if not name:
        return "unresolved", None

    if name in module_index:
        return "exact", module_index[name]

    # ``from pkg.mod import Thing`` -> module is ``pkg.mod`` (exact already).
    # ``import pkg.mod.thing`` where thing is an attribute, not a module:
    parts = name.split(".")
    for i in range(len(parts) - 1, 0, -1):
        prefix = ".".join(parts[:i])
        if prefix in module_index:
            return "exact", module_index[prefix]

    if name in package_names:
        return "partial", None

    for i in range(len(parts) - 1, 0, -1):
        prefix = ".".join(parts[:i])
        if prefix in module_index:
            return "partial", module_index[prefix]
        if prefix in package_names:
            return "partial", None

    return "unresolved", None


def _relative_target(module: ModuleInfo, imported: Optional[str], level: int) -> str:
    package = _package_of(module)
    base_parts = package.split(".") if package else []
    drop = max(level - 1, 0)
    if drop:
        base_parts = base_parts[: max(len(base_parts) - drop, 0)]
    base = ".".join(base_parts)
    if imported:
        return f"{base}.{imported}" if base else imported
    return base


def resolve_imports(modules: Dict[str, ModuleInfo]) -> List[ImportRelation]:
    """Build resolved import relations for every module in *modules*."""
    module_index, package_names = _build_index(modules)
    relations: List[ImportRelation] = []

    for path, module in modules.items():
        if not module.parseable:
            continue
        for imp in module.imports:
            if imp.module is None and not imp.is_relative:
                # ``import a`` / ``import a.b``
                for target in imp.names:
                    resolution, target_path = _resolve(
                        target, module_index, package_names
                    )
                    relations.append(
                        ImportRelation(
                            source_module=module.module_name,
                            source_path=path,
                            imported_module=target,
                            lineno=imp.lineno,
                            statement=imp.statement,
                            resolution=resolution,
                            target_path=target_path,
                            is_relative=False,
                        )
                    )
            elif imp.module is None and imp.is_relative:
                # ``from . import name``: each name may be a submodule.
                base = _relative_target(module, None, imp.level)
                for name in imp.names:
                    candidate = f"{base}.{name}" if base else name
                    resolution, target_path = _resolve(
                        candidate, module_index, package_names
                    )
                    if resolution != "exact":
                        base_res, base_target = _resolve(
                            base, module_index, package_names
                        )
                        if base_res != "unresolved":
                            resolution, target_path = base_res, base_target
                    relations.append(
                        ImportRelation(
                            source_module=module.module_name,
                            source_path=path,
                            imported_module=name,
                            lineno=imp.lineno,
                            statement=imp.statement,
                            resolution=resolution,
                            target_path=target_path,
                            is_relative=True,
                        )
                    )
            else:
                if imp.is_relative:
                    target_name = _relative_target(module, imp.module, imp.level)
                else:
                    target_name = imp.module
                resolution, target_path = _resolve(
                    target_name, module_index, package_names
                )
                relations.append(
                    ImportRelation(
                        source_module=module.module_name,
                        source_path=path,
                        imported_module=imp.module,
                        lineno=imp.lineno,
                        statement=imp.statement,
                        resolution=resolution,
                        target_path=target_path,
                        is_relative=imp.is_relative,
                    )
                )

    relations.sort(key=lambda r: (r.source_path, r.lineno))
    return relations


# --------------------------------------------------------------------------- #
# Graph
# --------------------------------------------------------------------------- #
def build_dependency_graph(
    modules: Dict[str, ModuleInfo],
    relations: List[ImportRelation],
) -> Dict[str, Any]:
    """Return a JSON-compatible dependency graph of nodes and edges."""
    nodes: List[Dict[str, Any]] = []
    for path, module in sorted(modules.items()):
        nodes.append(
            {
                "id": module.module_name or path,
                "path": path,
                "parseable": module.parseable,
                "classes": len(module.classes),
                "functions": len(module.all_functions),
                "imports": len(module.imports),
                "has_main_guard": module.has_main_guard,
            }
        )
    edges = [r.to_dict() for r in relations]
    return {"nodes": nodes, "edges": edges}


def summarize_resolution(relations: List[ImportRelation]) -> Dict[str, int]:
    counts = {"exact": 0, "partial": 0, "unresolved": 0}
    for relation in relations:
        counts[relation.resolution] = counts.get(relation.resolution, 0) + 1
    return counts
