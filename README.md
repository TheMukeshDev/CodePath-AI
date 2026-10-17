<p align="center">
  <img src="public/brand/codepath-logo.png" alt="CodePath AI Logo" width="460">
</p>
<p align="center">
  <strong>Khojo Code. Samjho System. Contribute with Confidence.</strong><br>
  <em>Team CodeYatra</em> · <a href="BRAND_GUIDE.md">Brand System Guide</a> · <a href="public/brand/showcase.html">Interactive Showcase</a>
</p>

# CodePath AI

CodePath AI helps beginners discover, understand, and contribute to open-source
repositories with the assistance of open-weight AI. It retrieves a public
repository through the GitHub REST API, analyzes its Python source with the
built-in `ast` module, and uses a **local** Ollama model to explain the
codebase using **evidence grounded in the actual source code**.

## Status

* **Phase 1 — Repository explorer:** ✅ metadata, README, and file-tree retrieval.
* **Phase 2 — Codebase intelligence:** ✅ safe source retrieval, Python static
  analysis, entry-point detection, module relationship mapping, bounded
  evidence packages, evidence-grounded AI explanations, analysis caching.
* **Phase 3 — Contribution recommender:** ✅ read-only issue discovery,
  deterministic skill/difficulty matching, evidence and confidence reporting,
  optional local-AI issue explanations, and a beginner contribution workflow.
* **Phase 4 — Contribution guidance & learning paths:** ✅ issue-to-code
  linking, an issue-specific contribution guide, personalized learning paths,
  an interactive contribution checklist, and optional local-AI file
  explanations and plan drafts.

> **Honest limitation:** static analysis is necessarily incomplete. Dynamic
> imports (`importlib`, `__import__`), runtime configuration, plugin
> registries, and framework magic cannot be fully detected. CodePath AI
> distinguishes *static facts* from *AI interpretation* and explicitly reports
> what it cannot determine.

## Features

### Phase 1 — Repository explorer
* Validate and parse public GitHub URLs.
* Fetch repository metadata, default branch, and commit SHA.
* Fetch README (decoded safely).
* Fetch the recursive file tree (with truncation awareness).

### Phase 2 — Codebase intelligence
* **Safe source retrieval** — selected Python files only, via the GitHub API,
  using the repository's real default branch/revision. Per-file size limit
  (default 100 KB), max analyzed files (default 100), binary/oversized/excluded
  files skipped and reported. No cloning, no code execution.
* **Static analysis (`analyzer/file_scanner.py`)** — extracts module names,
  top-level and nested functions, methods, parameters, line ranges,
  decorators, docstrings, and absolute/relative imports using `ast`. Syntax
  errors are handled gracefully.
* **Entry-point detection (`analyzer/code_map.py`)** — finds `__main__` guards
  and `__main__.py` as **evidence**, and common filenames/framework imports as
  **heuristics**, with an explanation for every candidate.
* **Module relationship mapping** — resolves imports to repository files where
  possible. Each relation records source module, imported module, resolved
  target, line number, statement, and resolution status
  (`exact` / `partial` / `unresolved`).
* **Evidence-grounded AI (`ai/context_builder.py`, `ai/prompts.py`)** — builds a
  compact, character-bounded evidence package (facts + line-numbered snippets)
  and asks the local model to explain architecture, entry points, important
  files, module relationships, reading order, and uncertainties.
* **Beginner-friendly explorer (`app.py`)** — Streamlit UI with source preview
  (line numbers), symbol tables, entry-point report, dependency table, JSON
  graph data, AI explanation, and reading order.
* **Analysis caching (`analyzer/cache.py`)** — keys include repository identity
  **and** revision SHA, so results never leak across repositories or revisions.

### Phase 3 — Find your first contribution
* **Read-only issue discovery (`analyzer/issue_client.py`)** — searches open
  issues via the GitHub Search API (`is:issue`, PRs excluded), with label and
  keyword filters. Everything is bounded (page size, issue count, title/body
  length) and cached briefly with a TTL cache. No writes, ever.
