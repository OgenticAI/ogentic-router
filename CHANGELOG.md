# Changelog

All notable changes to ogentic-router will be documented here. Format
loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## 0.2.0 — Unreleased

0.1.0 shipped the library, the four adapters and one CLI command (`serve`).
0.2.0 makes the CLI usable on its own, adds audit and MCP, and turns on the two
safety rails (budget ceiling, deny-cloud) by default. Two of those defaults
change behaviour — see **Changed**.

### Added
- **`ogentic-router route`** (OGE-585): runs Shield + policy on one prompt and
  prints the decision (`backend_id`, `rule_id`, `transform`, `reasoning`) as JSON
  or `--output text`. Reads the prompt from stdin (recommended: argv is visible in
  the process table) or `--prompt`. `--execute` also dispatches to the chosen
  backend; `--model` and `--budget-ceiling` feed the cost check.
- **`route --classification <path|->`**: decide from a Shield analysis the caller
  already has (the JSON `ogentic-shield analyze --output json` prints), without
  running Shield and without the prompt text. For apps that already run Shield
  themselves. Same policy evaluation, deny-cloud check and audit row as the
  prompt path; malformed input exits 2 with the offending field named. Library
  equivalents: `Router.route_analysis()` and
  `ogentic_router.classification.analysis_from_json()`; new
  `ClassificationError`.
- **`ogentic-router policies validate | show | dry-run`** (OGE-585): check a
  policy file (exit 0/2), print its rules as a table, or show the decision for a
  prompt without calling any backend. Router-backed commands fall back to
  `$ROUTER_CONFIG` / `$OGENTIC_ROUTER_CONFIG`.
- **Audit** (OGE-584): one shape-only `RouteDecisionAudit` row per routing call,
  error paths included — prompt fingerprint, score, category labels, chosen
  backend, HMAC `request_id`; never the prompt. Sinks: `NoopSink` (default),
  `LocalFileSink` (JSON lines, fsync, file lock), `OgenticAuditSink`
  (forward-compatible; waits on `ogentic-audit`). Configured by `audit:` in
  `router.yaml`.
- **MCP tool surface** (OGE-586): `ogentic-router serve --mcp` starts a stdio MCP
  server with `router.classify_route`, `router.policies`, `router.adapters` and
  `router.evaluate_dry`. Needs the `[mcp]` extra.
- **Budget ceiling** (OGE-1061): `Router.route(..., model=, budget_ceiling=)`,
  `BudgetCeilingExceeded`, and a per-model cost estimator. The check runs before
  Shield and before any network call.
- **Deny-cloud** (OGE-1135): content Shield puts in PRIVILEGE, PHI or MNPI can
  never resolve to a cloud backend. The router raises `CloudRouteDeniedError`
  before dispatch even if a rule is wrong or mis-ordered. Configured by a new
  `deny_cloud` policy block.
- Python 3.13 support (OGE-1028).
- Docs: README rewrite, policy reference, architecture, comparison, privacy
  posture, Sotto integration guide, ADR-0001, and the usual OSS files (NOTICE,
  code of conduct, security policy).

### Changed
- **Budget enforcement is ON by default** (OGE-1120). A policy without a
  `budget:` block now enforces a $1.00 per-call estimated-cost ceiling, so
  `route()` can raise `BudgetCeilingExceeded` where 0.1.0 never did. Opt out per
  engagement with `budget: {enforce: false}`, or per call with
  `budget_ceiling=None`.
- **Deny-cloud is ON by default** (OGE-1135). `route()` can raise
  `CloudRouteDeniedError` for regulated content headed to a cloud backend. Opt
  out with `deny_cloud: {enforce: false}` or narrow `groups`.
- `filelock` moved into the base install (used by `LocalFileSink`).
- `examples/router.yaml` is now local-only and needs no API key, so the
  documented `serve` quickstart starts without `OPENAI_API_KEY`.
- The HTTP server reports the package version instead of a hard-coded `0.1.0`.

### Fixed
- The HTTP server crashed at startup with `TypeError` for any Ollama or
  llama.cpp backend: it passed `base_url=` where those adapters take
  `endpoint=` (OGE-587).
- `ogentic-router route` printed a traceback on `CloudRouteDeniedError`; it now
  prints the error and exits 1.

### Removed
- The in-repo Streamlit demo; it now lives in
  [OgenticAI/router-streamlit-demo](https://github.com/OgenticAI/router-streamlit-demo).

### Known limitations
- The HTTP server still dispatches every request to the policy's
  `default_backend`. Per-request Shield → policy selection is in the library and
  CLI, not yet in the server.
- `route --classification` skips the budget check: the estimate needs the prompt
  text, which that path never sees.

## 0.1.0 — 2026-06-13

First PyPI release. Wave-2 baseline.

### Added
- Policy DSL (YAML, first-match-wins, predicates: groups_include/exclude,
  sensitivity_score_gte/lt, category_in/not_in).
- Router class — wires Shield classification → Policy → backend selection.
- Adapter Protocol (async chat) + four built-in adapters:
  OpenAI, Anthropic (cloud); Ollama, llama.cpp (local, loopback-only).
- OpenAI-shaped FastAPI server (OGE-583) and the `ogentic-router serve` CLI
  command — the only CLI command in 0.1.0.
- Optional extras: [shield], [cloud], [local], [server], [mcp], [audit].

### Out of scope (next release)
- `route` / `policies` CLI commands (0.2.0).
- Audit integration (0.2.0).
- MCP tool surface (0.2.0).
