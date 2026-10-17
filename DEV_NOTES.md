# CodePath AI — Development Notes (Phases 3–4)

Design log for the contribution-recommendation engine (Phase 3) and the
contribution-guidance / learning-path features (Phase 4). These notes record
*why* things are the way they are, not just what the code does.

## 1. Repository state reconciliation (must read)

At the start of Phase 3 the working tree was **mixed**: two incompatible
generations of the same modules coexisted.

| Concern | Generation **A** (kept) | Generation **B** (discarded) |
| --- | --- | --- |
| `analyzer/github_client.py` | `parse_github_url`, `RepoMetadata`, `fetch_python_files` | `parse_repo_url`, `get_repo_metadata`, dict metadata |
| `ai/model_client.py` | `OllamaClient`, `config_from_env`, `resolve_model` | `ModelClient`, `check_availability` |
| `analyzer/file_scanner.py` | full AST scanner (`ModuleInfo`, `analyze_files`) | 19-line tree stub |
| `ai/prompts.py` | `SYSTEM_PROMPT`, `build_user_prompt` | `SYSTEM_PROMPT_TEMPLATE` |

`analyzer/code_map.py` and `ai/context_builder.py` both require
`ModuleInfo` / `analyze_source`, which only generation **A** provides. The
project is therefore standardised on **A**:

* Restored `analyzer/github_client.py`, `ai/model_client.py`,
  `analyzer/file_scanner.py`, and `ai/prompts.py` to the A implementations.
* Restored the A `app.py` (631 lines) over the thin B variant.
* Rewrote `tests/test_github_client.py` and `tests/test_model_client.py` against
  the A API.

There is no committed baseline (git has a single "Initial commit"; these files
were untracked), so the A implementations in this tree are the canonical ones.

## 2. Phase 3 module layout

```
analyzer/issue_client.py   # read-only GitHub issue search (Phase 3)
analyzer/recommender.py    # profile, difficulty, deterministic scoring
ai/issue_explainer.py      # optional local-model explanation
```

`analyzer/cache.py` gained a small `TTLCache` used for issue-query caching.

## 3. North-star principles

1. **Deterministic scoring, separate confidence.** A model may *explain* a
   match; it may never *produce* the score. `match_score` lives in pure Python.
   `confidence` reports evidence sufficiency and is intentionally independent of
   `match_score` so a label-only match is not mistaken for a well-understood one.
2. **No fabrication.** Every evidence line cites a real field of a real issue
   (a label, a title token, the URL). The explainer validates model-suggested
   file paths against the repository and drops those that do not exist.
3. **Untrusted by default.** Issue titles, bodies, labels, and profile text are
   bounded, never executed, and wrapped in explicit `UNTRUSTED` blocks.
4. **Graceful degradation.** Every AI path has a deterministic fallback. The
   feature is fully usable with no Ollama and no network beyond GitHub.

## 4. Scoring model (`analyzer/recommender.py`)

Six components, each in `[0, 1]`, combined with configurable weights that are
renormalised to sum to `1.0`:

| Component | Question it answers |
| --- | --- |
| `skill` | Do the user's declared skills appear in the issue? |
| `label_fit` | Are the labels beginner-appropriate? |
| `difficulty_fit` | Does estimated effort match the user's experience? |
| `scope` | Is the change bounded (short description, no huge thread)? |
| `interest` | Do declared interests appear in the issue? |
| `recency` | Is the issue recent (still actionable)? |

`match_score = clamp(Σ rawᵢ · weightᵢ, 0, 1) · 100`, rounded to one decimal.
Ordering is `score desc`, then `issue number asc`, so runs are reproducible.

### Difficulty estimation

Effort is a heuristic from the issue's own text and labels (beginner labels
lower it; `hard`/`epic`/`security`/… and hard-topic terms raise it; a missing
body makes it explicitly *unknown*). Every contributing signal is listed so a
reviewer can audit the estimate. It is labelled as an estimate, never a promise.

### Confidence

`compute_confidence` adds weight for a present body, labels, a long-enough
title, a parseable timestamp, and a file hint. It never reads `match_score`.
Low confidence (< 0.5) attaches an explicit caution.

### Recency and determinism

Recency is computed against an injectable `now` (defaults to `datetime.now(UTC)`).
Tests pass a fixed `now` so no test is time-dependent or flaky.

## 5. Issue discovery (`analyzer/issue_client.py`)

* `IssueClient` **subclasses `GitHubClient`** to reuse the shared session, token
  handling, timeouts, path validation, and typed errors
  (`RateLimitError`, `RepoNotFoundError`, `GitHubError`).
