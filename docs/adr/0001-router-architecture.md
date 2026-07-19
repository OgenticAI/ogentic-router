# ADR-0001 — ogentic-router architecture: on-device, sensitivity-aware LLM routing

- **Status:** Accepted (2026-07-19)
- **Date:** 2026-07-04 (drafted)
- **Deciders:** David (CTO), primitives owner
- **Kind:** OSS library (Apache-2.0), Python ≥ 3.10
- **Related:** ecosystem review R-10 (ship the port layer, sequence primitives behind it) · [`ogentic-shield`](https://github.com/OgenticAI/ogentic-shield) · `ogentic-audit` · `ogentic-adapter-sdk` · [`@ogenticai/agent-core`](https://github.com/OgenticAI/agent-core) Route port · Decisions Log #2 (no vendor lock-in), #6 (Shield+Audit on every call), #8 (private/local as the differentiator)
- **Supersedes:** none. This is the foundational ADR; later decisions (policy DSL grammar, adapter contract v1, streaming protocol) get their own ADRs (0002+).

---

## Context and problem statement

Agents and apps across the ecosystem need to send prompts to an LLM. Today that
choice is hard-wired per app (agent-reviewer imported the Anthropic SDK
directly), and the emerging "AI control planes" — OpenRouter, LiteLLM proxy,
Portkey, Helicone, Merge Gateway — all route on **cost / latency / quality**.
None routes on **content sensitivity**, and every one of them makes the routing
decision **on vendor servers, after the content has already left the user's
machine**. That is structurally disqualifying for the regulated content Ogentic
exists to serve: attorney–client privilege, PHI, MNPI.

We need a router that:

1. Decides **where a prompt is allowed to go before it leaves the device**, based
   on the *sensitivity* of the content (not just price/speed).
2. Keeps sensitive content on a **local** backend; lets only cleared or redacted
   content cross the network.
3. Produces a **court-defensible record** of every routing decision.
4. Is **not** a service Ogentic hosts — there must be no Ogentic infrastructure
   that can ever see user content.
5. Drops into existing code with minimal change, and slots behind
   `@ogenticai/agent-core`'s Route port with **zero agent-code change** (the
   "trust as ports" model, review S2).

## Decision drivers

- **Privacy invariant (non-negotiable):** classification, policy, and audit run
  **in-process on the caller's machine**. Sensitive payloads never transit
  Ogentic or an unapproved third party.
