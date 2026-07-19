# ogentic-router

**Privacy-aware LLM routing.** The routing decision happens on your device,
*before* content leaves. Sensitive content stays on a local model; only cleared
or redacted content crosses the network. Pairs with
[`ogentic-shield`](https://github.com/OgenticAI/ogentic-shield) for
classification and `ogentic-audit` for tamper-evident evidence.

[![PyPI](https://img.shields.io/pypi/v/ogentic-router.svg)](https://pypi.org/project/ogentic-router/)

## Thesis

Every "AI control plane" today ([OpenRouter](https://openrouter.ai),
[Merge Gateway](https://www.merge.dev/gateway), Helicone, Portkey, LiteLLM
proxy) routes on **cost / latency / quality**. None routes on **content
sensitivity**, and the decision is made *after* the content has already left the
machine — which structurally disqualifies them for regulated content (legal
privilege, PHI, MNPI).

`ogentic-router` inverts it: **the routing decision happens on the user's
device, before the content leaves.** Shield classifies → the policy picks a
backend → only the cleared payload crosses the network. It's a **library, not a
hosted service** — there is no Ogentic infrastructure that ever sees your
content. See [How we compare](#how-we-compare) for why that differs from a cloud
gateway's zero-data-retention mode.

## Install

```bash
pip install ogentic-router
```

The base install is slim (just the policy DSL). Pull in what you need via
extras:

| Extra | Adds | Use it for |
|---|---|---|
| `[shield]` | `ogentic-shield` | The default classifier (needed to route on sensitivity). |
| `[cloud]` | `openai`, `anthropic`, `httpx` | The OpenAI / Anthropic adapters. |
| `[local]` | `httpx` | The Ollama / llama.cpp adapters. |
| `[server]` | `fastapi`, `uvicorn` | The OpenAI-shaped local endpoint-swap server. |
| `[mcp]` | `mcp` | Reserved for the MCP tool surface (v0.2 — not yet built). |
| `[audit]` | — | Reserved for the `ogentic-audit` HMAC-chained sink (until that library ships). Local-file audit needs no extra — see [Audit](#audit). |
| `[all]` | all of the above | Everything. |
| `[dev]` | pytest, ruff, mypy, … | Contributing. |

```bash
pip install "ogentic-router[shield,cloud,local,server]"
```

## Get started in 30 seconds

Route one prompt and see the decision — which backend it's allowed to go to, and
why:

```python
from ogentic_router import Policy, Router

policy = Policy.from_yaml("examples/policy.yaml")
router = Router(policy)                       # default classifier: ogentic-shield

decision = router.route("Draft a note about the privileged settlement memo.")
print(decision.backend_id)   # -> ollama-local   (privilege stays on-device)
print(decision.reasoning)    # -> why this rule fired
```

`route()` returns a **decision, not a completion** — it makes the
privacy-relevant choice and stops. You then dispatch to the backend it names
(`decision.backend_id`). Keeping *decide* and *dispatch* separate means the
decision is recordable before any byte is sent. Full runnable version:
[`examples/route_string.py`](examples/route_string.py).

## Drop-in for the OpenAI SDK

Point any existing OpenAI-SDK code at the local router — one line changes:

```bash
pip install "ogentic-router[shield,cloud,server]"
ROUTER_CONFIG=examples/router.yaml ogentic-router serve   # 127.0.0.1:8080
```

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8080/v1",   # <- the only change
    api_key="not-used-by-the-router",       # server holds real keys via ROUTER_CONFIG
)
resp = client.chat.completions.create(
    model="gpt-4o-mini",
    messages=[{"role": "user", "content": "Say hello."}],
)
```

Verify what the router will do by reading the loaded policy at
`GET /v1/policy`. Runnable: [`examples/openai_sdk_swap.py`](examples/openai_sdk_swap.py).

> **v0.1 caveat:** the server dispatches to the policy's `default_backend`; the
> full per-request Shield → policy selection lands in v0.2
> ([OGE-584](https://linear.app/ogenticai/issue/OGE-584)). The **library**
> `Router` makes the full per-prompt decision today. See
> [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Policy DSL

Policies are YAML, first-match-wins. Full reference:
[docs/POLICY_REFERENCE.md](docs/POLICY_REFERENCE.md).

```yaml
version: 1
default_backend: ollama-local
rules:
  - id: privilege-stays-local
    when: { groups_include: [PRIVILEGE, PHI, MNPI] }   # regulated → on-device
    route: ollama-local
  - id: high-sensitivity-stays-local
    when: { sensitivity_score_gte: 70 }
    route: ollama-local
  - id: medium-redact-then-cloud
    when: { sensitivity_score_gte: 30 }
    route: openai-cloud
    transform: shield_redact                            # mask before it leaves
  - id: low-cloud
    when: { sensitivity_score_gte: 0 }
    route: openai-cloud
```

Predicates: `groups_include` / `groups_exclude`, `sensitivity_score_gte` /
`sensitivity_score_lt`, `category_in` / `category_not_in`. The one transform is
`shield_redact`.

## Adapters

Four backends ship. Local adapters are **loopback-only** (enforced in code);
cloud adapters are **host-allowlisted**.

| Backend | Class | Default endpoint / model | Extra | Notes |
|---|---|---|---|---|
| OpenAI | `OpenAIAdapter` | `gpt-4o-mini` | `[cloud]` | Host allowlisted to `api.openai.com`. |
| Anthropic | `AnthropicAdapter` | `claude-sonnet-4-5` | `[cloud]` | Allowlisted to `api.anthropic.com`; defaults `max_tokens=1024`. |
| Ollama | `OllamaAdapter` | `http://localhost:11434`, `llama3.2:3b` | `[local]` | Loopback-only. |
| llama.cpp | `LlamaCppAdapter` | `http://localhost:8080` | `[local]` | Loopback-only; model pinned server-side. |

All implement one async method — `chat(messages, *, model=None, max_tokens=None, temperature=None, stream=False)` — and pass provider-native responses through unchanged.

## Budget ceiling (fail-fast cost control)

`route(..., budget_ceiling=<USD>)` (and `--budget-ceiling` on the CLI) checks the
estimated input-token cost **before** the call leaves the device — no partial
sends.

```python
from ogentic_router import Router, BudgetCeilingExceeded

try:
    decision = router.route(prompt, model="gpt-4o-mini", budget_ceiling=0.001)
except BudgetCeilingExceeded as e:
    print(f"blocked: est ${e.estimated_cost:.6g} > ceiling ${e.ceiling}")
```

`None` = no enforcement; `0.0` = refuse all (dry-run); `> 0` = raise if the
estimate exceeds it. The estimate is input tokens only (~4 chars/token).

## Audit

Every `route()` call emits one **shape-only** decision row — a fingerprint of
the prompt, the sensitivity score, category labels, and the chosen backend,
**never the raw prompt text**. The default sink drops rows; opt into a local
JSON-lines log (no extra needed):

```python
from ogentic_router import Router
from ogentic_router.audit import LocalFileSink

router = Router.from_config({
    "policy_path": "examples/policy.yaml",
    "audit": {"sink": "local_file", "path": "~/.ogentic-router/audit.jsonl"},
})
router.route("Privileged attorney-client memo …")
# audit.jsonl gains one line:
# {"ts": "...", "request_id": "<hmac>", "prompt_hash": "sha256:…",
#  "sensitivity_score": 87, "route_decision": "ollama-local",
#  "rule_id": "privilege-stays-local", "backend_is_local": true, "error": null, ...}
```

Rows are emitted on **error paths too** (`error` carries the exception class name
only). Emission is fire-and-forget — a full or misconfigured log never crashes
the router. Set `OGENTIC_ROUTER_AUDIT_SALT` for reproducible `request_id`s across
restarts. The `OgenticAuditSink` (HMAC-chained, tamper-evident) lights up once
`ogentic-audit` publishes. Replay a log with
[`examples/audit_replay.py`](examples/audit_replay.py).

## Privacy invariants

The load-bearing guarantees (full version:
[docs/PRIVACY_POSTURE.md](docs/PRIVACY_POSTURE.md),
[CONTRIBUTING.md](CONTRIBUTING.md)):

- **In-process, on your machine.** Classification, policy, and redaction are
  library calls, not network calls. No Ogentic infrastructure sees your content.
- **Decision before dispatch.** The `RouteDecision` exists before any content is
  sent.
- **Local means local.** Local adapters reject any non-loopback host.
- **Fail-closed.** Classifier/policy failures refuse the call; sensitive content
  can never resolve to a cloud backend against the policy.
- **Shape-only audit.** Decision records carry hashes and labels, never raw
  prompt text — one row per `route()`, error paths included. See [Audit](#audit).

## How we compare

`ogentic-router` is **not** a replacement for a SaaS gateway if your content
isn't regulated and you route on cost/latency — the incumbents are excellent at
that. It solves a different problem. Full breakdown:
[docs/COMPARISON.md](docs/COMPARISON.md) · privacy officer's one-pager:
[docs/PRIVACY_POSTURE.md](docs/PRIVACY_POSTURE.md).

*Competitor data as of 2026-06-03.*

| | OpenRouter / Merge / Portkey | ogentic-router |
|---|---|---|
| Form factor | SaaS proxy | Local library |
| Routing axis | Cost / latency / quality | **Sensitivity** first, then cost |
| Where the decision runs | Vendor servers | **Your process** |
| Sensitive-content path | Transits vendor + provider | **Stays on-device (local backend)** |
| Audit | Vendor dashboard | **Shape-only, user-held** |
| Trust model | Trust the vendor | **Open source — verify it** |

### Zero-data-retention is not local-first

OpenRouter shipped a **zero-data-retention (ZDR)** toggle in May 2026. ZDR is a
real control, but it answers a different question:

**"Deleted after we processed it" ≠ "never left the device."**

| | ZDR (e.g. OpenRouter) | Local-first (ogentic-router) |
|---|---|---|
| Sensitive content leaves the device? | **Yes**, then deleted | **No** (kept local) |
| Guarantee type | Retention promise | Transit guarantee |
| Who can be compelled to produce it? | The vendor, while it holds it | No third party ever holds it |
| Verified how? | Vendor attestation | Runs in your process; open source |

OpenRouter is the category leader — **400+ models, 60+ providers, 250k+ apps,
4.2M+ users, $113M Series B led by CapitalG** (as of 2026-06-03). We don't
compete on breadth; we answer the regulated-content question they structurally
can't. Sourcing and per-product notes: [docs/COMPARISON.md](docs/COMPARISON.md).

## Examples & docs

- [`examples/route_string.py`](examples/route_string.py) — route one prompt, print the decision
- [`examples/openai_sdk_swap.py`](examples/openai_sdk_swap.py) — OpenAI SDK endpoint swap
- [`examples/audit_replay.py`](examples/audit_replay.py) — replay decisions from JSON-lines
- [`examples/policy.yaml`](examples/policy.yaml) / [`examples/router.yaml`](examples/router.yaml) — canonical config
- [`examples/sotto_desktop_config.toml`](examples/sotto_desktop_config.toml) — Sotto embed config
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) · [docs/POLICY_REFERENCE.md](docs/POLICY_REFERENCE.md) · [docs/COMPARISON.md](docs/COMPARISON.md) · [docs/PRIVACY_POSTURE.md](docs/PRIVACY_POSTURE.md) · [docs/SOTTO_INTEGRATION.md](docs/SOTTO_INTEGRATION.md) · [ADR-0001](docs/adr/0001-router-architecture.md)

## What's next (v0.2)

Per-request Shield pipeline in the server, the `OgenticAuditSink` (HMAC-chained,
once `ogentic-audit` ships), the MCP tool surface
([OGE-586](https://linear.app/ogenticai/issue/OGE-586)), and a drop-in proxy
demo. Track the [Linear project](https://linear.app/ogenticai/project/ogentic-router-oss-46e612b52d27).

## License

Apache-2.0. See [LICENSE](LICENSE). Security: [SECURITY.md](SECURITY.md).

## Ecosystem

| Project | Purpose | Status |
|---|---|---|
| [`ogentic-shield`](https://github.com/OgenticAI/ogentic-shield) | Privilege / PHI / MNPI detection | Published |
| `ogentic-audit` | HMAC-chained audit log | In flight |
| **`ogentic-router`** | Privacy-aware routing | **v0.1.0 (this repo)** |
| `sotto-desktop` | Privilege-protected desktop AI | v1 in flight |