* Query building is qualifier-based (`repo:`, `is:issue`, `is:open`,
  `label:"…"`) rather than free text, so results are predictable.
* Pull requests are excluded structurally (`is:issue`) and defensively
  (`pull_request` key present → skipped and counted).
* All fields are truncated (`MAX_TITLE_CHARS`, `MAX_BODY_CHARS`), the limit is
  clamped to `MAX_ISSUE_LIMIT`, and results are cached via `TTLCache`.
* **Cache correctness:** a cache hit returns `dataclasses.replace(cached, cached=True)`
  — a copy — so marking a result as cached never mutates the stored snapshot or
  a previously returned object. (This exact bug is covered by
  `test_search_issues_uses_cache_on_second_call`.)

## 6. AI explanation (`ai/issue_explainer.py`)

* Prompts live in `ai/prompts.py` (`ISSUE_SYSTEM_PROMPT`,
  `build_issue_explanation_prompt`). The system prompt forbids following
  instructions found inside issue text and forbids inventing URLs/labels/files.
* The model is asked for a single JSON object. Parsing is defensive: it accepts
  raw JSON or fenced ```` ```json ```` blocks, and otherwise extracts the first
  balanced `{...}` object.
* If parsing fails, or the model errors, or no client is configured, the module
  returns `fallback_explanation(...)` built only from real fields. The fallback
  is explicitly marked `used_model=False` / `parsed_ok=False`.
* `known_paths` lets the caller supply real repository paths; model-suggested
  paths not in that set are moved to `dropped_files` and noted in `limitations`.
* `IssueExplanation` has **no score field** at all. `score_note` restates that
  the deterministic score is authoritative.

## 7. UI (`app.py`, "Find Your First Contribution")

A seventh tab. Flow: profile → issue discovery (labels, limit, keywords,
advanced weights) → ranked table → per-issue evidence + cautions → optional
local-AI explanation → beginner workflow (fork → branch → locate → minimal
change → test → PR). Session keys: `issue_search`, `issue_recs`,
`issue_profile`, `issue_expl`.

## 8. Testing strategy

All Phase 3 tests are hermetic: GitHub and Ollama are mocked; no network, no
model. Determinism is enforced with a fixed `now`. Notable guarantees under
test:

* Cache hits do not mutate prior results.
* Score is bounded, reproducible, and independent from confidence.
* Ranking is stable and tie-breaks by issue number.
* A model cannot change the score, and unknown file paths are dropped.
* Injection-prone text stays inside untrusted delimiters.

Run everything with:

```bat
.venv\Scripts\python.exe -m pytest tests -q
```

## 9. Known limitations / future work

* Static analysis and issue ranking are heuristics; the UI says so explicitly.
* Only Python is analysed; the analyzer interfaces are the extension point.
* GitHub Search API is rate-limited; the TTL cache and optional token mitigate
  this, and the UI surfaces typed rate-limit errors.

---

# Phase 4 — Contribution guidance & learning paths

Phase 4 turns a chosen issue into something a beginner can actually *do*:
a step-by-step contribution guide, a personalized learning path, and an
interactive checklist. The governing rule is unchanged from Phase 3: **facts
come from real data, model output is only ever a labelled suggestion**.

## 10. Phase 4 module layout

```
analyzer/issue_linker.py        # issue → candidate repository files (facts + hypotheses)
analyzer/contribution_guide.py  # issue-specific guide (problem, steps, risks)
analyzer/learning_path.py       # ordered, personalized topics
analyzer/checklist.py           # deterministic tickable steps + scoping
ai/structured_output.py         # shared, defensive JSON parsing/cleaning
ai/contribution_assistant.py    # optional local-AI file explanation + plan draft
```

`ai/prompts.py` gained `FILE_EXPLAIN_SYSTEM_PROMPT` / `build_file_explanation_prompt`
and `PLAN_SYSTEM_PROMPT` / `build_plan_prompt`.

## 11. Issue-to-code linking (`analyzer/issue_linker.py`)

The linker surfaces candidate files and is deliberately conservative about what
it claims:

* **Two evidence grades.** A file is `confirmed` when the issue text explicitly
  names a real path, module, class, or function in the analysed repository.
  It is `likely` when only vocabulary/docstring keywords overlap. Only
  `confirmed` counts as an explicit reference.
* **Word-boundary matching.** Symbol matches require whole tokens, so a word
  like "prerequests" never matches the `requests` module.
* **No ownership claims.** The result does not say a file *causes* an issue; it
  says a file *may* be relevant and gives the exact reason string(s).
* **Bounded and ordered.** Results are capped (default 8) and sorted by score
  then path, so output is reproducible; truncation is flagged.
* **Real URLs only.** GitHub blob links are built from the repository's own
  `html_url` and revision SHA, and only when that metadata exists.

## 12. Contribution guide (`analyzer/contribution_guide.py`)

* **Acceptance criteria are never invented.** They are extracted only when the
  issue author wrote them (checklist items, bullets under an acceptance/requirements
  heading, or explicit "should/must/expected to" sentences) and are presented as
  verbatim quotes from untrusted issue text.
* **Facts vs hypotheses are separated.** `facts` come from data we hold (issue
  number/state/comments, module count, links). `hypotheses` are things to verify,
  and every `GuideRisk` is tagged `verified` or `hypothesis`.
* **Missing information is surfaced.** A missing body, absent criteria, and
  unlocated code become explicit `unknowns` and `questions`, not silent gaps.
* Investigation steps adapt to the issue kind (bug → reproduce first, docs →
  read the docs, feature → plan the change).

## 13. Learning path (`analyzer/learning_path.py`)

* **Personalization without assumptions.** Topics are marked `reinforce` when the
  declared profile already mentions a related skill, else `to learn`. The notes
  state that *not* mentioning a skill is never treated as evidence the person
  lacks it.
* **Ordered prerequisites.** Python fundamentals first, then issue-type skill,
  the most relevant file, testing, detected frameworks, and finally the Git/GitHub
  PR workflow.
* **Curated links only.** External resources come from a fixed map of canonical
  documentation sites (python.org, docs.pytest.org, git-scm.com,
  docs.github.com, framework docs). No URL is ever generated from model output.
* **Framework detection** is static (from parsed imports) and listed, not guessed.

## 14. Checklist (`analyzer/checklist.py`)

* Five ordered categories: understand → setup → implement → verify → share.
* Adapts to issue kind (bug/docs/feature), to whether tests exist, and to whether
  the repository advertises contribution guidelines.
* `checklist_key(repo, issue)` is a stable, lowercased id so the UI can scope
  progress to a single issue. **Progress never leaks between issues** because
  widget keys are namespaced by that key.

## 15. Optional AI (`ai/contribution_assistant.py`)

* Two calls: `explain_file` and `draft_contribution_plan`. Both accept a client
  and return a deterministic fallback when none is configured or the model fails.
* **Anti-fabrication for symbols.** `explain_file` validates backticked symbol
  names against the symbols that actually exist in the parsed file; invented
  names are moved to `dropped_symbols` and noted in `limitations`.
* **Plans are hypotheses.** `PlanDraft.is_hypothesis` is always `True`, and the UI
  states the deterministic checklist remains authoritative.
* Shared parsing/cleaning lives in `ai/structured_output.py` (raw, fenced, or
  embedded JSON; control-character stripping; bounded lists with de-duplication).

## 16. UI (`app.py`, "Contribution Guide")

An eighth tab, with three sub-tabs: **Guide**, **Learning path**, and **Progress**.
It reuses the Phase 3 recommendation set (`issue_recs`) and the analysed
`modules`/`file_contents`. Selecting a different issue resets the built guide,
plan, and file explanation. Progress is stored in
`st.session_state["checklist_state"]` keyed by `checklist_key`, and "Reset
progress" also clears the namespaced widget keys before rerunning.

## 17. Phase 4 testing strategy

Phases 1–3 remain green. Phase 4 adds deterministic, offline tests:

* `tests/test_issue_linker.py` — explicit vs keyword matching, word boundaries,
  symbol/line capture, URL construction, truncation, determinism.
* `tests/test_contribution_guide.py` — criteria extraction, missing-body handling,
  facts/hypotheses separation, kind-specific steps.
* `tests/test_learning_path.py` — ordering, reinforcement, framework/test
  detection, canonical links, "skill is not assumed" note.
* `tests/test_checklist.py` — categories, adaptation, stable keys, progress.
* `tests/test_contribution_assistant.py` — fallbacks, JSON parsing, symbol
  validation, untrusted prompt wrapping, no score fields.
* `tests/test_structured_output.py` — JSON extraction and cleaning edge cases.
* `tests/test_phase4_ui.py` — Streamlit `AppTest` smoke test of the real tab.

## 18. Phase 4 scope guardrails

No repository code is executed or modified; no branches, commits, or PRs are
created; nothing is posted to GitHub. Phase 5 (UX polish), Phase 6 (hardening),
and Phase 7 (release) remain separate.

