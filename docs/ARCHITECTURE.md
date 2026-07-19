# Architecture

`ogentic-router` decides **where a prompt is allowed to go before it leaves the
device**, based on the *sensitivity* of the content. The full design rationale
is in [ADR-0001](adr/0001-router-architecture.md); this page is the operational
"where does data actually flow" explainer, matched to what v0.1 ships.

## The four-step pipeline

```
┌─────────────────────────────────────────────────────────────┐
│ Caller (library call, or OpenAI SDK → local endpoint swap)   │
└───────────────────────────┬─────────────────────────────────┘
                            │  prompt
                            ▼
┌─────────────────────────────────────────────────────────────┐   ON-DEVICE
│ ogentic-router                                              │   IN-PROCESS
│                                                             │   (no Ogentic
│  1. Classify   Shield.analyze(prompt)  → score + groups     │    infra here)
│  2. Decide     Policy.evaluate(...)    → RouteDecision       │
│  3. (opt)      Shield.redact()         → if transform set    │
│  4. Dispatch   Adapter.chat(...)       → chosen backend      │
└──────────┬──────────────────┬───────────────────────────────┘
           ▼                  ▼                    ▼
      Local backend      Cloud (cleared)     Cloud (redacted)
      Ollama/llama.cpp   OpenAI/Anthropic    same, on redacted text
      (loopback-only)                        payload
```

Steps 1–3 always run locally. Only step 4, and only for a non-local backend,
crosses the network — and only with content the policy has cleared or redacted.

## Where the boundary is drawn in v0.1

The library and the server sit at different points on the road to the full
pipeline. Be precise about which you're using:

| Surface | Classify (1) | Decide (2) | Redact (3) | Dispatch (4) |
|---|---|---|---|---|
| **`Router.route(prompt)`** (library) | ✅ Shield | ✅ policy | decision flags it | ✗ returns the decision; you dispatch |
| **Adapter `.chat(...)`** | — | — | — | ✅ calls the backend |
| **Server `POST /v1/chat/completions`** | ✗ v0.2 | routes on `default_backend` | ✗ v0.2 | ✅ |

Two deliberate design points:

1. **`Router.route()` returns a decision, not a completion.** It makes the
   privacy-relevant choice — classify → policy → `RouteDecision` — and stops.
   You then hand the decision to the adapter named by `decision.backend_id`.
   Separating *decide* from *dispatch* means the audit record exists before any
   byte is sent, and a misconfigured dispatcher can't route sensitive content
   outward against the decision.

2. **The server's per-request Shield pipeline is a v0.2 gap.** In v0.1 the
   OpenAI-shaped server selects the policy's `default_backend` and dispatches;
   it does not yet classify each request and evaluate the rules per-call. It
   *does* load the policy and expose it at `GET /v1/policy` so you can see the
   rules that will apply once wired (OGE-584). Until then, use the library
   `Router` when you need the actual per-prompt decision.

## Components

| Component | Responsibility | Code |
|---|---|---|
| Classifier | Sensitivity score + category groups. Default: `ogentic-shield`. Pluggable — any object with `analyze(text)`. | `router.py`, `classification.py` |
| Policy engine | Declarative YAML → `RouteDecision`. Pure, deterministic, first-match-wins. | `policy/` |
| Adapters | Call a backend. OpenAI, Anthropic (cloud); Ollama, llama.cpp (local). | `adapters/` |
| Cloud allowlist | Cloud adapters accept only `api.openai.com` / `api.anthropic.com` (extendable by env var). | `adapters/_allowlist.py` |
| Loopback guard | Local adapters accept only `localhost` / `127.0.0.1` / `::1`. Non-loopback raises `LocalhostOnlyError`. | `adapters/_localhost.py` |
| Server | OpenAI-shaped FastAPI endpoint-swap surface (`[server]` extra). | `server/` |
| CLI | `ogentic-router serve` and `ogentic-router route`. | `cli/` |

## Server endpoints (as shipped)

| Method | Path | Behavior |
|---|---|---|
| GET | `/healthz` | `{"status": "ok"}`. |
| GET | `/v1/models` | OpenAI-shaped model list derived from configured backends. |
| POST | `/v1/chat/completions` | Chat completion; streaming (SSE) and non-streaming. Routes to `default_backend` in v0.1. `503` if started with no config. |
| GET | `/v1/policy` | The loaded policy: version, `default_backend`, rule count, rules. `404` if no policy. |
| GET | `/v1/decision/{id}` | Stub in v0.1 — returns "Decision audit is not available in v0.1 (ogentic-audit integration pending)". |

The server reads its config path from the `ROUTER_CONFIG` environment variable
(or an explicit `--config`). Default bind is `127.0.0.1:8080`; binding to
`0.0.0.0` prints a loud warning, because the local adapters' loopback guarantee
assumes the server itself isn't exposed off-box.

## Audit emission

`Router.route()` emits one shape-only `RouteDecisionAudit` row per call via the
configured `audit_sink` (default `NoopSink`), including on error paths. Sinks:
`NoopSink` (drop), `LocalFileSink` (JSON-lines, fsync + file-lock), and
`OgenticAuditSink` (forward-compat for the HMAC-chained `ogentic-audit` log).
Emission is fire-and-forget — a sink failure is logged at WARNING and swallowed;
routing already happened. Rows carry a `prompt_hash`, never raw text. See the
[Audit](../README.md#audit) section and `src/ogentic_router/audit/`.

## What is not wired yet

- **HMAC-chained audit** — `OgenticAuditSink` raises at construction until
  `ogentic-audit` publishes to PyPI. Local-file and Noop sinks work today.
- **Server decision lookup** — `GET /v1/decision/{id}` is a stub; per-request
  server-side audit + lookup lands with the server's v0.2 Shield pipeline.

## MCP tool surface

`ogentic-router serve --mcp` boots a stdio MCP server exposing four shape-only
tools — `router.classify_route`, `router.policies`, `router.adapters`,
`router.evaluate_dry` — so an assistant can ask which backend a prompt would take
*without* firing the LLM call. Same `router.yaml`; the transport diverges at
boot. `build_server(router)` lazy-imports the MCP SDK, so the module imports fine
without the `[mcp]` extra. See the [MCP](../README.md#mcp) section and
`src/ogentic_router/mcp/`.
