# Security Policy

`ogentic-router` is sold on a single invariant: **sensitive content does not leave the device**. Classification, policy evaluation, redaction, and audit all run in-process on the caller's machine, and the routing engine is fail-closed — content above the policy threshold can never resolve to a network backend. See [ADR-0001](docs/adr/0001-router-architecture.md) for the full model.

Any finding that breaks that invariant is priority-zero work. So is anything that lets a caller believe content stayed local when it did not.

## Reporting a vulnerability

**Do not open a public issue or pull request for security findings.**

Email **security@ogenticai.com** with:

- A description of the issue and the affected component (policy engine, classifier port, redactor, adapter registry, local HTTP endpoint, MCP surface, CLI, config loading).
- The version (commit SHA or release tag) you tested against.
- Reproduction steps, ideally including a minimal `routing.yml` and a PoC request.
- Your assessment of impact — in particular whether it causes **sensitive content to reach a network backend**, or causes an audit record to be missing, wrong, or forgeable.
- Whether you would like public credit when the fix ships.

We will acknowledge receipt within **3 business days** and aim to provide an initial triage within **7 business days**. Coordinated-disclosure timelines are typically **90 days** from the date of acknowledgement, shorter if the issue is being actively exploited and longer by mutual agreement when a fix requires a policy-format change.

## Scope

In scope:

- The Python package under `src/ogentic_router/` — policy engine, adapters, server, CLI.
- The `routing.yml` policy format and its loader, including any parse path that could silently widen a rule.
- The local OpenAI-compatible endpoint and the MCP surface under `src/ogentic_router/server/`.
- Credential handling — anything that causes a backend key to be logged, echoed in an error, or written to an audit row.

Findings we consider especially severe:

- **Privacy-invariant bypass** — any input, config, or failover path that routes above-threshold content to a non-local backend.
- **Fail-open on error** — a classifier, policy, or audit failure that results in the request proceeding rather than being refused.
- **Redaction leakage** — the redacted tier transmitting unredacted text, or the rehydration mapping crossing the network.
- **Audit gaps** — a routing decision that dispatches without a corresponding audit record, or a record that misstates the tier, backend, or redaction actually applied.

Out of scope at v0.1:

- Adversaries with live access to the running process, its memory, or the on-device model weights.
- The accuracy of the underlying classifier itself — `ogentic-shield` false negatives are a tuning issue, tracked in that repo, not a router vulnerability. A *systematic* way to force misclassification through router's own input handling **is** in scope.
- The security of third-party backends you configure (Anthropic, OpenAI, OpenRouter, a local Ollama you expose to your network).
- Binding the local endpoint to a non-loopback interface on purpose. It defaults to localhost; overriding that is a deployment decision, not a router defect.

A finding outside this scope is still welcome — we may not classify it as a vulnerability, but we will document it and credit you if you'd like.

## Supported versions

`ogentic-router` is pre-1.0.

| Version | Supported |
|---------|-----------|
| `0.1.x` | Yes — security fixes on the latest `0.1.x` |
| `< 0.1`  | No |

Note that the published `0.1.0` package is release plumbing and early surface; the functional v0.1 described in [ADR-0001](docs/adr/0001-router-architecture.md)'s Definition of Done has not shipped yet. Until it does, `main` is the reference for what the invariant actually enforces.

## PGP

A PGP key for `security@ogenticai.com` will be published alongside the functional v0.1 release. Until then, plain email is acceptable; if you require encrypted communication before then, mention it in your initial mail and we will coordinate a key out of band.