- **No vendor lock-in** (Decisions Log #2): any backend — cloud or local —
  behind one contract.
- **Private/local first** (Decisions Log #8): local LLMs (llama.cpp / Ollama /
  MLX) are first-class backends, not an afterthought.
- **Shield + Audit on every call** (Decisions Log #6): routing sits at the
  `route` step of the `shield → route → audit` spine and must not bypass it.
- **R-10 definition of done:** a dated v0.1 with a local OpenAI-compatible
  endpoint **and** an MCP surface, a real backend via a minimal
  `ogentic-adapter-sdk`, and one end-to-end `shield → route → audit` integration
  test in CI. v0.1 milestone target: **2026-08-31**.
- **Slim core:** the base install must not drag in spaCy/Presidio weights; Shield
  and heavy backends live behind extras.

## Considered options

1. **Adopt a SaaS gateway** (OpenRouter / Portkey / Merge / Helicone). Rejected
   as the primary: the routing decision runs on vendor servers *after* content
   leaves the device — it cannot satisfy the privacy invariant. Retained as
   *optional cloud backends* behind an adapter.
2. **Adopt LiteLLM (proxy or library) as the router.** LiteLLM is an excellent
   multi-provider abstraction and we will reuse it *as a backend adapter*, but it
   routes on cost/latency and has no on-device, sensitivity-first policy model or
   court-defensible audit. Not the router; a backend.
3. **Status quo — per-agent provider config.** Rejected: every agent re-decides,
   nothing enforces the privacy invariant, no shared audit, drift.
4. **Build `ogentic-router` — a local library that classifies with Shield,
   applies a declarative sensitivity policy, calls a backend via the Adapter
   protocol, and emits an audit row.** **Chosen.**

## Decision outcome

Build `ogentic-router` as an **on-device, sensitivity-aware routing library**
(not a hosted service). The library is the product; the CLI, the local
OpenAI-compatible endpoint, and the MCP server are thin surfaces over it. Backends
are reached through the `ogentic-adapter-sdk` Adapter protocol. Cloud gateways and
LiteLLM are consumed *as backends*, never as the decision plane.

```
┌───────────────────────────────────────────────┐
│ Caller (app code, or @ogenticai/agent-core)    │
│  • existing OpenAI/Anthropic SDK → endpoint swap│
│  • or import the library / call the MCP tool    │
└───────────────┬────────────────────────────────┘
                │  prompt (+ optional pre-classification)
                ▼
┌───────────────────────────────────────────────┐   ON-DEVICE / IN-PROCESS
│ ogentic-router (local)                         │   (no Ogentic infra here)
│                                                │
│  1. Classify   → ogentic-shield.analyze()      │  sensitivity score + groups
│  2. Decide     → Policy engine (declarative)   │  sensitivity+intent → tier
│  3. (opt) Redact → shield.redact() prefilter   │  for the "cloud-redacted" path
│  4. Dispatch   → Adapter (ogentic-adapter-sdk) │  call the chosen backend
│  5. Record     → ogentic-audit.append()        │  HMAC-chained decision row
└───────┬───────────────────┬────────────────────┘
        ▼                   ▼                   ▼
   Local backend       Cloud (cleared)     Cloud (redacted)
   llama.cpp/Ollama/   Anthropic/OpenAI/   same, on the
   MLX/vLLM/SGLang     OpenRouter/Together  redacted payload
```

**Key invariant restated:** steps 1, 2, 3, 5 always run locally. Only step 4, for
a *non-local* tier, crosses the network — and only with content the policy has
cleared or redacted.

## Architecture

### Components

| Component | Responsibility |
|---|---|
| **Classifier port** | Wraps `ogentic-shield` (default) → sensitivity score + category groups (PRIVILEGE, PHI, MNPI, …). Pluggable; a caller may inject a classification it already computed. |
| **Policy engine** | Evaluates a declarative policy (YAML/DSL) against the classification + request intent → a **routing decision** (a tier + concrete backend + whether to redact). Pure, deterministic, side-effect-free. |
| **Adapter registry** | Resolves a backend id → an `ogentic-adapter-sdk` Adapter. Backends: `local` (llama.cpp/Ollama/MLX), `openai`, `anthropic`, and any OpenAI-compatible gateway (OpenRouter/Together/Fireworks/DeepInfra/Groq/Cerebras/vLLM/SGLang). |
| **Redactor** | Optional pre-network `shield.redact()` for the cloud-redacted tier; returns the mapping so the caller can rehydrate locally. |
| **Audit emitter** | Writes an `ogentic-audit` decision row (inputs' sensitivity, chosen tier/backend, redaction applied, latency, outcome) — HMAC-chained, user-controlled. |
| **Surfaces** | (a) Python library (the core). (b) CLI. (c) **Local** `localhost:PORT/v1/chat/completions` endpoint-swap server. (d) **MCP** server exposing `router.route` / `router.chat`. All four are the *same* pipeline; the network surfaces bind to localhost by default. |
| **Config** | `routing.yml` — policy + tier→backend mapping + backend credentials via references (never inline secrets; keys resolve from `ogentic-vault` / env). |

### Request lifecycle

1. Caller submits a chat request to any surface (library call, local endpoint, or
   MCP tool). It may include a `classification` it already has (see agent-core
   integration) to skip step 2.
2. **Classify** (if not supplied): `shield.analyze(text)` → `{score, findings[]}`.
3. **Decide:** the policy engine maps `(score, groups, intent, model hint)` to a
   `RoutingDecision {tier, backend, model, redact: bool, reason}`.
4. **Enforce privacy:** if `tier == local`, only local adapters are eligible; the
   engine refuses to emit a cloud backend for content above the threshold
   (fail-closed — a misconfigured cloud fallback can never win for sensitive
   content).
5. **(Optional) redact:** if `redact`, run `shield.redact()`; carry the mapping.
6. **Dispatch** through the Adapter (streaming or unary), with per-backend
   timeout, retry, and failover to the next eligible backend *in the same tier*.
7. **Record:** append the decision + outcome to `ogentic-audit`.
8. Return the response (rehydrated locally if it was redacted).

### Interfaces & contracts

- **Backend contract** — `ogentic-adapter-sdk` `Adapter` protocol (Wave-2
  primitive): an async `generate(request) -> response` (+ streaming) with a
  normalized request/response shape. Zero runtime dependency on other `ogentic-*`
  packages; optional provider integrations behind extras. Router depends only on
  this protocol, not on any concrete provider SDK.
- **Local OpenAI-compatible endpoint** — `POST /v1/chat/completions`
  (+ `/v1/models`), streaming via SSE, so any OpenAI/Anthropic SDK works by
  pointing `base_url` at `localhost`. This satisfies R-10's "OpenAI-compatible
  endpoint" while staying on-device.
- **MCP surface** — tools `router.route(messages, hints?) -> decision` and
  `router.chat(messages, hints?) -> completion`, for agents and for distribution
  to Goose / Gyri (mount the router as an MCP server). Satisfies R-10's "MCP
  surface".
- **Policy DSL** — declarative YAML, e.g.:
  ```yaml
  default_tier: cloud
  rules:
    - if: "group in [PRIVILEGE, PHI, MNPI]"   # any regulated group
      then: { tier: local }
    - if: "score >= 60"
      then: { tier: local }
    - if: "score >= 25"
      then: { tier: cloud, redact: true }     # redact before it leaves
  tiers:
    local:  { backends: [ollama/llama3.1, llamacpp/qwen2.5] }
    cloud:  { backends: [anthropic/claude-sonnet-5, openrouter/*] , failover: ordered }
  ```
  The grammar is small and total; its formal spec is deferred to ADR-0002.
- **Audit decision row** — `{ts, actor, event: "route.decide", score, groups,
  tier, backend, model, redacted, latency_ms, outcome}` appended via
  `ogentic-audit` (HMAC-chained).

## Relationship to `@ogenticai/agent-core` and the primitive set

This is the load-bearing integration decision, and it directly answers "the
runtime must include all our OSS projects."

**The runtime (`@ogenticai/agent-core`) is the single integration point for the
whole trust stack; each `ogentic-*` primitive is the production adapter behind an
agent-core port.** Router is specifically the **Route-port** implementation.

| agent-core port | Primitive (production adapter) | Repo |
|---|---|---|
| DetectRedact | ogentic-shield (detect) + ogentic-redact (mask/unmask) | ogentic-shield, ogentic-redact |
| Route | **ogentic-router** | this repo |
| Audit | ogentic-audit | ogentic-audit |
| Store | ogentic-vault | ogentic-vault |
| LLM provider | via ogentic-adapter-sdk Adapter protocol | ogentic-adapter-sdk |
| (ingest) | ogentic-converter (docs → text before Shield) | ogentic-converter |

**Avoiding double work / single source of truth.** agent-core already runs Shield
inbound. When agent-core calls Router (behind the Route port), it passes the
classification it computed; Router **skips its own `analyze`** and applies policy
+ dispatch + audit only. When Router is used **standalone** (an app swaps its
endpoint, no agent-core), Router runs the full pipeline itself. Either way the
invariant holds and Shield is the single classifier.

**Cross-language boundary.** agent-core is TypeScript; Router is Python. agent-core
reaches Router through Router's **local surfaces** — the localhost
OpenAI-compatible endpoint or the MCP tool — never by embedding Python. This keeps
"no Ogentic infra sees content" true (the surface is on the same device) and needs
zero change to any agent's call site.

**Non-overlap rule.** Router owns *backend selection + privacy enforcement*;
agent-core owns *agent orchestration* (Agent Definition, tiered autonomy/HITL,
tools). Shield and Audit are shared primitives both consume — they are not
re-implemented in either.

## Cross-cutting concerns (NFRs)

- **Privacy:** the fail-closed rule (step 4) is tested explicitly — sensitive
  content can never resolve to a cloud backend, even with a misordered failover.
- **Resilience:** per-backend timeout + bounded retry + ordered failover **within
  a tier only** (never across the sensitivity boundary). A local-tier failure
  does not fall back to cloud.
- **Streaming:** first-class SSE passthrough; redaction is incompatible with
  streaming the raw model output, so the redacted tier buffers-then-rehydrates.
- **Observability (R-6):** every decision emits an OpenTelemetry span (score,
  tier, backend, latency, redacted, outcome) in addition to the audit row.
- **Security:** backend credentials resolve from `ogentic-vault`/env by
  reference; never inlined in `routing.yml`; provider errors are wrapped, never
  leaked raw (matches the build standard).
- **Performance budget:** the local decision path (classify cached + policy eval
  + audit append) targets low single-digit-ms overhead over the raw backend call;
  Shield's own latency dominates and is the tuning target.
- **Slim core:** base deps stay `pydantic / pyyaml / click / rich`; Shield and
  provider SDKs are extras (`pip install ogentic-router[shield,anthropic,local]`).

## Consequences

**Positive**
- The only routing product that decides on sensitivity **before** content leaves
  the device — a real, defensible moat for regulated buyers (Sotto, DrTalk,
  Revere), and the "audit trail is the product" wedge (Trust Pack).
- One contract for every backend (cloud or local); swapping providers or adding
  Groq/Cerebras/vLLM is a config + adapter change, not code.
- Drops behind agent-core's Route port with zero agent change; realizes "the
  runtime includes all OSS projects" via the port table above.
- Distributable via MCP to Goose/Gyri — monetizes the moat beyond our own agents.

**Negative / costs**
- Two pipelines conceptually run Shield (agent-core and Router); mitigated by the
  pass-through-classification rule, but it must be enforced or we double-classify.
- A local OpenAI-compatible server on the device is more moving parts than a
  library-only call; kept optional.
- Sensitivity-aware routing is only as good as Shield's accuracy; false negatives
  route sensitive content outward. Ship conservative thresholds; log everything.

**Neutral**
- We depend on LiteLLM/provider SDKs, but only *behind* the Adapter protocol, so
  they're swappable and don't leak into the core.

## Rollout / Definition of Done

**v0.1 (target 2026-08-31)** — closes R-10:
- [ ] Pluggable Shield classifier (default `ogentic-shield`; injectable classification).
- [ ] Policy engine + `routing.yml` DSL (local / cloud / cloud-redacted tiers).
- [ ] Backend adapters via `ogentic-adapter-sdk`: `local` (Ollama/llama.cpp), `anthropic`, `openai`/OpenAI-compatible.
- [ ] Local `localhost/v1/chat/completions` endpoint-swap server (streaming + unary).
- [ ] MCP surface (`router.route`, `router.chat`).
- [ ] `ogentic-audit` decision logging.
- [ ] **One end-to-end `shield → route → audit` integration test in CI** (the R-10 gate).
- [ ] Fail-closed privacy test (sensitive content never selects a cloud backend).

**Post-v0.1**
- ADR-0002: policy DSL formal grammar + conflict resolution.
- ADR-0003: adapter contract v1 (streaming, tool-calling, usage/cost normalization).
- agent-core Route-port adapter that calls the local endpoint/MCP with pass-through classification.
- Cost/latency-aware selection *within* a tier; canary/weighted routing.

## Open questions

- **Package name parity:** consumed behind agent-core's Route port — confirm the
  RouteDecision→backend mapping shape with agent-core before v0.1.
- **Redaction + streaming:** confirm buffer-then-rehydrate UX is acceptable for
  the redacted tier, or make redacted-tier non-streaming in v0.1.
- **Vault dependency ordering:** `ogentic-vault` is not started; until then,
  credentials resolve from env with a documented migration to Vault.
- **Author/domain:** repo metadata uses `david@ogentic.ai`; align on the
  canonical org domain before publish.
