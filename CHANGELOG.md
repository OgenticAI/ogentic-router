# Changelog

All notable changes to ogentic-router will be documented here. Format
loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## Unreleased

### Added
- MCP tool surface (OGE-586): `ogentic-router serve --mcp` boots a stdio MCP
  server with four shape-only tools — `router.classify_route`, `router.policies`,
  `router.adapters`, `router.evaluate_dry` (adapter never called;
  `include_outgoing_prompt` opt-in for the post-redaction text). `build_server`
  lazy-imports the MCP SDK. `Router` gained a `backends` descriptor +
  `.backends` property for the adapters tool.
- Audit integration (OGE-584): `Router` emits one shape-only
  `RouteDecisionAudit` row per `route()` call — sensitivity score, category
  labels, chosen backend, HMAC `request_id`, `prompt_hash` — never the raw
  prompt, error paths included. Sinks: `NoopSink` (default), `LocalFileSink`
  (JSON-lines, fsync + cross-platform file lock), `OgenticAuditSink`
  (forward-compat for the HMAC-chained `ogentic-audit` log). Configure via the
  `audit:` block in `router.yaml`. `filelock` moved into the base install.

## 0.1.0 — 2026-06-13

First PyPI release. Wave-2 baseline.

### Added
- Policy DSL (YAML, first-match-wins, predicates: groups_include/exclude,
  sensitivity_score_gte/lt, category_in/not_in).
- Router class — wires Shield classification → Policy → backend selection.
- Adapter Protocol (async chat) + four built-in adapters:
  OpenAI, Anthropic (cloud); Ollama, llama.cpp (local, loopback-only).
- CLI scaffold (`ogentic-router` entrypoint).
- Optional extras: [shield], [cloud], [local], [server], [mcp], [audit].

### Out of scope (next release)
- OpenAI-shaped FastAPI server + `serve` CLI subcommand (v0.2).
- Audit integration (v0.2).
- MCP tool surface (v0.2).