* **Deterministic recommender (`analyzer/recommender.py`)** — scores each real
  issue against a contributor profile on six components: skill overlap, label
  fit, difficulty fit, scope/boundedness, interest match, and recency. Weights
  are configurable and renormalised automatically. Results are reproducible
  (score desc, then issue number).
* **Difficulty estimation** — a heuristic effort signal derived only from the
  issue's own labels and text, with each signal listed explicitly.
* **Score ≠ confidence** — `match_score` (0–100) measures fit; `confidence`
  (0–1) measures how much evidence exists. An empty issue can still match on
  labels, but it is clearly flagged as low-confidence.
* **Optional local-AI explanation (`ai/issue_explainer.py`)** — asks the local
  Ollama model for a structured JSON explanation. The model **never** changes
  the deterministic score, invented file paths are filtered against the real
  repository, and a deterministic fallback is used whenever the model is
  unavailable or returns unusable output.
* **Beginner workflow (`app.py`)** — a new **Find Your First Contribution** tab
  with profile inputs, issue discovery, a ranked table, per-issue evidence, and
  step-by-step guidance from fork to pull request.

### Phase 4 — Contribution guide & learning path
* **Issue-to-code linking (`analyzer/issue_linker.py`)** — connects a real issue
  to the repository files it may concern. A file is marked **confirmed** when
  the issue explicitly names a real path, module, class, or function (whole-token
  matching), and **likely** when there is only keyword overlap. Every candidate
  carries the exact reason(s) it was surfaced, and GitHub links are built only
  from the repository's real metadata. It never claims a file *causes* an issue.
* **Issue-specific contribution guide (`analyzer/contribution_guide.py`)** —
  problem, objective, an ordered investigation sequence, questions to ask, and
  risks. **Acceptance criteria are never invented**; they appear only when the
  issue author wrote them, quoted verbatim as untrusted text. Facts and
  hypotheses are kept strictly separate.
* **Personalized learning path (`analyzer/learning_path.py`)** — an ordered set
  of topics (fundamentals → issue-type skill → the relevant file → testing →
  detected frameworks → Git/GitHub workflow). Topics are marked **reinforce**
  when your profile already lists a related skill, otherwise **to learn**; a
  missing skill is never treated as evidence you lack it. Resources link only to
  canonical documentation sites.
* **Interactive checklist (`analyzer/checklist.py`)** — deterministic,
  issue-specific steps across *understand → setup → implement → verify → share*,
  adapting to the issue type, the presence of tests, and contribution guidelines.
  Progress is scoped per issue and never leaks between issues.
* **Optional local AI (`ai/contribution_assistant.py`)** — explain a specific
  file or draft a plan with the local model. Invented symbols are dropped against
  the file's real symbols, plans are labelled as hypotheses, and a deterministic
  fallback is always available.
* **Guide UI (`app.py`)** — a new **Contribution Guide** tab with *Guide*,
  *Learning path*, and *Progress* sub-tabs.

## Architecture

```
CodePath-AI/
├── app.py                    # Streamlit UI (Phase 1 explorer + Phase 2 analysis
│                             #   + Phase 3 recommender + Phase 4 guide)
├── analyzer/
│   ├── github_client.py      # Safe GitHub REST retrieval (no execution)
│   ├── file_scanner.py       # AST extraction (facts only)
│   ├── code_map.py           # Entry points, import resolution, graph data
│   ├── issue_client.py       # Read-only issue discovery (Phase 3)
│   ├── recommender.py        # Deterministic skill/difficulty matching (Phase 3)
│   ├── issue_linker.py       # Issue → candidate files (Phase 4)
│   ├── contribution_guide.py # Issue-specific guide (Phase 4)
│   ├── learning_path.py      # Personalized learning path (Phase 4)
│   ├── checklist.py          # Contribution checklist (Phase 4)
│   └── cache.py              # Revision-scoped cache + TTL cache
├── ai/
│   ├── model_client.py       # Local Ollama client (typed, graceful failures)
│   ├── context_builder.py    # Bounded evidence package + formatting
│   ├── issue_explainer.py    # Optional JSON issue explanations (Phase 3)
│   ├── contribution_assistant.py # Optional file explain / plan draft (Phase 4)
│   ├── structured_output.py  # Shared defensive JSON parsing (Phase 4)
│   └── prompts.py            # Evidence-grounded, untrusted-data prompts
├── tests/                    # pytest suite (no network, no model required)
├── requirements.txt
└── README.md
```

