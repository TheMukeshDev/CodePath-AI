"""Tests for the AST-based file scanner."""

from __future__ import annotations

from analyzer.cache import AnalysisCache
from analyzer.file_scanner import (
    ImportInfo,
    analyze_files,
    analyze_source,
    module_name_from_path,
)

SAMPLE = '''"""Module docstring."""

import os
import sys as system
from collections import OrderedDict
from . import sibling
from .utils import helper
from ..pkg import thing


def top_level(a, b=1, *args, **kwargs):
    """Top docstring."""
    def nested(x):
        return x

    return nested(a)


class Widget:
    """A widget."""

    def __init__(self, name):
        self.name = name

    async def render(self):
        return self.name

    class Inner:
        def deep(self):
            return 1


if __name__ == "__main__":
    top_level(1)
'''


def test_extracts_classes_and_functions():
    module = analyze_source("pkg/mod.py", SAMPLE)
    assert module.parseable
    assert module.module_name == "pkg.mod"
    assert module.docstring == "Module docstring."
    assert [c.name for c in module.classes] == ["Widget"]
    top = [f for f in module.functions if not f.is_nested]
    assert [f.name for f in top] == ["top_level"]
    assert top[0].docstring == "Top docstring."


def test_extracts_methods_and_nested_functions():
    module = analyze_source("pkg/mod.py", SAMPLE)
    widget = module.classes[0]
    method_names = [m.name for m in widget.methods]
    assert "__init__" in method_names
    assert "render" in method_names
    # Nested function inside top_level.
    nested = [f for f in module.functions if f.is_nested]
    assert any(f.name == "nested" for f in nested)
    render = next(m for m in widget.methods if m.name == "render")
    assert render.is_async is True
    assert render.is_method is True


def test_function_arguments_and_line_ranges():
    module = analyze_source("pkg/mod.py", SAMPLE)
    top = module.functions[0]
    assert "a" in top.args and "b" in top.args
    assert "*args" in top.args and "**kwargs" in top.args
    assert top.lineno < top.end_lineno


def test_imports_absolute_and_relative():
    module = analyze_source("pkg/mod.py", SAMPLE)
    statements = {i.statement for i in module.imports}
    assert any("import os" in s for s in statements)
    assert any("from collections import OrderedDict" in s for s in statements)
    relative = [i for i in module.imports if i.is_relative]
    assert {i.level for i in relative} == {1, 2}
    assert all(isinstance(i, ImportInfo) for i in module.imports)


def test_main_guard_detection():
    module = analyze_source("main.py", SAMPLE)
    assert module.has_main_guard
    assert module.main_guard_lines


def test_syntax_error_is_graceful():
    module = analyze_source("broken.py", "def oops(:\n    pass\n")
    assert module.parseable is False
    assert module.syntax_error
    assert module.classes == [] and module.functions == []


def test_empty_file():
    module = analyze_source("empty.py", "")
    assert module.parseable
    assert module.line_count == 0
    assert module.classes == []
    assert module.functions == []
    assert module.imports == []


def test_module_name_from_path():
    assert module_name_from_path("a/b/c.py") == "a.b.c"
    assert module_name_from_path("a/b/__init__.py") == "a.b"
    assert module_name_from_path("mod.pyi") == "mod"
    assert module_name_from_path("__init__.py") == ""


def test_analyze_files_with_cache_round_trip():
    cache = AnalysisCache()
    files = {"pkg/mod.py": SAMPLE, "broken.py": "def x(:\n"}
    first = analyze_files(files, cache=cache, repository="acme/demo", revision="sha1")
    assert len(cache) == 2
    # Second run should be served from cache and still produce equivalent data.
    second = analyze_files(files, cache=cache, repository="acme/demo", revision="sha1")
    assert first["pkg/mod.py"].module_name == second["pkg/mod.py"].module_name
    assert second["broken.py"].parseable is False
