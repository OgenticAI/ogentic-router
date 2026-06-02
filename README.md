# ogentic-router

**Privacy-aware LLM routing.** Sensitive content stays local; redacted content may go to cloud. Pairs with [`ogentic-shield`](https://github.com/OgenticAI/ogentic-shield) for classification and `ogentic-audit` (coming) for tamper-evident evidence.

> ⚠️ **Pre-alpha.** The repo is scaffolded; v0.1 build is in flight. Track progress on the [Linear project](https://linear.app/ogenticai/project/ogentic-router-oss-46e612b52d27).

## Thesis

Every "AI control plane" today ([Merge Gateway](https://www.merge.dev/gateway), OpenRouter, Helicone, Portkey, LiteLLM proxy) routes on **cost / latency / quality**. None routes on **content sensitivity**. Result: the routing decision is made *after* the content has already left the user's machine — which structurally disqualifies these products for regulated content (legal privilege, PHI, MNPI).

`ogentic-router` inverts the architecture: **the routing decision happens on the user's device, before the content leaves**. The Shield classifier decides sensitivity → the router picks a backend → only the cleared payload crosses the network. Pairs with `ogentic-audit` to produce a court-defensible record of every routing decision.

This is the v0.1 promise. The library, not the service.

## Architecture

```
┌──────────────────────────┐
│  Application code        │   uses standard OpenAI / Anthropic SDK
└──────────────┬───────────┘
               │ endpoint swap (drop-in)
               ▼
┌──────────────────────────┐
│  ogentic-router (local)  │
│  1. Shield.analyze       │  classify input
│  2. Policy engine        │  sensitivity + intent → backend choice
│  3. Adapter              │  call the picked backend
│  4. Audit emit           │  ogentic-audit decision row
└──────────────┬───────────┘
               │
   ┌───────────┼─────────────────┐
   ▼           ▼                 ▼
Local LLM   Cloud (cleared)   Cloud (redacted)
```

**Key invariant:** Shield + Policy + Audit run **in-process on the user's machine**. The router is a library, not a hosted service. There is no Ogentic infrastructure that ever sees user content.

## How it differs from SaaS gateways

| Dimension | OpenRouter / Merge Gateway / Portkey | ogentic-router |
|---|---|---|
| Architecture | SaaS reverse proxy | Local library |
| Routing axis | Cost / latency / quality | **Sensitivity** + cost / latency |
| Where the routing decision runs | Vendor servers | User's machine |
| Sensitive content path | Transits vendor + chosen provider | Never leaves user's device (local backend) |
| Audit log | Vendor-controlled | HMAC-chained, user-controlled, court-defensible |
| Local LLM support | Not first-class | First-class (llama.cpp / Ollama / MLX) |
| Trust model | Trust the vendor | Open source — verify the privacy claim |

## v0.1 scope

- Pluggable Shield classifier (default: `ogentic-shield`).
- Pluggable backend adapters: OpenAI, Anthropic, Ollama, llama.cpp.
- Declarative policy: `if sensitivity ≥ N OR group ∈ {PRIVILEGE, PHI, MNPI} → local; else → cloud (with optional Shield.redact prefilter)`.
- Endpoint-swap server: `localhost:NNNN/v1/chat/completions`, drop-in for existing OpenAI/Anthropic SDKs.
- Decision logging into `ogentic-audit`.
- Streaming + non-streaming.
- Python library + CLI + MCP tool surface.

## Install (pre-alpha)

```bash
pip install ogentic-router  # not yet published; coming with v0.1
```

For now, install from source:

```bash
git clone https://github.com/OgenticAI/ogentic-router
cd ogentic-router
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"
```

## Status

v0.1 milestone target: **2026-08-31**. See the [Linear project](https://linear.app/ogenticai/project/ogentic-router-oss-46e612b52d27) for tickets and progress.

## License

Apache-2.0. See [LICENSE](LICENSE).

## Ecosystem

| Project | Purpose | Status |
|---|---|---|
| [`ogentic-shield`](https://github.com/OgenticAI/ogentic-shield) | Privilege / PHI / MNPI detection | v0.2 shipped |
| `ogentic-audit` | HMAC-chained audit log | In flight |
| **`ogentic-router`** | Privacy-aware routing | **Pre-alpha (this repo)** |
| `sotto-desktop` | Privilege-protected desktop AI for regulated pros | v1 in flight |