Data flow:

```
GitHub API ──▶ github_client.fetch_python_files
          ──▶ file_scanner.analyze_files ──▶ code_map.detect_entry_points
                                          ──▶ code_map.resolve_imports
                                          ──▶ code_map.build_dependency_graph
                                          ──▶ context_builder.build_evidence_package
                                          ──▶ model_client (Ollama) ──▶ explanation

GitHub API ──▶ issue_client.search_issues
          ──▶ recommender.recommend (deterministic score + confidence + evidence)
          ──▶ issue_explainer.explain_issue (optional Ollama; never changes score)

issue + modules ──▶ issue_linker.link_issue_to_files ──▶ contribution_guide
                ──▶ learning_path.build_learning_path
                ──▶ checklist.build_checklist
                ──▶ contribution_assistant (optional Ollama; never changes facts)
```

## Evidence and citation strategy

* Nothing is sent to the model except a **bounded evidence package**.
* The package contains repository metadata, README excerpt, file/symbol
  summaries, import relations, and short **line-numbered** source snippets.
* The system prompt instructs the model to cite `path/to/file.py:line`, to
  separate static facts from interpretation, and to state when evidence is
  insufficient.
* Repository content is wrapped in an explicit **UNTRUSTED EVIDENCE** block and
  treated as data, never as instructions.
* A default character budget (~12,000 chars) keeps prompts small enough for an
  8 GB machine running a ≈3B-parameter model.

## Supported languages

**Python only (`*.py`, `*.pyi`)** in this phase. The architecture is layered
so additional languages can be added later, but no other language is currently
parsed. Non-Python files are ignored during retrieval and analysis.

## Requirements

* Python 3.10+ (developed and tested on Python 3.15).
* Windows, macOS, or Linux.
* [Ollama](https://ollama.com) for AI explanations (optional for all static
  analysis features).
* Internet access to reach `api.github.com` (retrieval only).

## Windows setup and run

From **Command Prompt** (`cmd.exe`):

```bat
cd C:\Users\ACER\Downloads\coding\CodePath-AI
python -m venv .venv
.venv\Scripts\activate.bat
pip install -r requirements.txt
```

Install and start the local model (optional, needed only for AI explanations):

```bat
ollama pull qwen2.5:3b
ollama serve
```

Run the app:

```bat
streamlit run app.py
```

