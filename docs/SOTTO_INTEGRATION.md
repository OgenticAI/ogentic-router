# Sotto Desktop integration guide

> **Review status:** this guide needs sign-off from whoever leads Sotto Desktop
> v1 before it's authoritative. It's written from the router's as-shipped
> surface; the Sotto-side embed points are marked where they depend on Sotto
> internals that the Sotto team should confirm.

Sotto Desktop is the primary downstream consumer of ogentic-router. Sotto
orchestrates the on-device LLM-call lifecycle: it classifies with Shield,
applies the routing policy, redacts if the target is cloud and the content is
sensitive, picks an adapter, and records the decision. In the Sotto codebase
this lives in `src-tauri/src/router.rs`.

## The embed model

Sotto is a Tauri app (Rust shell). ogentic-router is a Python library. Sotto
embeds the router in-process and drives it through the router's public surface.
Sotto's own configuration is TOML
([`examples/sotto_desktop_config.toml`](../examples/sotto_desktop_config.toml));
the router's configuration is YAML
([`examples/router.yaml`](../examples/router.yaml)). The TOML points at the YAML.

```
┌────────────────────────────────────────────┐
│ Sotto Desktop (Tauri / Rust shell)         │
│  src-tauri/src/router.rs                    │
│  reads sotto_desktop_config.toml            │
└───────────────┬────────────────────────────┘
                │ router_config = resources/router.yaml
                ▼
┌────────────────────────────────────────────┐
│ ogentic-router (embedded, in-process)      │
│  1. Shield.analyze   2. Policy.evaluate     │
│  3. Shield.redact?   4. Adapter.chat        │
└───────────────┬────────────────────────────┘
                ▼
      local (Ollama)  |  cloud (cleared/redacted)
```

## Configuration surface

Sotto ships two files inside the app bundle:

1. **`router.yaml`** (RouterConfig) — the router's real config. Bind Sotto's
   packaged path to the `ROUTER_CONFIG` environment variable, or pass it
   explicitly. Fields: `version: 1`, `policy_path`, an optional `shield` block
   (`profiles`, `config`), and `backends[]` (each `id`, `kind`, optional
   `api_key_env`, `base_url`, `default_model`). See
   [POLICY_REFERENCE.md](POLICY_REFERENCE.md) for the policy file it points at.

2. **`policy.yaml`** — the routing rules. Sotto's shipped default should keep
   privilege / PHI / MNPI and high-sensitivity content local; the canonical
   [`examples/policy.yaml`](../examples/policy.yaml) is a good starting point.

Keys are never stored in these files. The router reads each backend's key from
the environment variable named by `api_key_env` at dispatch time.

## Lifecycle, per call

1. **Classify** — `Router.classify(prompt)` (or `route` does it inline) →
   `ShieldClassification` (`score`, `category_groups_found`, `top_category`,
   `entity_count`, `text_hash`). Runs in-process; nothing leaves the machine.
2. **Decide** — `Router.route(prompt)` → `RouteDecision` (`backend_id`,
   `rule_id`, `transform`, `reasoning`). This is the point Sotto records for
   audit.
3. **Redact (optional)** — if `decision.transform == shield_redact`, apply
   `Shield.redact()` before dispatch.
4. **Dispatch** — call the adapter named by `decision.backend_id`
   (`Adapter.chat(messages, ...)`). Local adapters are loopback-enforced; cloud
   adapters are host-allowlisted.

> **Sotto-team confirm:** whether Sotto calls the Python library directly (via a
> Python sidecar / PyO3) or stands up the router's local server
> (`ogentic-router serve`, `127.0.0.1:8080`) and talks OpenAI-wire to it. Both
> are supported; the guide assumes the in-process library path. If Sotto uses the
> server, note the v0.1 server caveat below.

## Audit log destination

Sotto owns where decision records land. Point `audit_log` in the TOML at a
per-user path (e.g. `~/Library/Application Support/Sotto/router-decisions.jsonl`).
Each record is `RouteDecision.to_dict()` — shape-only, no raw prompt text; see
[PRIVACY_POSTURE.md](PRIVACY_POSTURE.md). Until ogentic-audit emission ships
(v0.2, OGE-584), Sotto's wrapper appends these rows itself; afterward the router
emits to the HMAC-chained `ogentic-audit` log and Sotto points at that.
[`examples/audit_replay.py`](../examples/audit_replay.py) shows the replay path.

## v0.1 caveats Sotto should know

- **If Sotto uses the embedded library** (`Router.route`), it gets the full
  per-prompt Shield → policy decision today. This is the recommended path.
- **If Sotto uses the local server**, note that in v0.1 the server dispatches to
  the policy's `default_backend` and does not yet run the per-request Shield
  pipeline (v0.2, OGE-584). For sensitive-content correctness before v0.2, drive
  the library directly.
- **Audit auto-emission and the MCP tool surface are v0.2** — don't wire Sotto
  to `GET /v1/decision/{id}` (a stub today) or an MCP endpoint (unbuilt) yet.
