# Sotto Desktop integration guide

> **Status: the integration is not built yet.** This is a *plan*, verified
> against the `sotto-desktop` repo as of commit `5db37b0` (2026-06-24), not a
> description of working code. Every claim below about Sotto's current state was
> checked in that repo; where something doesn't exist, this guide says so.

Sotto Desktop is the intended primary downstream consumer of ogentic-router.
This guide records what Sotto has today, what the router offers, and the
concrete seam between them.

## What exists in Sotto today (verified)

Sotto's Rust backend is a **scaffold of stubs** — six files under
`src-tauri/src/` (`main`, `lib`, `shield`, `audit`, `llama`, `vault`), each a
doc-comment plus a `status()` returning a `&'static str`. The only registered
Tauri command is `status()`. Concretely:

| Piece | Actual state |
|---|---|
| `src-tauri/src/route.rs` | **Does not exist.** `route.rs` is the *planned* filename (`docs/ARCHITECTURE.md`); "OSS F1 ticket pending; Sotto-side integration follows". (Note: not `router.rs`.) |
| Router invocation | **None.** `Cargo.toml` declares only `tauri`, `tauri-plugin-opener`, `serde`, `serde_json` — no HTTP client, no `pyo3`, no process-spawn crate. `capabilities/default.json` grants only `core:default` + `opener:default`, so the app currently *cannot* shell out or make HTTP calls. |
| Shield | Stub returning `"stub — see OGE-57"`. Documented intent: subprocess-invoke the `ogentic-shield` Python CLI and parse JSON. |
| Audit | Stub returning `"stub — see OGE-59"`. Nothing is written anywhere — no path, no key, no I/O. |
| llama.cpp / Ollama | Stub. No endpoint, port, or model path in code. Documented intent: bundled `llama.cpp` as a Tauri sidecar. |
| Config file | **None.** No TOML/JSON app config, no serde config struct. The only persisted state is frontend UI state in `localStorage` under `sotto-desktop-state-v1`. |

Two doc inconsistencies worth knowing: `docs/ARCHITECTURE.md` labels Shield and
Audit "shipped / shipping today" — that is aspirational; `README.md`'s "🚧 Stub"
markers are the accurate ones.

The closest thing to a routing vocabulary that exists today is a UI toggle in
`src/state/sotto-store.ts`: `outboundRoute: "local" | "redacted-cloud" | "raw-cloud"`
and `profile: "legal" | "therapy" | "finance"`. These are mock controls with no
backend consumer — but they map cleanly onto the router's tiers, which is a good
sign for the design.

## The integration seam

Because nothing is wired, the first real decision is **how** Sotto reaches the
router. The router is Python; Sotto is Rust/Tauri. Two supported options:

### Option A — local HTTP (recommended to start)

Sotto spawns `ogentic-router serve` as a sidecar and talks to it over the
loopback OpenAI-shaped endpoint (`127.0.0.1:8080/v1/chat/completions`).

- **Pros:** no Python embedding; the router is a black box behind a stable wire
  format; Sotto can use any HTTP client. Works today — verified end to end
  against a local Ollama backend.
- **Cons:** a second process to supervise; and in v0.1 the *server* dispatches to
  the policy's `default_backend` rather than classifying per request (the full
  Shield→policy pipeline on the server is v0.2). For per-prompt sensitivity
  routing before v0.2, use Option B or call the MCP surface.
- **Requires:** adding an HTTP dependency and a shell/sidecar capability to
  `capabilities/default.json` — neither is present today.

### Option B — MCP (stdio)

Sotto spawns `ogentic-router serve --mcp` and calls the tools over stdio:
`router.classify_route` (which backend and why), `router.evaluate_dry` (plus the
post-redaction outgoing text, opt-in), `router.policies`, `router.adapters`.

- **Pros:** gives the **full per-prompt decision today** (the library path, not
  the v0.1 server shortcut), shape-only by default, and it's the same surface
  Claude Desktop uses.
- **Cons:** stdio process management; not a chat-completion path — Sotto still
  dispatches the actual completion itself (which is arguably correct: decide
  here, dispatch there).

Whichever is chosen, the privacy-relevant decision is made **before** any
dispatch, and the router emits a shape-only audit row per decision.

## Config Sotto will need to ship

Two files inside the app bundle:

1. **`router.yaml`** ([example](../examples/router.yaml)) — the router's config:
   `version: 1`, `policy_path`, an optional `shield` block, an optional `audit`
   block, and `backends[]`. Point `ROUTER_CONFIG` at Sotto's packaged copy.
   Note the shipped example is deliberately **keyless and local-only** so it
   starts with no API keys; a cloud backend requires its `api_key_env` variable
   to be set or the server refuses to start.
2. **`policy.yaml`** ([example](../examples/policy.yaml)) — the routing rules.
   Sotto's default should keep privilege / PHI / MNPI and high-sensitivity
   content local. See [POLICY_REFERENCE.md](POLICY_REFERENCE.md).

Keys are never stored in these files — the router reads each backend's key from
the environment variable named by `api_key_env` at dispatch time.

[`examples/sotto_desktop_config.toml`](../examples/sotto_desktop_config.toml) is
an illustrative Sotto-side wrapper (TOML, pointing at the YAML above). It is a
**proposal** — Sotto has no config format today, so nothing in Sotto reads it
yet.

## Lifecycle, per call

1. **Classify** — `Router.classify(prompt)` → `ShieldClassification` (`score`,
   `category_groups_found`, `top_category`, `entity_count`, `text_hash`).
   In-process; nothing leaves the machine.
2. **Decide** — `Router.route(prompt)` → `RouteDecision` (`backend_id`,
   `rule_id`, `transform`, `reasoning`).
3. **Redact (optional)** — if `transform == shield_redact`, apply
   `Shield.redact()` before dispatch.
4. **Dispatch** — call the adapter named by `decision.backend_id`. Local
   adapters are loopback-enforced; cloud adapters are host-allowlisted.
5. **Record** — the router emits a shape-only `RouteDecisionAudit` row to the
   configured sink (`audit:` block). This is what Sotto's audit ledger should
   read from.

## Audit

The router ships a `LocalFileSink` that appends one shape-only JSON-lines row
per decision (fsync + cross-platform file lock). Point `audit.path` at a
per-user location, e.g.
`~/Library/Application Support/Sotto/router-decisions.jsonl`.

Rows carry a `prompt_hash`, sensitivity score, category labels, chosen backend,
whether redaction applied, and — on failures — the exception **class name only**.
Never raw prompt text. See [PRIVACY_POSTURE.md](PRIVACY_POSTURE.md).

This matters for Sotto specifically: its Audit Ledger UI currently renders
hardcoded fixture rows (e.g. `"decision: send-redacted → cloud"`). Those fixtures
are a close match for the real `RouteDecisionAudit` shape, so wiring the ledger
to a real `audit.jsonl` is mostly a data-source swap.
[`examples/audit_replay.py`](../examples/audit_replay.py) shows the read path.

The HMAC-*chained*, tamper-evident `ogentic-audit` sink is a separate step and
lights up once that library publishes.

## Ownership

There is no `CODEOWNERS` in `sotto-desktop` and no separate "Sotto team" —
every product commit is authored by David Oladeji (`david@ogenticai.com`); the
other commits are factory-kit sync bots. Work is tracked under the Linear
project *Sotto Desktop v1*. So the reviewer for this guide is David.