Then open the URL Streamlit prints (usually http://localhost:8501), enter a
repository such as `https://github.com/psf/requests`, and use the sidebar
buttons in order: **Fetch repository → Analyze Python codebase → Generate AI
explanation**.

## Testing

```bat
.venv\Scripts\activate.bat
python -m pytest tests -v
```

The tests are deterministic and require **no network connection and no installed
model**. GitHub and Ollama responses are mocked.

Coverage includes: function/class/method/nested extraction, docstrings,
absolute/relative imports, syntax errors and empty files, main-guard detection,
entry-point confidence, import resolution, oversized/binary/excluded/missing
files, file and analysis limits, revision-scoped cache isolation, TTL cache
behaviour, prompt/context size limits, evidence formatting with file paths and
line numbers, and graceful handling when Ollama is unavailable. Phase 3 adds:
issue-query construction, pull-request exclusion, bounded issue fields,
issue-search caching, deterministic ranking and tie-breaking, weight
renormalisation, difficulty estimation, score/confidence independence, JSON
explanation parsing/fallback, and the guarantee that a model can never alter the
deterministic score or introduce file paths that do not exist. Phase 4 adds:
issue-to-code matching (explicit vs keyword, whole-token boundaries), acceptance
criteria extraction from issue text only, facts/hypotheses separation, learning
path ordering and reinforcement, checklist adaptation and per-issue scoping,
defensive JSON parsing, model-symbol validation, and a Streamlit UI smoke test of
the Contribution Guide tab.

## Security model

* **No execution:** analyzed code is never imported, evaluated, or run. Only
  `ast.parse` is used.
* **No writes upstream:** the app never commits, pushes, or modifies any
  remote repository.
* **Untrusted input:** repository files, READMEs, and metadata are treated as
  untrusted data. Prompt injection in repository content is ignored by design.
* **Bounded retrieval:** per-file size limit, max file count, directory
  exclusions, binary detection, and timeouts.
* **Path safety:** repository URLs and paths are validated; `..` traversal is
  rejected.
* **Secrets:** an optional GitHub token is read from the sidebar or
  `GITHUB_TOKEN`-style input and is only placed in request headers. Tokens and
  environment variables are never logged or displayed.
* **Rate limits:** GitHub `403/429` rate-limit responses raise a clear,
  actionable error instead of failing silently.
* **Issue text is untrusted too:** issue titles, bodies, labels, and authors are
  treated exactly like repository content — bounded, never executed, and
  embedded in explicit untrusted blocks for the model. A model's suggested file
  paths are validated against the repository before being shown.
* **Score integrity:** the deterministic recommendation score is computed in
  pure Python and is never produced or modified by a model.
* **Guidance integrity:** contribution guides, learning paths, and checklists are
  built deterministically from real data. Acceptance criteria are only quoted
  when the issue author wrote them, and facts are always separated from
  labelled hypotheses.
* **Symbol validation:** when the local model explains a file, backticked symbol
  names are checked against the file's real symbols; invented names are dropped
  and reported. Plans are labelled as hypotheses, never as facts.

## Example analysis: `psf/requests`

1. Enter `https://github.com/psf/requests` and click **Fetch repository**.
2. Click **Analyze Python codebase**. CodePath AI retrieves the `requests`
   package files (e.g. `requests/api.py`, `requests/sessions.py`,
   `requests/models.py`), parses them, and lists classes such as `Session` and
   functions such as `get` / `post`.
3. Open the **Entry points** tab: `requests/api.py` is marked **HEURISTIC**
   (filename/convention), while any module with an `if __name__ == "__main__"`
   guard is marked **EVIDENCE**.
4. Open the **Dependencies** tab to see how `requests` modules import each
   other and which imports are external (`urllib3`, `charset_normalizer`, …).
5. Click **Generate AI explanation** (with Ollama running) for an
   evidence-cited architecture summary and reading order.
6. Open the **Find Your First Contribution** tab, enter a few skills
   (e.g. `python, docs`), keep the `good first issue` label filter, and click
   **Fetch & rank issues**. You get a deterministic, evidence-backed ranking;
   optionally click **Explain this issue (local model)** for a beginner-friendly
   walkthrough that never changes the score.
7. Open the **Contribution Guide** tab, pick an issue, and click **Build
   contribution guide**. You get the issue's problem and objective, its
   candidate files (each with the exact reason it was surfaced), an ordered
   investigation sequence, questions to ask, a personalized **Learning path**,
   and an issue-scoped **Progress** checklist. Optionally explain an individual
   file or draft a plan with the local model.

`requests` is a library, so it may legitimately have **no** confirmed
application entry point. CodePath AI reports this rather than inventing one.

## Roadmap

* Additional language analyzers behind the existing interfaces.
* Lightweight dependency visualization.
* Deeper issue-to-file linking that also reuses the Phase 2 dependency graph.
* Contributor-history awareness (respecting GitHub's public API limits).

## License

MIT — see `LICENSE`.
