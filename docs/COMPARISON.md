# How ogentic-router compares

> **Data current as of 2026-06-03.** Competitor scale and feature claims below
> are from that date's landscape scan and are not re-verified on every release.
> Where a figure can't be sourced it's dropped rather than guessed. See
> [Sourcing](#sourcing) at the bottom.

`ogentic-router` is **not** a drop-in replacement for a SaaS AI gateway for most
teams. If you route on cost, latency, or model breadth and your content isn't
regulated, the incumbents below are excellent and you should probably use one.
This document exists to help a buyer **self-qualify**: it answers "is my problem
the one ogentic-router solves, or a different one?"

The one question that separates them: **does the routing decision happen before
the content leaves the device, or after?**

## Architecture at a glance

| | OpenRouter | Merge Gateway | Helicone / Portkey | LiteLLM proxy | **ogentic-router** |
|---|---|---|---|---|---|
| Form factor | SaaS proxy | SaaS proxy | SaaS proxy / gateway | Self-host proxy or library | **Local library** (+ optional local server) |
| Routing axis | Cost / latency / model | Unified API / cost | Observability / cost / fallback | Cost / provider abstraction | **Content sensitivity** first, then cost |
| Where the decision runs | Vendor servers | Vendor servers | Vendor / your gateway | Your proxy host | **The caller's process** |
| Sensitive-content path | Transits vendor + provider | Transits vendor + provider | Transits gateway + provider | Transits proxy + provider | **Can stay on-device (local backend); never transits Ogentic** |
| Audit of the decision | Vendor dashboard | Vendor dashboard | Vendor dashboard | Your logs | **Shape-only, HMAC-chained, user-held** (v0.2) |
| Local LLMs | Not first-class | No | Via provider config | Yes (as a provider) | **First-class, loopback-enforced** |
| Trust model | Trust the vendor's ZDR claim | Trust the vendor | Trust the gateway | Trust your own deploy | **Open source — verify the claim** |

## ZDR vs. local-first — the load-bearing distinction

OpenRouter shipped "Guardrails" in May 2026, including a **zero-data-retention
(ZDR) toggle**, prompt-injection defense, and provider allowlisting. ZDR is a
real and valuable control. But it answers a different question than local-first,
and the difference is exactly the one a regulated buyer cares about:

**"Deleted after we processed it" ≠ "never left the device."**

| | Zero-Data-Retention (e.g. OpenRouter ZDR) | Local-first (ogentic-router) |
|---|---|---|
| Does sensitive content leave the device? | **Yes** — it transits the vendor to be processed | **No** — for content the policy keeps local |
| What's the privacy guarantee? | A **retention** promise: not stored after processing | A **transit** guarantee: it was never sent |
| Who can be compelled to produce it? | The vendor, in the window it holds it | No third party ever holds it |
| How is it verified? | Vendor attestation / contract | The decision runs in your process; open source |
| Failure mode | A retention bug or subpoena exposes content that was sent | Nothing to expose — it never left |

For legal privilege, PHI, and MNPI, the distinction is not academic: privilege
can be waived by disclosure to a third party regardless of retention policy, and
"we deleted it" is not the same defense as "it was never disclosed." That is the
gap ogentic-router is built to close.

## Acknowledging the category leader

OpenRouter is the category leader by a wide margin, and this document is not a
claim otherwise. As of 2026-06-03 it offered **400+ models across 60+
providers**, served **250k+ apps** and **4.2M+ users**, and had raised a **$113M
Series B led by CapitalG** (announced May 2026). With that runway it will keep
extending into the privacy lane — which is precisely why naming the architectural
distinction explicitly matters now: without it, a buyer comparing the two on a
feature checklist will conclude they're equivalent. They aren't. They answer
different questions.

## Per-product notes

### OpenRouter
Unified access to hundreds of models behind one API, routed on price/latency/
availability, plus the May-2026 Guardrails (ZDR, injection defense, provider
allowlisting). **Pick OpenRouter when** you want maximum model breadth and
cost/latency routing and your content isn't regulated. **We differ** on where the
decision runs and whether sensitive content transits a third party at all.

### Merge Gateway
A unified LLM API/gateway in the integration-platform tradition — one interface,
many providers, centralized cost and key management. **Pick Merge when** you want
a managed unified API across providers. **We differ** as a local library with a
sensitivity-first decision, not a hosted unification layer.

### Helicone / Portkey
Observability-first gateways: logging, tracing, caching, fallbacks, cost
analytics, sitting in front of provider calls. **Pick these when** your priority
is visibility and reliability of cloud LLM traffic. **We differ** in purpose —
they observe the traffic that leaves; we decide what's allowed to leave.

### LiteLLM proxy
An excellent multi-provider abstraction, self-hostable, that normalizes many
providers behind an OpenAI-shaped interface and routes on cost/availability. It's
close enough in spirit that we **reuse it as a backend adapter pattern** rather
than compete with it. **Pick LiteLLM when** you want provider abstraction and
self-hosted control without a sensitivity policy. **We differ** by adding the
on-device classification + policy + audit spine; LiteLLM is the dispatch layer,
not the decision layer.

## When you should NOT use ogentic-router

- Your content isn't regulated and you route purely on cost/latency/model — an
  incumbent gateway will serve you better.
- You want a hosted service with a dashboard and SLAs — this is a library.
- You need breadth across hundreds of models today — we ship four adapters
  (OpenAI, Anthropic, Ollama, llama.cpp).

## Sourcing

- OpenRouter product scale (model/provider/app/user counts) and the Guardrails
  feature set: OpenRouter, <https://openrouter.ai> and
  <https://openrouter.ai/models>, as observed 2026-06-03.
- OpenRouter Series B ($113M, led by CapitalG): reported May 2026. **Open item:**
  the exact announcement/press URL should be attached before this doc is cited
  externally; the figure is retained here because it appears in the source ticket
  (OGE-587) but is flagged pending a durable link.
- Merge Gateway: <https://www.merge.dev/gateway>.
- The privilege-waiver-by-disclosure framing tracks the regulated-content thesis
  in the OgenticAI vision doc; see [ADR-0001](adr/0001-router-architecture.md)
  for the on-device rationale.
