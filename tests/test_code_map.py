"""Tests for entry-point detection and import mapping."""

from __future__ import annotations

import json

from analyzer.code_map import (
    build_dependency_graph,
    detect_entry_points,
    resolve_imports,
    summarize_resolution,
)
from analyzer.file_scanner import analyze_source

MODULES = {
    "main.py": '''import app
from pkg import util


def main():
    app.run()


if __name__ == "__main__":
    main()
''',
    "app.py": "import streamlit\n\n\ndef run():\n    pass\n",
    "pkg/__init__.py": "from .util import helper\n",
    "pkg/util.py": "import os\nimport requests\nfrom . import constants\nfrom pkg.deep.mod import thing\n",
    "pkg/deep/mod.py": "x = 1\n",
    "pkg/deep/__init__.py": "",
    "pkg/constants.py": "VALUE = 1\n",
    "widgets/__main__.py": "print('pkg entry')\n",
}


def _modules():
    return {path: analyze_source(path, src) for path, src in MODULES.items()}


def test_detect_entry_points_evidence_and_heuristics():
    entry_points = detect_entry_points(_modules())
    by_path = {ep.path: ep for ep in entry_points}

    main = by_path["main.py"]
    assert main.confidence == "evidence"
    assert any("__main__" in reason for reason in main.reasons)

    pkg_main = by_path["widgets/__main__.py"]
    assert pkg_main.confidence == "evidence"

    # app.py is a filename/framework heuristic, not evidence.
    app = by_path["app.py"]
    assert app.confidence == "heuristic"

    # Evidence candidates sort before heuristic ones.
    assert entry_points[0].confidence == "evidence"


def test_entry_point_explanations_present():
    entry_points = detect_entry_points(_modules())
    for ep in entry_points:
        assert ep.reasons, f"{ep.path} has no explanation"
        assert ep.confidence in {"evidence", "heuristic"}


def test_resolve_imports_absolute_exact_and_partial():
    relations = resolve_imports(_modules())
    lookup = {(r.source_path, r.imported_module): r for r in relations}

    # `import app` from main.py resolves exactly.
    assert lookup[("main.py", "app")].resolution == "exact"
    assert lookup[("main.py", "app")].target_path == "app.py"

    # `import requests` is external -> unresolved.
    assert lookup[("pkg/util.py", "requests")].resolution == "unresolved"

    # `from pkg.deep.mod import thing` resolves exactly.
    assert lookup[("pkg/util.py", "pkg.deep.mod")].resolution == "exact"
    assert lookup[("pkg/util.py", "pkg.deep.mod")].target_path == "pkg/deep/mod.py"


def test_resolve_relative_imports():
    relations = resolve_imports(_modules())
    lookup = {(r.source_path, r.imported_module, r.is_relative): r for r in relations}

    rel = lookup[("pkg/util.py", "constants", True)]
    assert rel.resolution == "exact"
    assert rel.target_path == "pkg/constants.py"

    # `from .util import helper` inside pkg/__init__.py resolves to pkg/util.py.
    rel2 = lookup[("pkg/__init__.py", "util", True)]
    assert rel2.target_path == "pkg/util.py"


def test_dependency_graph_is_json_serializable():
    modules = _modules()
    relations = resolve_imports(modules)
    graph = build_dependency_graph(modules, relations)
    assert "nodes" in graph and "edges" in graph
    # Must serialise without error.
    json.dumps(graph)
    assert any(node["path"] == "main.py" for node in graph["nodes"])
    assert graph["edges"]


def test_summarize_resolution_counts():
    relations = resolve_imports(_modules())
    counts = summarize_resolution(relations)
    assert set(counts) == {"exact", "partial", "unresolved"}
    assert counts["unresolved"] >= 1
    assert counts["exact"] >= 1


def test_unparseable_module_skipped_in_resolution():
    modules = _modules()
    modules["broken.py"] = analyze_source("broken.py", "def x(:\n")
    relations = resolve_imports(modules)
    assert all(r.source_module != "broken" for r in relations)
    entry_points = detect_entry_points(modules)
    assert all(ep.path != "broken.py" for ep in entry_points)
