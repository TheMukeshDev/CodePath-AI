"""CodePath AI — Streamlit interface.

Phase 1: repository explorer (metadata, README, file tree).
Phase 2: Python codebase intelligence (safe retrieval, static analysis,
entry-point detection, dependency mapping, evidence-grounded AI explanation).

Run with:  streamlit run app.py
"""

from __future__ import annotations

import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional

import streamlit as st

from ai.context_builder import (
    build_context_for_question,
    build_evidence_package,
    format_static_facts,
)
from ai.issue_explainer import explain_issue
from ai.model_client import (
    ModelNotAvailableError,
    OllamaClient,
    OllamaConnectionError,
    OllamaError,
    config_from_env,
)
from ai.prompts import SYSTEM_PROMPT, build_reading_order_prompt, build_user_prompt
from analyzer.cache import AnalysisCache
from analyzer.code_map import (
    build_dependency_graph,
    detect_entry_points,
    resolve_imports,
    summarize_resolution,
)
from analyzer.file_scanner import ModuleInfo, analyze_files
from analyzer.github_client import (
    DEFAULT_MAX_FILES,
    DEFAULT_MAX_FILE_BYTES,
    FileRetrievalResult,
    GitHubClient,
    GitHubError,
    InvalidRepoUrlError,
    RateLimitError,
    RepoMetadata,
    RepoNotFoundError,
    parse_github_url,
)
from analyzer.checklist import (
    CATEGORY_LABELS,
    build_checklist,
    checklist_key,
    group_by_category,
    progress,
)
from analyzer.contribution_guide import build_contribution_guide
from analyzer.issue_client import DEFAULT_ISSUE_LIMIT, IssueClient, IssueSearchResult
from analyzer.issue_linker import link_issue_to_files
from analyzer.learning_path import build_learning_path
from analyzer.recommender import (
    ContributorProfile,
    IssueRecommendation,
    MatchWeights,
    recommend,
)
from ai.contribution_assistant import draft_contribution_plan, explain_file

APP_TAGLINE = "Khojo Code. Samjho System. Contribute with Confidence."
BRAND_DIR = Path(__file__).parent / "public" / "brand"
FAVICON_PATH = Path(__file__).parent / "public" / "favicon-32x32.png"
LOGO_PATH = BRAND_DIR / "codepath-logo.png"


