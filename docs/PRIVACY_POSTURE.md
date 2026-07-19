# Privacy posture

*A single page for a general counsel or privacy officer evaluating
ogentic-router. It answers one question on its own: "how is this actually
different from a cloud gateway's zero-data-retention mode?"*

## The claim, in one sentence

For content your policy marks sensitive, ogentic-router makes the routing
decision **in your own process, before anything is sent**, and keeps that
content on a **local** model — so it never transits Ogentic or an unapproved
third party at all.

## Why that differs from zero-data-retention (ZDR)

A cloud gateway's ZDR mode is a **retention** promise: your content is sent to
the vendor, processed, and not stored afterward. ogentic-router gives a
**transit** guarantee: for content the policy keeps local, it was never sent.

**"Deleted after we processed it" ≠ "never left the device."**

For attorney–client privilege, PHI, and MNPI this is the material distinction —
privilege can be waived by disclosure to a third party independent of whether
that party retained the data, and "it was never disclosed" is a stronger posture
than "it was disclosed and then deleted." The long-form comparison is in
[COMPARISON.md](COMPARISON.md).

## The concrete guarantees

1. **In-process execution.** Classification, policy evaluation, and (when
   configured) redaction run inside the calling application's process. They are
   library calls, not network calls.

2. **No Ogentic-hosted infrastructure.** There is no Ogentic server in the path.
   Ogentic operates nothing that can see your content. The project is a library
   you install and run; you can read the source and verify this.

3. **Decision before dispatch.** The router produces a `RouteDecision` — which
   backend, and why — *before* any content is dispatched. The privacy-relevant
   choice is made and recordable ahead of transmission, not inferred after.

4. **Local backends are loopback-only.** The Ollama and llama.cpp adapters
   accept only `localhost` / `127.0.0.1` / `::1` endpoints; a non-loopback host
   raises `LocalhostOnlyError` before any client is even constructed. "Local"
   means on-box, enforced in code, not by convention.

5. **Cloud backends are allowlisted.** Cloud adapters accept only their
   provider's canonical host (`api.openai.com`, `api.anthropic.com`) unless an
   operator explicitly extends the allowlist via environment variable. A
   caller-supplied URL cannot redirect a cloud call to an arbitrary endpoint.

6. **Shape-only audit.** The decision record carries the sensitivity score,
   category labels, chosen backend, whether redaction was applied, and a
   `sha256:`-prefixed hash of the input — **never the raw prompt text**. The
   audit answers "what was decided" without itself becoming a copy of the
   sensitive content. (Automatic emission to the HMAC-chained `ogentic-audit`
   log ships in v0.2; the record shape is stable today via
   `RouteDecision.to_dict()`.)

7. **Fail-closed.** If the classifier is unavailable the router raises rather
   than silently proceeding, and the policy engine refuses to emit a cloud
   backend for content the rules keep local — a misordered failover cannot send
   sensitive content outward.

## What this does *not* claim

- It does not defend against an adversary with live access to the running
  process, host memory, or the on-device model weights.
- It is only as good as the classifier's accuracy — a false negative can route
  content the user would have considered sensitive. Thresholds ship conservative;
  the decision log lets you review what happened.
- Cloud backends you configure are outside our control — once cleared/redacted
  content is dispatched to OpenAI or Anthropic, their terms govern it.

See [SECURITY.md](../SECURITY.md) for the responsible-disclosure process and the
full threat model.
