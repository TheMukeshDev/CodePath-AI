"""Static analysis of Python source files using the built-in ``ast`` module.

The scanner never imports, evaluates, or executes analyzed code. It only parses
source text into an abstract syntax tree and extracts navigation-oriented
facts: modules, classes, functions, methods, documentation strings, and import
statements.

A syntactically invalid file yields a :class:`ModuleInfo` with
``parseable=False`` instead of raising, so one broken file cannot abort a
repository-wide analysis.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# --------------------------------------------------------------------------- #
# Data containers
# --------------------------------------------------------------------------- #
@dataclass
class ImportInfo:
    module: Optional[str]
    names: List[str]
    level: int
    lineno: int
    is_relative: bool
    statement: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "module": self.module,
            "names": list(self.names),
            "level": self.level,
            "lineno": self.lineno,
            "is_relative": self.is_relative,
            "statement": self.statement,
        }


@dataclass
class FunctionInfo:
    name: str
    qualified_name: str
    lineno: int
    end_lineno: int
    args: List[str] = field(default_factory=list)
    decorators: List[str] = field(default_factory=list)
    docstring: Optional[str] = None
    is_method: bool = False
    class_name: Optional[str] = None
    is_nested: bool = False
    is_async: bool = False

    @property
    def line_range(self) -> str:
        return f"{self.lineno}-{self.end_lineno}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "qualified_name": self.qualified_name,
            "lineno": self.lineno,
            "end_lineno": self.end_lineno,
            "args": list(self.args),
            "decorators": list(self.decorators),
            "docstring": self.docstring,
            "is_method": self.is_method,
            "class_name": self.class_name,
            "is_nested": self.is_nested,
            "is_async": self.is_async,
        }


@dataclass
class ClassInfo:
    name: str
    lineno: int
    end_lineno: int
    bases: List[str] = field(default_factory=list)
    decorators: List[str] = field(default_factory=list)
    docstring: Optional[str] = None
    methods: List[FunctionInfo] = field(default_factory=list)

    @property
    def line_range(self) -> str:
        return f"{self.lineno}-{self.end_lineno}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "lineno": self.lineno,
            "end_lineno": self.end_lineno,
            "bases": list(self.bases),
            "decorators": list(self.decorators),
            "docstring": self.docstring,
            "methods": [m.to_dict() for m in self.methods],
        }


@dataclass
class ModuleInfo:
    path: str
    module_name: str
    docstring: Optional[str] = None
    classes: List[ClassInfo] = field(default_factory=list)
    functions: List[FunctionInfo] = field(default_factory=list)
    imports: List[ImportInfo] = field(default_factory=list)
    parseable: bool = True
    syntax_error: Optional[str] = None
    line_count: int = 0
    has_main_guard: bool = False
    main_guard_lines: List[int] = field(default_factory=list)
    top_level_names: List[str] = field(default_factory=list)

    @property
    def all_functions(self) -> List[FunctionInfo]:
        """Top-level and nested functions, plus methods."""
        result = list(self.functions)
        for cls in self.classes:
            result.extend(cls.methods)
        return result

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "module_name": self.module_name,
            "docstring": self.docstring,
            "classes": [c.to_dict() for c in self.classes],
            "functions": [f.to_dict() for f in self.functions],
            "imports": [i.to_dict() for i in self.imports],
            "parseable": self.parseable,
            "syntax_error": self.syntax_error,
            "line_count": self.line_count,
            "has_main_guard": self.has_main_guard,
            "main_guard_lines": list(self.main_guard_lines),
            "top_level_names": list(self.top_level_names),
        }


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def module_name_from_path(path: str) -> str:
    """Convert a repository file path into a dotted module name.

    ``pkg/sub/mod.py`` -> ``pkg.sub.mod`` and ``pkg/sub/__init__.py`` ->
    ``pkg.sub``.
    """
    normalised = path.replace("\\", "/").strip("/")
    if normalised.endswith(".pyi"):
        normalised = normalised[: -len(".pyi")]
    elif normalised.endswith(".py"):
        normalised = normalised[: -len(".py")]
    parts = [p for p in normalised.split("/") if p and p != "."]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _iter_decorators(node: ast.AST) -> List[str]:
    decorators: List[str] = []
    for dec in getattr(node, "decorator_list", []) or []:
        try:
            decorators.append(ast.unparse(dec))
        except Exception:  # pragma: no cover - unparse is best-effort
            decorators.append("<?>")
    return decorators


def _argument_names(args: ast.arguments) -> List[str]:
    names: List[str] = []
    posonly = list(getattr(args, "posonlyargs", []) or [])
    for arg in posonly + list(args.args):
        names.append(arg.arg)
    if args.vararg:
        names.append("*" + args.vararg.arg)
    for arg in args.kwonlyargs:
        names.append(arg.arg)
    if args.kwarg:
        names.append("**" + args.kwarg.arg)
    return names


def _function_info(
    node: ast.AST,
    qualified_name: str,
    *,
    is_method: bool,
    class_name: Optional[str],
    is_nested: bool,
) -> FunctionInfo:
    return FunctionInfo(
        name=node.name,
        qualified_name=qualified_name,
        lineno=node.lineno,
        end_lineno=getattr(node, "end_lineno", node.lineno) or node.lineno,
        args=_argument_names(node.args),
        decorators=_iter_decorators(node),
        docstring=ast.get_docstring(node),
        is_method=is_method,
        class_name=class_name,
        is_nested=is_nested,
        is_async=isinstance(node, ast.AsyncFunctionDef),
    )


def _walk_functions(
    body: List[ast.stmt],
    qualified_prefix: str,
    *,
    class_name: Optional[str],
    is_method: bool,
    is_nested: bool,
) -> List[FunctionInfo]:
    """Recursively collect function definitions, including nested ones."""
    functions: List[FunctionInfo] = []
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            qualified = f"{qualified_prefix}.{node.name}" if qualified_prefix else node.name
            functions.append(
                _function_info(
                    node,
                    qualified,
                    is_method=is_method,
                    class_name=class_name,
                    is_nested=is_nested,
                )
            )
            functions.extend(
                _walk_functions(
                    node.body,
                    qualified,
                    class_name=class_name,
                    is_method=is_method,
                    is_nested=True,
                )
            )
        elif isinstance(node, (ast.If, ast.For, ast.While, ast.With, ast.Try)):
            for field_name in ("body", "orelse", "finalbody"):
                nested_body = getattr(node, field_name, None)
                if nested_body:
                    functions.extend(
                        _walk_functions(
                            nested_body,
                            qualified_prefix,
                            class_name=class_name,
                            is_method=is_method,
                            is_nested=is_nested,
                        )
                    )
            for handler in getattr(node, "handlers", []) or []:
                functions.extend(
                    _walk_functions(
                        handler.body,
                        qualified_prefix,
                        class_name=class_name,
                        is_method=is_method,
                        is_nested=is_nested,
                    )
                )
    return functions


def _statement_text(node: ast.AST, source_lines: List[str]) -> str:
    segment = ast.get_source_segment("\n".join(source_lines), node)
    if segment:
        return segment.splitlines()[0].strip()
    try:
        return ast.unparse(node).strip()
    except Exception:  # pragma: no cover
        return f"<{type(node).__name__}>"


def _collect_imports(tree: ast.AST, source_lines: List[str]) -> List[ImportInfo]:
    imports: List[ImportInfo] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
            imports.append(
                ImportInfo(
                    module=None,
                    names=names,
                    level=0,
                    lineno=node.lineno,
                    is_relative=False,
                    statement=_statement_text(node, source_lines),
                )
            )
        elif isinstance(node, ast.ImportFrom):
            names = [alias.name for alias in node.names]
            imports.append(
                ImportInfo(
                    module=node.module,
                    names=names,
                    level=node.level or 0,
                    lineno=node.lineno,
                    is_relative=bool(node.level),
                    statement=_statement_text(node, source_lines),
                )
            )
    imports.sort(key=lambda i: i.lineno)
    return imports


def _has_main_guard(tree: ast.Module) -> List[int]:
    lines: List[int] = []
    for node in tree.body:
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if (
            isinstance(test, ast.Compare)
            and isinstance(test.left, ast.Name)
            and test.left.id == "__name__"
            and len(test.comparators) == 1
        ):
            comparator = test.comparators[0]
            if isinstance(comparator, ast.Constant) and comparator.value == "__main__":
                for op in test.ops:
                    if isinstance(op, ast.Eq):
                        lines.append(node.lineno)
                        break
    return lines


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def analyze_source(path: str, source: str) -> ModuleInfo:
    """Parse *source* and extract navigation facts.

    Never raises for invalid Python; returns ``parseable=False`` instead.
    """
    module_name = module_name_from_path(path)
    source_lines = source.splitlines()
    line_count = len(source_lines)

    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        location = f"line {exc.lineno}" if exc.lineno else "unknown line"
        return ModuleInfo(
            path=path,
            module_name=module_name,
            parseable=False,
            syntax_error=f"{exc.__class__.__name__}: {exc.msg} ({location})",
            line_count=line_count,
        )
    except (ValueError, RecursionError) as exc:  # pragma: no cover - defensive
        return ModuleInfo(
            path=path,
            module_name=module_name,
            parseable=False,
            syntax_error=f"{exc.__class__.__name__}: {exc}",
            line_count=line_count,
        )

    classes: List[ClassInfo] = []
    functions: List[FunctionInfo] = []
    top_level_names: List[str] = []

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            class_name = node.name
            methods: List[FunctionInfo] = []
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    qualified = f"{class_name}.{child.name}"
                    methods.append(
                        _function_info(
                            child,
                            qualified,
                            is_method=True,
                            class_name=class_name,
                            is_nested=False,
                        )
                    )
                    methods.extend(
                        _walk_functions(
                            child.body,
                            qualified,
                            class_name=class_name,
                            is_method=True,
                            is_nested=True,
                        )
                    )
            methods.sort(key=lambda m: m.lineno)
            bases = []
            for base in node.bases:
                try:
                    bases.append(ast.unparse(base))
                except Exception:  # pragma: no cover
                    bases.append("<?>")
            classes.append(
                ClassInfo(
                    name=class_name,
                    lineno=node.lineno,
                    end_lineno=getattr(node, "end_lineno", node.lineno) or node.lineno,
                    bases=bases,
                    decorators=_iter_decorators(node),
                    docstring=ast.get_docstring(node),
                    methods=methods,
                )
            )
            top_level_names.append(class_name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            info = _function_info(
                node,
                node.name,
                is_method=False,
                class_name=None,
                is_nested=False,
            )
            functions.append(info)
            functions.extend(
                _walk_functions(
                    node.body,
                    node.name,
                    class_name=None,
                    is_method=False,
                    is_nested=True,
                )
            )
            top_level_names.append(node.name)

    functions.sort(key=lambda f: f.lineno)
    classes.sort(key=lambda c: c.lineno)
    main_guard_lines = _has_main_guard(tree)

    return ModuleInfo(
        path=path,
        module_name=module_name,
        docstring=ast.get_docstring(tree),
        classes=classes,
        functions=functions,
        imports=_collect_imports(tree, source_lines),
        parseable=True,
        syntax_error=None,
        line_count=line_count,
        has_main_guard=bool(main_guard_lines),
        main_guard_lines=main_guard_lines,
        top_level_names=top_level_names,
    )


def analyze_files(
    files: Dict[str, str],
    cache: Optional[Any] = None,
    repository: str = "",
    revision: str = "",
) -> Dict[str, ModuleInfo]:
    """Analyze many ``{path: source}`` pairs.

    If *cache* is provided it must implement ``get(repository, revision, path)``
    and ``set(repository, revision, path, value)``; parsed dictionaries are
    cached and re-hydrated when present.
    """
    modules: Dict[str, ModuleInfo] = {}
    for path, source in files.items():
        if cache is not None:
            cached = cache.get(repository, revision, path)
            if cached:
                modules[path] = _module_from_dict(cached)
                continue
        info = analyze_source(path, source)
        modules[path] = info
        if cache is not None:
            cache.set(repository, revision, path, info.to_dict())
    return modules


def _module_from_dict(data: Dict[str, Any]) -> ModuleInfo:
    classes = [
        ClassInfo(
            name=c["name"],
            lineno=c["lineno"],
            end_lineno=c["end_lineno"],
            bases=list(c.get("bases", [])),
            decorators=list(c.get("decorators", [])),
            docstring=c.get("docstring"),
            methods=[
                FunctionInfo(**{k: v for k, v in m.items()}) for m in c.get("methods", [])
            ],
        )
        for c in data.get("classes", [])
    ]
    functions = [FunctionInfo(**f) for f in data.get("functions", [])]
    imports = [ImportInfo(**i) for i in data.get("imports", [])]
    return ModuleInfo(
        path=data["path"],
        module_name=data["module_name"],
        docstring=data.get("docstring"),
        classes=classes,
        functions=functions,
        imports=imports,
        parseable=data.get("parseable", True),
        syntax_error=data.get("syntax_error"),
        line_count=data.get("line_count", 0),
        has_main_guard=data.get("has_main_guard", False),
        main_guard_lines=list(data.get("main_guard_lines", [])),
        top_level_names=list(data.get("top_level_names", [])),
    )