# --------------------------------------------------------------------------- #
# Session state helpers
# --------------------------------------------------------------------------- #
def _init_state() -> None:
    defaults: Dict[str, Any] = {
        "meta": None,
        "readme": "",
        "tree": None,
        "tree_truncated": False,
        "retrieval": None,
        "modules": None,
        "entry_points": None,
        "relations": None,
        "graph": None,
        "evidence": None,
        "ai_answer": None,
        "reading_order": None,
        "issue_search": None,
        "issue_recs": None,
        "issue_profile": None,
        "issue_expl": None,
        "guide_bundle": None,
        "guide_plan": None,
        "file_expl": None,
        "file_contents": None,
        "checklist_state": {},
        "_last_issue_idx": None,
        "cache": AnalysisCache(),
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def _reset_analysis() -> None:
    for key in (
        "retrieval",
        "modules",
        "entry_points",
        "relations",
        "graph",
        "evidence",
        "ai_answer",
        "reading_order",
        "file_contents",
        "guide_bundle",
        "guide_plan",
        "file_expl",
    ):
        st.session_state[key] = None


def _reset_issue_state() -> None:
    """Clear everything derived from a *previous* repository.

    Called whenever a new repository is fetched so issue rankings, guides,
    learning paths, and checklist progress can never leak across repositories.
    """
    st.session_state.issue_search = None
    st.session_state.issue_recs = None
    st.session_state.issue_profile = None
    st.session_state.issue_expl = None
    st.session_state.guide_bundle = None
    st.session_state.guide_plan = None
    st.session_state.file_expl = None
    st.session_state.checklist_state = {}
    # Drop the namespaced checklist widget keys too, otherwise a same-numbered
    # issue in the next repository could inherit stale checkbox values.
    for key in [k for k in st.session_state.keys() if str(k).startswith("chk::")]:
        st.session_state.pop(key, None)
    st.session_state["_last_issue_idx"] = None


def _load_token_default() -> str:
    """Return an optional GitHub token from the environment or a local `.env`.

    The token is **never required**: the app works fully unauthenticated and
    only uses a token when one is supplied to raise the rate limit. `.env` is
    parsed without any third-party dependency, and the value is never logged.
    """
    env_token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if env_token and env_token.strip():
        return env_token.strip()
    env_path = Path(__file__).parent / ".env"
    try:
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                name, _, value = line.partition("=")
                if name.strip() in ("GITHUB_TOKEN", "GH_TOKEN"):
                    return value.strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def _client(token: str) -> GitHubClient:
    return GitHubClient(token=token or None)


# --------------------------------------------------------------------------- #
# Actions
# --------------------------------------------------------------------------- #
def fetch_repository(url: str, token: str) -> None:
    owner, repo = parse_github_url(url)
    client = _client(token)
    with st.spinner("Fetching repository metadata…"):
        meta = client.get_repository(owner, repo)
    with st.spinner("Fetching README…"):
        readme = client.get_readme(owner, repo, ref=meta.default_branch)
    with st.spinner("Fetching file tree…"):
        entries, truncated = client.get_file_tree(owner, repo, meta.commit_sha or meta.default_branch)

    previous: Optional[RepoMetadata] = st.session_state.get("meta")
    previous_id = previous.full_name if previous is not None else None

    st.session_state.meta = meta
    st.session_state.readme = readme
    st.session_state.tree = entries
    st.session_state.tree_truncated = truncated
    _reset_analysis()
    if previous_id is None or previous_id.lower() != meta.full_name.lower():
        # A genuinely different repository — discard issue/guide state so it
        # cannot leak from the previously explored repository.
        _reset_issue_state()


def analyze_repository(max_files: int, max_file_kb: int, max_evidence_chars: int) -> None:
    meta: Optional[RepoMetadata] = st.session_state.meta
    if meta is None:
        st.warning("Fetch a repository first.")
        return

    client = _client(st.session_state.get("github_token", ""))
    max_file_bytes = max_file_kb * 1024

    with st.spinner("Retrieving Python source files safely…"):
        result: FileRetrievalResult = client.fetch_python_files(
            meta.owner,
            meta.name,
            ref=meta.commit_sha or meta.default_branch,
            max_files=max_files,
            max_file_bytes=max_file_bytes,
            metadata=meta,
        )

    file_contents = {f.path: f.content for f in result.ok_files if f.content is not None}
    if not file_contents:
        st.warning(
            "No analyzable Python source files were retrieved. "
            "See the skipped-file report below."
        )
        _reset_analysis()
        st.session_state.retrieval = result
        return

    repo_id = meta.full_name
    revision = meta.commit_sha or meta.default_branch
    cache: AnalysisCache = st.session_state.cache

    with st.spinner("Parsing source with the Python AST (no code is executed)…"):
        modules = analyze_files(file_contents, cache=cache, repository=repo_id, revision=revision)

    with st.spinner("Detecting entry points and mapping imports…"):
        entry_points = detect_entry_points(modules)
        relations = resolve_imports(modules)
        graph = build_dependency_graph(modules, relations)

    with st.spinner("Building bounded evidence package for the AI model…"):
        evidence = build_evidence_package(
            meta.to_dict(),
            st.session_state.readme,
            modules,
            entry_points,
            relations,
            file_contents,
            max_chars=max_evidence_chars,
        )

    st.session_state.retrieval = result
    st.session_state.modules = modules
    st.session_state.entry_points = entry_points
    st.session_state.relations = relations
    st.session_state.graph = graph
    st.session_state.evidence = evidence
    st.session_state.file_contents = file_contents
    st.session_state.ai_answer = None
    st.session_state.reading_order = None


def generate_ai_explanation(base_url: str, model: str, mode: str) -> None:
    evidence = st.session_state.evidence
    meta: Optional[RepoMetadata] = st.session_state.meta
    if evidence is None or meta is None:
        st.warning("Run the codebase analysis first.")
        return

    client = OllamaClient(base_url=base_url, model=model)
    try:
        resolved = client.resolve_model()
    except OllamaConnectionError as exc:
        st.error(f"Ollama is not reachable: {exc}")
        return
    except ModelNotAvailableError as exc:
        available = ", ".join(exc.available) if exc.available else "none installed"
        st.error(
            f"{exc} Available models: {available}. "
            "Install with `ollama pull qwen2.5:3b`, or choose an available model."
        )
        return
    except OllamaError as exc:
        st.error(f"Ollama error: {exc}")
        return

    context = build_context_for_question(evidence)
    if mode == "reading_order":
        prompt = build_reading_order_prompt(meta.full_name, context)
    else:
        prompt = build_user_prompt(meta.full_name, context)

    with st.spinner(f"Generating explanation with {resolved} (local)…"):
        try:
            answer = client.generate(prompt, system=SYSTEM_PROMPT, model=resolved)
        except OllamaError as exc:
            st.error(f"The model could not produce a response: {exc}")
            return

    if mode == "reading_order":
        st.session_state.reading_order = answer
    else:
        st.session_state.ai_answer = answer


# --------------------------------------------------------------------------- #
# Rendering helpers
# --------------------------------------------------------------------------- #
def render_metadata(meta: RepoMetadata) -> None:
    st.subheader(f"{meta.full_name}")
    if meta.description:
        st.write(meta.description)
    cols = st.columns(5)
    cols[0].metric("Stars", f"{meta.stars:,}")
    cols[1].metric("Forks", f"{meta.forks:,}")
    cols[2].metric("Open issues", f"{meta.open_issues:,}")
    cols[3].metric("Language", meta.language or "—")
    cols[4].metric("License", meta.license_name or "—")
    st.caption(
        f"Default branch: `{meta.default_branch}` · Revision: `{meta.commit_sha[:10] or 'n/a'}`"
        + (f" · [Open on GitHub]({meta.html_url})" if meta.html_url else "")
    )


def render_phase1(meta: RepoMetadata, tree: Optional[List[Dict[str, Any]]], readme: str) -> None:
    st.markdown("### Repository explorer (Phase 1)")
    render_metadata(meta)

    with st.expander("README", expanded=False):
        if readme:
            st.markdown(readme)
        else:
            st.info("No README could be retrieved.")

    if tree is not None:
        st.caption(f"File tree: {len(tree)} blobs")
        if st.session_state.get("tree_truncated"):
            st.warning("GitHub truncated the file tree; results may be incomplete.")
        with st.expander("File tree", expanded=False):
            for entry in tree[:1000]:
                st.text(entry["path"])
            if len(tree) > 1000:
                st.caption(f"…and {len(tree) - 1000} more.")


def render_warnings(retrieval: FileRetrievalResult) -> None:
    skipped = retrieval.skipped or []
    if not skipped and not retrieval.tree_truncated and not retrieval.file_limit_reached:
        return
    with st.expander("Retrieval warnings", expanded=True):
        if retrieval.tree_truncated:
            st.warning(
                "GitHub reported a truncated file tree. Some files may be missing "
                "from this analysis."
            )
        if retrieval.file_limit_reached:
            st.warning(
                f"The file limit was reached; some Python files were not analyzed. "
                f"Increase the limit in the sidebar to include more."
            )
        if skipped:
            by_reason: Dict[str, List[str]] = {}
            for entry in skipped:
                by_reason.setdefault(entry.get("reason", "unknown"), []).append(
                    entry.get("path", "?")
                )
            for reason, paths in sorted(by_reason.items()):
                st.markdown(f"**{reason}** ({len(paths)} file(s))")
                st.text("\n".join(paths[:50]) + ("\n…" if len(paths) > 50 else ""))


def render_source_tab(modules: Dict[str, ModuleInfo]) -> None:
    st.markdown("#### Source preview")
    st.caption("Line numbers match the file on GitHub. No code is executed.")
    paths = sorted(modules.keys())
    selected = st.selectbox("Choose a file", paths)
    module = modules[selected]

    if not module.parseable:
        st.error(f"This file could not be parsed: {module.syntax_error}")

    content = (st.session_state.get("file_contents") or {}).get(selected, "")
    lines = content.splitlines()
    numbered = "\n".join(f"{i:>4}: {line}" for i, line in enumerate(lines, start=1))
    st.code(numbered or "(empty file)", language="python")


def render_symbols_tab(modules: Dict[str, ModuleInfo]) -> None:
    st.markdown("#### Discovered classes and functions")
    rows: List[Dict[str, Any]] = []
    for path in sorted(modules):
        module = modules[path]
        for cls in module.classes:
            rows.append(
                {
                    "file": path,
                    "kind": "class",
                    "name": cls.name,
                    "lines": cls.line_range,
                    "docstring": (cls.docstring or "").splitlines()[0][:80]
                    if cls.docstring
                    else "",
                }
            )
            for method in cls.methods:
                rows.append(
                    {
                        "file": path,
                        "kind": "method",
                        "name": f"{cls.name}.{method.name}",
                        "lines": method.line_range,
                        "docstring": (method.docstring or "").splitlines()[0][:80]
                        if method.docstring
                        else "",
                    }
                )
        for func in module.functions:
            label = func.qualified_name
            if func.is_nested:
                label += " (nested)"
            rows.append(
                {
                    "file": path,
                    "kind": "function",
                    "name": label,
                    "lines": func.line_range,
                    "docstring": (func.docstring or "").splitlines()[0][:80]
                    if func.docstring
                    else "",
                }
            )
    if rows:
        st.dataframe(rows, use_container_width=True, hide_index=True)
    else:
        st.info("No classes or functions were found.")


def render_entry_points_tab(entry_points: List[Any]) -> None:
    st.markdown("#### Likely entry points")
    st.caption(
        "‘Evidence’ means a `__main__` guard or `__main__.py`. "
        "‘Heuristic’ means a filename/framework convention — not proof."
    )
    if not entry_points:
        st.info("No likely entry points were detected.")
        return
    for ep in entry_points:
        badge = "EVIDENCE" if ep.confidence == "evidence" else "HEURISTIC"
        with st.expander(f"[{badge}] {ep.path}", expanded=True):
            for reason in ep.reasons:
                st.markdown(f"- {reason}")


def render_dependencies_tab(relations: List[Any], graph: Optional[Dict[str, Any]]) -> None:
    st.markdown("#### Module import relationships")
    counts = summarize_resolution(relations)
    cols = st.columns(3)
    cols[0].metric("Exact", counts.get("exact", 0))
    cols[1].metric("Partial", counts.get("partial", 0))
    cols[2].metric("Unresolved", counts.get("unresolved", 0))
    st.caption(
        "An import means a module is referenced, not that a function is called "
        "at runtime. Unresolved imports usually point to the standard library or "
        "third-party packages."
    )
    rows = [
        {
            "source": r.source_path,
            "line": r.lineno,
            "imported": r.imported_module,
            "target": r.target_path or "—",
            "resolution": r.resolution,
            "relative": r.is_relative,
        }
        for r in relations
    ]
    if rows:
        st.dataframe(rows, use_container_width=True, hide_index=True)
    else:
        st.info("No imports were found.")

    if graph is not None:
        with st.expander("Graph data (JSON)"):
            st.json(graph)


def render_ai_tab() -> None:
    st.markdown("#### Evidence-grounded architecture explanation")
    if st.session_state.evidence is None:
        st.info("Run the codebase analysis first.")
        return

    evidence = st.session_state.evidence
    st.caption(
        f"Evidence budget: {evidence['budget']['max_chars']:,} chars · "
        f"{len(evidence.get('snippets', []))} snippet(s) · "
        f"{len(evidence.get('files', []))} file(s)"
    )
    with st.expander("Static-analysis facts sent to the model (no AI here)"):
        st.text(format_static_facts(evidence))

    if st.session_state.ai_answer:
        st.markdown("### AI explanation")
        st.markdown(st.session_state.ai_answer)
    else:
        st.info("Click ‘Generate AI explanation’ in the sidebar to produce one.")


# --------------------------------------------------------------------------- #
# Phase 3 — Find your first contribution
# --------------------------------------------------------------------------- #
def _split_labels(raw: str) -> List[str]:
    parts: List[str] = []
    for chunk in re.split(r"[,;\n]+", raw or ""):
        text = chunk.strip()
        if text and text not in parts:
            parts.append(text)
    return parts


def _beginner_workflow() -> List[str]:
    """Deterministic first-contribution steps (no AI involved)."""
    return [
        "**1. Fork & branch** — fork the repository, then create a branch named "
        "after the issue (e.g. `fix/123-typo`).",
        "**2. Reproduce or read** — read the issue end-to-end and check whether it "
        "links an existing file, test, or line number.",
        "**3. Locate the code** — use the *Source*, *Symbols*, and *Dependencies* "
        "tabs to find the file the issue refers to.",
        "**4. Smallest possible change** — fix only what the issue describes; avoid "
        "unrelated refactors in your first pull request.",
        "**5. Verify** — run the project's test suite and any documented lint/type "
        "checks before committing.",
        "**6. Open the pull request** — reference the issue (e.g. `Fixes #123`), "
        "describe the change, and quote the evidence that supports your fix.",
    ]


def _run_issue_search(
    meta: RepoMetadata,
    profile: ContributorProfile,
    label_filter: str,
    limit: int,
    keywords: str,
    weights: MatchWeights,
) -> None:
    token = st.session_state.get("github_token", "")
    client = IssueClient(token=token or None)
    labels = _split_labels(label_filter)

    with st.spinner("Searching open issues…"):
        try:
            result: IssueSearchResult = client.search_issues(
                meta.owner,
                meta.name,
                labels=labels,
                limit=int(limit),
                keywords=keywords.strip() or None,
            )
        except RateLimitError as exc:
            st.error(f"GitHub rate limit reached: {exc}")
            return
        except RepoNotFoundError as exc:
            st.error(f"Repository not found: {exc}")
            return
        except GitHubError as exc:
            st.error(f"GitHub error while searching issues: {exc}")
            return
        except Exception as exc:  # pragma: no cover - last-resort guard
            st.error(f"Unexpected error while searching issues: {exc}")
            return

    st.session_state.issue_search = result
    st.session_state.issue_recs = recommend(result.issues, profile, weights=weights)
    st.session_state.issue_profile = profile
    st.session_state.issue_expl = None


def _render_recommendation(rec: IssueRecommendation, profile: ContributorProfile) -> None:
    issue = rec.issue
    header = f"#{issue.number} — {issue.title}"
    url = f"[open on GitHub]({issue.html_url})" if issue.html_url else "no URL supplied"
    st.markdown(f"#### {header}")
    st.caption(
        f"match **{rec.match_score:.1f}**/100 · confidence **{rec.confidence:.2f}** · "
        f"{rec.difficulty.label} · {url}"
    )

    cols = st.columns(4)
    cols[0].metric("Match", f"{rec.match_score:.1f}")
    cols[1].metric("Confidence", f"{rec.confidence:.2f}")
    cols[2].metric("Effort", rec.difficulty.level.title())
    cols[3].metric("Comments", issue.comments)

    if issue.labels:
        st.caption("Labels: " + ", ".join(f"`{label}`" for label in issue.labels))

    with st.expander("Why this matched (evidence + component scores)", expanded=False):
        st.caption(
            "Match score and confidence are computed separately. Nothing below "
            "came from a model — each line cites a field of this real issue."
        )
        st.markdown("\n".join(f"- {line}" for line in rec.evidence) or "- (no evidence)")
        st.markdown(
            "**Components:** "
            + ", ".join(f"`{name}` {value:.2f}" for name, value in sorted(rec.components.items()))
        )

    if rec.cautions:
        with st.expander(f"Cautions ({len(rec.cautions)})", expanded=False):
            st.markdown("\n".join(f"- {c}" for c in rec.cautions))

    if issue.body.strip():
        with st.expander("Issue description (untrusted text)", expanded=False):
            st.text(issue.body)
    else:
        st.warning("This issue has no description — scope is unverified.")


def _render_issue_explanation(rec: IssueRecommendation, profile: ContributorProfile) -> None:
    st.markdown("#### Optional: local AI explanation")
    st.caption(
        "Runs only against your local Ollama model. The explanation **never changes "
        "the match score** above. If no model is available, a deterministic "
        "explanation is shown instead."
    )

    env_config = config_from_env()
    with st.expander("Local AI settings", expanded=False):
        base_url = st.text_input("Ollama URL", value=env_config.base_url, key="contrib_ollama_url")
        model = st.text_input("Model", value=env_config.model, key="contrib_ollama_model")

    if st.button("Explain this issue (local model)", key="explain_issue_btn"):
        client = OllamaClient(base_url=base_url, model=model)
        known_paths = list((st.session_state.get("file_contents") or {}).keys())
        with st.spinner("Asking the local model…"):
            explanation = explain_issue(
                rec.issue,
                profile,
                recommendation=rec,
                client=client,
                model=model,
                known_paths=known_paths,
            )
        st.session_state.issue_expl = explanation

    explanation = st.session_state.get("issue_expl")
    if explanation is None:
        st.info("Click the button above for a local explanation, or skip it — the score already stands.")
        return

    if explanation.error and not explanation.used_model:
        st.warning(explanation.error)
    elif explanation.error:
        st.info(explanation.error)

    if explanation.summary:
        st.markdown(f"**Summary:** {explanation.summary}")
    if explanation.why_it_matches:
        st.markdown(f"**Why it may match:** {explanation.why_it_matches}")
    if explanation.relevant_files:
        st.markdown("**Files likely involved (validated against this repository):**")
        st.markdown("\n".join(f"- `{p}`" for p in explanation.relevant_files))
    if explanation.dropped_files:
        st.caption("Model suggested paths that do not exist here: " + ", ".join(explanation.dropped_files))
    if explanation.suggested_first_steps:
        st.markdown("**Suggested first steps:**")
        st.markdown("\n".join(f"{i}. {s}" for i, s in enumerate(explanation.suggested_first_steps, start=1)))
    if explanation.skills_to_use or explanation.skills_to_learn:
        st.markdown(
            "**Skills:** uses " + (", ".join(explanation.skills_to_use) or "—")
            + " · may learn " + (", ".join(explanation.skills_to_learn) or "—")
        )
    if explanation.limitations:
        st.markdown("**Limitations:**")
        st.markdown("\n".join(f"- {l}" for l in explanation.limitations))

    st.caption(explanation.score_note)


def render_contribution_tab() -> None:
    st.markdown("### Find Your First Contribution (Phase 3)")
    meta: Optional[RepoMetadata] = st.session_state.meta
    if meta is None:
        st.info("Fetch a repository first — issue discovery needs a repository context.")
        return

    st.caption(
        "Read-only GitHub issue search + local, deterministic ranking. No repository "
        "code is executed, nothing is posted, and no secrets are logged."
    )

    # --- 1. Contributor profile ------------------------------------------- #
    with st.expander("1 · Your profile", expanded=True):
        col_a, col_b = st.columns(2)
        experience = col_a.selectbox(
            "Experience", ["beginner", "intermediate", "advanced"], index=0
        )
        availability = col_b.select_slider(
            "Time available", options=["few_hours", "weekend", "ongoing"], value="few_hours"
        )
        skills = st.text_input(
            "Skills (comma separated)", value="python, docs, testing",
            help="Used for skill overlap. Leave blank for a neutral skill score.",
        )
        interests = st.text_input(
            "Interests (comma separated)", value="",
            help="e.g. docs, api, performance, cli",
        )
        profile = ContributorProfile.from_text(
            skills_text=skills,
            interests_text=interests,
            experience=experience,
            availability=availability,
        )

    # --- 2. Issue discovery ------------------------------------------------ #
    with st.expander("2 · Issue discovery & scoring", expanded=True):
        label_filter = st.text_input(
            "Labels (comma separated)", value="good first issue",
            help="Leave blank to search all open issues. Add 'good first issue' for beginner tasks.",
        )
        limit = st.number_input(
            "Max issues", min_value=1, max_value=100, value=DEFAULT_ISSUE_LIMIT
        )
        keywords = st.text_input("Extra keywords (optional)", value="")

        with st.expander("Scoring weights (advanced)"):
            st.caption("Weights are renormalised automatically; scores stay deterministic.")
            w_skill = st.slider("Skill overlap", 0.0, 1.0, 0.30, 0.05)
            w_label = st.slider("Label fit", 0.0, 1.0, 0.20, 0.05)
            w_diff = st.slider("Difficulty fit", 0.0, 1.0, 0.20, 0.05)
            w_scope = st.slider("Scope / boundedness", 0.0, 1.0, 0.15, 0.05)
            weights = MatchWeights(
                skill=w_skill, label_fit=w_label, difficulty_fit=w_diff, scope=w_scope
            )

        if st.button("Fetch & rank issues", use_container_width=True):
            _run_issue_search(meta, profile, label_filter, int(limit), keywords, weights)

    # --- 3. Results -------------------------------------------------------- #
    recs: Optional[List[IssueRecommendation]] = st.session_state.get("issue_recs")
    if recs is None:
        st.info("Set your profile above and click **Fetch & rank issues**.")
        st.markdown("#### Beginner workflow")
        st.markdown("\n".join(_beginner_workflow()))
        return

    search: Optional[IssueSearchResult] = st.session_state.get("issue_search")
    if not recs:
        # A completed search that returned no rankable open issues is NOT the
        # same as "not searched yet" — say so clearly instead of showing the
        # initial prompt again.
        if search is not None:
            st.warning(
                "No open issues matched your filters. "
                f"Query: `{search.query}` · GitHub reported "
                f"{search.total_count} total match(es). "
                "Try clearing the label filter, removing keywords, or searching "
                "a different repository."
            )
            if search.skipped_pull_requests:
                st.caption(
                    f"Skipped {search.skipped_pull_requests} pull request(s)."
                )
        else:
            st.warning("No open issues were returned for this repository.")
        st.markdown("#### Beginner workflow")
        st.markdown("\n".join(_beginner_workflow()))
        return

    if search is not None:
        st.caption(
            f"Query: `{search.query}` · {len(search.issues)} issue(s) · "
            f"{search.total_count} total match(es) · "
            f"{'served from cache' if search.cached else 'live search'}"
        )
        if search.skipped_pull_requests:
            st.caption(f"Skipped {search.skipped_pull_requests} pull request(s).")

    st.markdown("#### Ranked candidates")
    rows = [
        {
            "rank": r.rank,
            "#": r.issue.number,
            "title": r.issue.title[:70],
            "match": r.match_score,
            "confidence": r.confidence,
            "effort": r.difficulty.level,
            "labels": ", ".join(r.issue.labels[:3]),
        }
        for r in recs
    ]
    st.dataframe(rows, use_container_width=True, hide_index=True)

    top = recs[0]
    st.success(
        f"Top candidate: **#{top.issue.number}** — {top.issue.title} "
        f"({top.match_score:.1f}/100, confidence {top.confidence:.2f}, {top.difficulty.label})."
    )

    options = [f"#{r.rank} · #{r.issue.number} {r.issue.title[:60]}" for r in recs]
    choice = st.selectbox("Inspect a candidate", options, key="issue_choice")
    index = options.index(choice)
    st.session_state.issue_expl = None if st.session_state.get("_last_issue_idx") != index else st.session_state.issue_expl
    st.session_state["_last_issue_idx"] = index

    _render_recommendation(recs[index], profile)
    _render_issue_explanation(recs[index], profile)

    with st.expander("Beginner workflow (how to act on this match)", expanded=False):
        st.markdown("\n".join(_beginner_workflow()))
        st.caption(
            "Static analysis and issue ranking are heuristics: verify the issue is "
            "still open and unassigned before you start."
        )


# --------------------------------------------------------------------------- #
# Phase 4: contribution guidance & learning paths
# --------------------------------------------------------------------------- #
def _guide_issue_options(recs: List[IssueRecommendation]) -> List[str]:
    return [f"#{r.issue.number} — {r.issue.title[:70]}" for r in recs]


def _file_snippet(path: str, max_lines: int = 60) -> str:
    content = (st.session_state.get("file_contents") or {}).get(path, "")
    if not content:
        return ""
    lines = content.splitlines()
    shown = lines[:max_lines]
    body = "\n".join(f"{i:>4}: {line}" for i, line in enumerate(shown, start=1))
    if len(lines) > max_lines:
        body += f"\n… ({len(lines) - max_lines} more lines)"
    return body


def _build_guide_bundle(
    meta: RepoMetadata, rec: IssueRecommendation, profile: ContributorProfile
) -> Dict[str, Any]:
    modules = st.session_state.get("modules") or {}
    readme = st.session_state.get("readme", "")
    link = link_issue_to_files(rec.issue, modules, repo_meta=meta)
    guide = build_contribution_guide(
        rec.issue, link, profile, meta, readme, modules, difficulty=rec.difficulty
    )
    path = build_learning_path(rec.issue, profile, link, meta, modules)
    checklist = build_checklist(rec.issue, link, profile, meta, readme, modules)
    return {
        "key": checklist_key(meta.full_name, rec.issue.number),
        "issue_number": rec.issue.number,
        "issue": rec.issue,
        "link": link,
        "guide": guide,
        "path": path,
        "checklist": checklist,
    }


def _phase4_model_inputs() -> tuple:
    env_config = config_from_env()
    with st.expander("Local AI settings (optional)", expanded=False):
        base_url = st.text_input("Ollama URL", value=env_config.base_url, key="p4_ollama_url")
        model = st.text_input("Model", value=env_config.model, key="p4_ollama_model")
    return base_url, model


def _render_file_explanation(bundle: Dict[str, Any]) -> None:
    stored = st.session_state.get("file_expl")
    if not stored or stored.get("key") != bundle["key"]:
        return
    exp = stored["data"]
    st.markdown(f"**Local AI explanation of `{exp.file_path}`**")
    if exp.error and not exp.used_model:
        st.warning(exp.error)
    elif exp.error:
        st.info(exp.error)
    if exp.summary:
        st.markdown(f"**Summary:** {exp.summary}")
    if exp.key_symbols:
        st.markdown("**Key symbols:**")
        st.markdown("\n".join(f"- {s}" for s in exp.key_symbols))
    if exp.how_it_might_relate:
        st.markdown(f"**How it may relate to the issue:** {exp.how_it_might_relate}")
    if exp.beginner_notes:
        st.markdown("**Beginner notes:**")
        st.markdown("\n".join(f"- {n}" for n in exp.beginner_notes))
    if exp.dropped_symbols:
        st.caption(
            "Symbols named by the model that do not exist in this file were "
            "removed: " + ", ".join(exp.dropped_symbols)
        )
    if exp.limitations:
        st.markdown("**Limitations:**")
        st.markdown("\n".join(f"- {l}" for l in exp.limitations))


def _render_plan(
    bundle: Dict[str, Any], profile: ContributorProfile, base_url: str, model: str
) -> None:
    st.markdown("**Optional: AI-drafted plan**")
    st.caption(
        "A suggestion only — never a guarantee, and it never replaces the "
        "deterministic checklist in the Progress tab."
    )
    if st.button("Draft a plan (local model)", key=f"p4_plan_{bundle['key']}"):
        client = OllamaClient(base_url=base_url, model=model)
        with st.spinner("Asking the local model to draft a plan…"):
            plan = draft_contribution_plan(
                bundle["guide"], profile, client=client, model=model
            )
        st.session_state.guide_plan = {"key": bundle["key"], "data": plan}

    stored = st.session_state.get("guide_plan")
    if not stored or stored.get("key") != bundle["key"]:
        return
    plan = stored["data"]
    if plan.error and not plan.used_model:
        st.warning(plan.error)
    elif plan.error:
        st.info(plan.error)
    if plan.plan_steps:
        st.markdown("**Suggested steps (verify each):**")
        st.markdown("\n".join(f"{i}. {s}" for i, s in enumerate(plan.plan_steps, start=1)))
    if plan.questions_to_ask:
        st.markdown("**Questions to ask:**")
        st.markdown("\n".join(f"- {q}" for q in plan.questions_to_ask))
    if plan.things_to_watch:
        st.markdown("**Things to watch:**")
        st.markdown("\n".join(f"- {w}" for w in plan.things_to_watch))
    if plan.limitations:
        st.markdown("**Limitations:**")
        st.markdown("\n".join(f"- {l}" for l in plan.limitations))


def _render_guide_section(
    bundle: Dict[str, Any], profile: ContributorProfile, meta: RepoMetadata
) -> None:
    guide = bundle["guide"]
    link = bundle["link"]
    issue = bundle["issue"]

    st.markdown(f"#### Guide for #{issue.number} — {issue.title}")
    if issue.html_url:
        st.caption(
            f"[Open issue on GitHub]({issue.html_url}) · state: {issue.state} · "
            f"kind: {guide.issue_kind}"
        )
    metrics = st.columns(3)
    metrics[0].metric("Candidate files", len(link.linked_files))
    metrics[1].metric("Explicit references", link.confirmed_count)
    metrics[2].metric(
        "Difficulty", guide.difficulty.label if guide.difficulty else "unknown"
    )

    st.markdown("**Problem**")
    st.write(guide.problem)
    st.markdown("**Objective**")
    st.write(guide.objective)

    st.markdown("**Acceptance criteria**")
    if guide.acceptance_criteria:
        for criterion in guide.acceptance_criteria:
            st.markdown(f"- {criterion.text}  \n  source: {criterion.source}")
    else:
        st.info("No explicit acceptance criteria were stated in the issue.")
    st.caption(guide.acceptance_note)

    st.markdown("**Candidate files (issue → code)**")
    if not link.linked_files:
        st.warning(
            "No candidate files were found. Treat the location as unknown and "
            "ask a maintainer which module handles this."
        )
    for linked in link.linked_files:
        badge = "confirmed reference" if linked.is_confirmed else "keyword match — verify"
        with st.expander(
            f"`{linked.path}` — {badge} · score {linked.score:.1f}",
            expanded=linked.is_confirmed,
        ):
            st.caption(f"module `{linked.module_name}` · evidence: {linked.confidence}")
            st.markdown("\n".join(f"- {reason}" for reason in linked.reasons))
            if linked.symbols:
                st.markdown(
                    "Symbols: " + ", ".join(f"`{s}`" for s in linked.symbols)
                )
            if linked.github_url:
                st.markdown(f"[View this file on GitHub]({linked.github_url})")
            if linked.needs_investigation:
                st.caption(
                    "This is not an explicit reference — confirm it is involved "
                    "before assuming so."
                )

    module_map: Dict[str, ModuleInfo] = st.session_state.get("modules") or {}
    inspectable = [f.path for f in link.linked_files if f.path in module_map]
    base_url, model = _phase4_model_inputs()
    if inspectable:
        st.markdown("**Inspect a candidate file**")
        choice = st.selectbox("File", inspectable, key=f"p4_file_{bundle['key']}")
        module = module_map.get(choice)
        if module is not None:
            st.caption(
                f"{len(module.classes)} class(es) · {len(module.functions)} "
                f"top-level function(s) · {module.line_count} lines"
            )
            if module.docstring:
                st.markdown(f"_Docstring:_ {module.docstring[:400]}")
            snippet = _file_snippet(choice)
            if snippet:
                st.code(snippet, language="python")
        if st.button("Explain this file (local model)", key=f"p4_explain_{bundle['key']}"):
            client = OllamaClient(base_url=base_url, model=model)
            with st.spinner("Asking the local model about this file…"):
                result = explain_file(
                    choice,
                    module=module,
                    snippet=_file_snippet(choice),
                    client=client,
                    model=model,
                    repo_full_name=meta.full_name,
                )
            st.session_state.file_expl = {
                "key": bundle["key"],
                "path": choice,
                "data": result,
            }
        _render_file_explanation(bundle)

    st.markdown("**Suggested investigation order**")
    st.markdown(
        "\n".join(
            f"{i}. {step}"
            for i, step in enumerate(guide.investigation_sequence, start=1)
        )
    )

    if guide.questions:
        st.markdown("**Questions to ask the maintainer**")
        st.markdown("\n".join(f"- {q}" for q in guide.questions))

    st.markdown("**Facts and hypotheses**")
    st.markdown("_Facts (from data we have):_")
    st.markdown("\n".join(f"- {f}" for f in guide.facts) or "- (none)")
    st.markdown("_Hypotheses (verify these):_")
    st.markdown("\n".join(f"- {h}" for h in guide.hypotheses) or "- (none)")

    if guide.risks:
        st.markdown("**Risks**")
        for risk in guide.risks:
            tag = "HYPOTHESIS" if risk.is_hypothesis else "VERIFIED"
            st.markdown(f"- [{tag}] {risk.text}")

    if guide.unknowns:
        st.markdown("**Unknowns**")
        st.markdown("\n".join(f"- {u}" for u in guide.unknowns))

    _render_plan(bundle, profile, base_url, model)


def _render_learning_section(bundle: Dict[str, Any]) -> None:
    path = bundle["path"]
    st.markdown(f"#### Learning path for #{path.issue_number}")
    st.caption(
        "Personalized using only what you told us about yourself plus static "
        "repository facts. Topics marked 'reinforce' overlap with your skills; "
        "'to learn' topics are suggestions you can skip."
    )
    for topic in path.topics:
        badge = "reinforce" if topic.status == "reinforce" else "to learn"
        with st.expander(f"{topic.order}. {topic.title} · [{badge}]", expanded=False):
            st.markdown(topic.why)
            if topic.where:
                st.markdown("**Where:** " + ", ".join(f"`{w}`" for w in topic.where))
            if topic.resources:
                st.markdown("**Resources:** " + " · ".join(topic.resources))
            if topic.exercises:
                st.markdown("**Exercises:**")
                st.markdown("\n".join(f"- {e}" for e in topic.exercises))
            if topic.readiness_checks:
                st.markdown("**Readiness checks:**")
                st.markdown("\n".join(f"- {c}" for c in topic.readiness_checks))
            if topic.matched_skills:
                st.caption("Overlaps your listed skills: " + ", ".join(topic.matched_skills))

    st.markdown("**Next steps**")
    st.markdown("\n".join(f"- {s}" for s in path.next_steps))
    if path.profile_notes:
        st.caption(" ".join(path.profile_notes))


def _render_progress_section(bundle: Dict[str, Any]) -> None:
    items = bundle["checklist"]
    key = bundle["key"]
    if "checklist_state" not in st.session_state or not isinstance(
        st.session_state.get("checklist_state"), dict
    ):
        st.session_state.checklist_state = {}
    state_all: Dict[str, Dict[str, bool]] = st.session_state.checklist_state
    state: Dict[str, bool] = state_all.setdefault(key, {})

    done, total, frac = progress(items, state)
    st.progress(frac)
    st.caption(f"{done} of {total} steps complete · progress is stored per issue (`{key}`)")

    for category, cat_items in group_by_category(items).items():
        st.markdown(f"**{CATEGORY_LABELS.get(category, category.title())}**")
        for item in cat_items:
            checked = st.checkbox(
                item.text,
                value=bool(state.get(item.id)),
                key=f"chk::{key}::{item.id}",
            )
            state[item.id] = checked

    if st.button("Reset progress for this issue", key=f"p4_reset_{key}"):
        state_all[key] = {}
        for item in items:
            st.session_state.pop(f"chk::{key}::{item.id}", None)
        st.rerun()


def render_guide_tab() -> None:
    st.markdown("### Contribution Guide (Phase 4)")
    meta: Optional[RepoMetadata] = st.session_state.meta
    if meta is None:
        st.info("Fetch a repository first.")
        return

    recs: Optional[List[IssueRecommendation]] = st.session_state.get("issue_recs")
    if not recs:
        st.info(
            "Discover issues in the **Find Your First Contribution** tab first, "
            "then return here to build a step-by-step guide."
        )
        return

    profile = st.session_state.get("issue_profile") or ContributorProfile()
    options = _guide_issue_options(recs)
    choice = st.selectbox("Choose an issue to guide you through", options, key="p4_issue_choice")
    rec = recs[options.index(choice)]

    bundle = st.session_state.get("guide_bundle")
    if bundle is not None and bundle.get("issue_number") != rec.issue.number:
        st.session_state.guide_bundle = None
        st.session_state.guide_plan = None
        st.session_state.file_expl = None
        bundle = None

    if not st.session_state.get("modules"):
        st.warning(
            "Run the codebase analysis in the sidebar so issues can be linked to "
            "real files. You can still build a guide, but candidate files will be empty."
        )

    if st.button("Build contribution guide", use_container_width=True, key="p4_build"):
        bundle = _build_guide_bundle(meta, rec, profile)
        st.session_state.guide_bundle = bundle
        st.session_state.guide_plan = None
        st.session_state.file_expl = None

    bundle = st.session_state.get("guide_bundle")
    if bundle is None or bundle.get("issue_number") != rec.issue.number:
        st.info(
            "Click **Build contribution guide** to generate the guide, personalized "
            "learning path, and contribution checklist for this issue."
        )
        return

    sub = st.tabs(["Guide", "Learning path", "Progress"])
    with sub[0]:
        _render_guide_section(bundle, profile, meta)
    with sub[1]:
        _render_learning_section(bundle)
    with sub[2]:
        _render_progress_section(bundle)


# --------------------------------------------------------------------------- #
# Sidebar
# --------------------------------------------------------------------------- #
def render_sidebar() -> Dict[str, Any]:
    st.sidebar.title("CodePath AI")
    st.sidebar.caption(APP_TAGLINE)

    st.sidebar.subheader("Repository")
    url = st.sidebar.text_input(
        "Public GitHub repository URL",
        value="",
        placeholder="https://github.com/owner/repo",
        key="repo_url_input",
        help="Paste any public repository URL, e.g. "
        "https://github.com/psf/requests or https://github.com/noob-mukesh/mukeshrobot",
    )
    token = st.sidebar.text_input(
        "GitHub token (optional, not required)",
        type="password",
        value=_load_token_default(),
        key="github_token_input",
        help="Completely optional. Without a token the public API allows "
        "60 requests/hour; a token raises that to 5,000/hour. The value is "
        "never logged or displayed.",
    )
    st.session_state.github_token = token

    with st.sidebar.expander("Analysis limits", expanded=False):
        max_files = st.number_input(
            "Max files", min_value=1, max_value=1000, value=DEFAULT_MAX_FILES
        )
        max_file_kb = st.number_input(
            "Max file size (KB)",
            min_value=1,
            max_value=2048,
            value=DEFAULT_MAX_FILE_BYTES // 1024,
        )
        max_evidence_chars = st.number_input(
            "AI context budget (chars)",
            min_value=2000,
            max_value=48000,
            value=12000,
            step=1000,
        )

    st.sidebar.subheader("Local AI (Ollama)")
    env_config = config_from_env()
    base_url = st.sidebar.text_input("Ollama URL", value=env_config.base_url)
    model = st.sidebar.text_input("Model", value=env_config.model)

    fetch_clicked = st.sidebar.button(
        "Fetch repository", use_container_width=True, key="fetch_repo_btn",
        type="primary",
    )
    analyze_clicked = st.sidebar.button(
        "Analyze Python codebase", use_container_width=True, key="analyze_btn"
    )
    ai_clicked = st.sidebar.button(
        "Generate AI explanation", use_container_width=True, key="ai_btn"
    )
    order_clicked = st.sidebar.button(
        "Recommend reading order", use_container_width=True, key="order_btn"
    )
    health_clicked = st.sidebar.button(
        "Check Ollama", use_container_width=True, key="health_btn"
    )

    st.sidebar.divider()
    st.sidebar.caption(
        "Static analysis is necessarily incomplete: dynamic imports, runtime "
        "configuration, and framework magic may not be detected."
    )

    return {
        "url": url,
        "token": token,
        "max_files": int(max_files),
        "max_file_kb": int(max_file_kb),
        "max_evidence_chars": int(max_evidence_chars),
        "base_url": base_url,
        "model": model,
        "fetch_clicked": fetch_clicked,
        "analyze_clicked": analyze_clicked,
        "ai_clicked": ai_clicked,
        "order_clicked": order_clicked,
        "health_clicked": health_clicked,
    }


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main() -> None:
    page_icon = str(FAVICON_PATH) if FAVICON_PATH.exists() else "🧭"
    st.set_page_config(page_title="CodePath AI", page_icon=page_icon, layout="wide")
    _init_state()

    if LOGO_PATH.exists():
        st.image(str(LOGO_PATH), width=340)
        st.caption(APP_TAGLINE)
    else:
        st.title("CodePath AI")
        st.caption(APP_TAGLINE)

    controls = render_sidebar()

    # Handle sidebar actions.
    if controls["health_clicked"]:
        client = OllamaClient(base_url=controls["base_url"], model=controls["model"])
        health = client.health()
        if health["available"]:
            st.sidebar.success(
                f"Ollama is running. {len(health['models'])} model(s) installed."
            )
            if not health.get("model_installed"):
                st.sidebar.warning(
                    f"Configured model '{controls['model']}' is not installed."
                )
        else:
            st.sidebar.error(f"Ollama unavailable: {health['error']}")

    if controls["fetch_clicked"]:
        try:
            fetch_repository(controls["url"], controls["token"])
            st.success("Repository fetched.")
        except InvalidRepoUrlError as exc:
            st.error(f"Invalid repository URL: {exc}")
        except RepoNotFoundError as exc:
            st.error(f"Repository not found: {exc}")
        except RateLimitError as exc:
            st.error(f"GitHub rate limit reached: {exc}")
        except GitHubError as exc:
            st.error(f"GitHub error: {exc}")
        except Exception as exc:  # pragma: no cover - last-resort guard
            st.error(f"Unexpected error while fetching: {exc}")

    if controls["analyze_clicked"]:
        try:
            analyze_repository(
                controls["max_files"],
                controls["max_file_kb"],
                controls["max_evidence_chars"],
            )
        except GitHubError as exc:
            st.error(f"GitHub error during analysis: {exc}")
        except Exception as exc:  # pragma: no cover
            st.error(f"Unexpected error during analysis: {exc}")

    if controls["ai_clicked"]:
        generate_ai_explanation(controls["base_url"], controls["model"], "explain")

    if controls["order_clicked"]:
        generate_ai_explanation(
            controls["base_url"], controls["model"], "reading_order"
        )

    # Body.
    meta: Optional[RepoMetadata] = st.session_state.meta
    if meta is None:
        st.info(
            "Enter a public GitHub repository URL in the sidebar and click "
            "**Fetch repository** to begin. Everything runs locally; no repository "
            "code is ever executed or modified."
        )
        return

    retrieval: Optional[FileRetrievalResult] = st.session_state.retrieval
    modules: Optional[Dict[str, ModuleInfo]] = st.session_state.modules

    tabs = st.tabs(
        [
            "Overview",
            "Source",
            "Symbols",
            "Entry points",
            "Dependencies",
            "AI explanation",
            "Find Your First Contribution",
            "Contribution Guide",
        ]
    )

    with tabs[0]:
        render_phase1(meta, st.session_state.tree, st.session_state.readme)
        if retrieval is not None:
            st.markdown("### Codebase analysis (Phase 2)")
            ok = len(retrieval.ok_files)
            cols = st.columns(4)
            cols[0].metric("Python files retrieved", ok)
            cols[1].metric("Files skipped", len(retrieval.skipped))
            if modules:
                cols[2].metric("Parseable modules", sum(1 for m in modules.values() if m.parseable))
                cols[3].metric(
                    "Unparseable", sum(1 for m in modules.values() if not m.parseable)
                )
            render_warnings(retrieval)
            if st.session_state.entry_points:
                top = st.session_state.entry_points[0]
                st.info(f"Most likely entry point: `{top.path}` ({top.confidence}).")

    with tabs[1]:
        if modules:
            render_source_tab(modules)
        else:
            st.info("Run the codebase analysis to preview source files.")

    with tabs[2]:
        if modules:
            render_symbols_tab(modules)
        else:
            st.info("Run the codebase analysis to list classes and functions.")

    with tabs[3]:
        if st.session_state.entry_points is not None:
            render_entry_points_tab(st.session_state.entry_points)
        else:
            st.info("Run the codebase analysis to detect entry points.")

    with tabs[4]:
        if st.session_state.relations is not None:
            render_dependencies_tab(st.session_state.relations, st.session_state.graph)
        else:
            st.info("Run the codebase analysis to map module relationships.")

    with tabs[5]:
        render_ai_tab()
        if st.session_state.reading_order:
            st.markdown("### Recommended reading order")
            st.markdown(st.session_state.reading_order)

    with tabs[6]:
        render_contribution_tab()

    with tabs[7]:
        render_guide_tab()


if __name__ == "__main__":
    main()
