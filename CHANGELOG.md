# Changelog

All notable changes to ogentic-router will be documented here. Format
loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

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
