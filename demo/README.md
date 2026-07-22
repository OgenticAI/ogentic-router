# ogentic-router — live demo

A small Streamlit app that shows the router in action: type a prompt, see how
Shield scores its sensitivity and which backend the policy would route it to —
**on-device, before any content leaves.** No LLM is called; the demo shows the
*decision*, which is the whole point.

Mirrors [`ogentic-shield`'s demo](https://huggingface.co/spaces/ogenticai/shield-demo)
in spirit, but hosted on **Railway** (OgenticAI's standard host for services) —
the Presidio + spaCy image is heavy, and Railway schedules it predictably where a
free HF Space tier does not.

## Run locally

```bash
pip install -e ".[shield]" streamlit      # from the repo root
streamlit run demo/app.py                 # http://localhost:8501
```

First load pays the Shield/Presidio cold-start (a few seconds); it's cached after.

## What it shows

Five built-in samples, one per routing outcome:

| Prompt | Shield | Routes to | Why |
|---|---|---|---|
| Attorney work product | `PRIVILEGE` | llama.cpp (local) | privilege stays on-device |
| Patient record | `PHI` | Ollama (local) | health data stays on-device |
| Insider / MNPI note | `MNPI` | Ollama (local) | material non-public info stays on-device |
| Personal reminder (name + email + phone) | `PII`, score ~28 | Anthropic (cloud), **redacted** | moderate → redact then cloud |
| Travel question | none, score 0 | OpenAI (cloud) | low sensitivity → cloud in the clear |

The routing rules live in [`demo/policy.yaml`](policy.yaml); the backends in
[`demo/router.yaml`](router.yaml). Edit the YAML to change behavior — no code.

## Deploy to Railway

The Dockerfile builds from the **repo root** (it installs the router from source,
because the demo uses `main`'s API, which the published `0.1.0` wheel predates).

Dashboard: **New Project → Deploy from GitHub repo** → `OgenticAI/ogentic-router`,
then **Settings**:
- **Root Directory** = repo root (leave blank / `/`)
- **Config-as-code** = `demo/railway.json` (sets the Dockerfile path + Streamlit start command + `/_stcore/health` healthcheck)

Or from the CLI at the repo root:

```bash
railway up --detach
```

### Two gotchas (both cost an hour on a first deploy)

1. **Bind `0.0.0.0`, not `::`.** Railway's edge connects over IPv4; a `--server.address ::`
   bind is IPv6-only on `python:*-slim` and the edge gets a 502 *"Application failed
   to respond"* even though the app is up. The start command here uses `0.0.0.0` — keep it.
2. **Give it a generous healthcheck timeout.** The `en_core_web_lg` download + Presidio
   warmup makes first boot slow; `healthcheckTimeout: 300` in `railway.json` covers it.

## Notes

- **No keys, no dispatch.** The demo only makes the routing *decision* — it never
  calls a backend — so it needs no API keys and sends nothing anywhere.
- **When a router release ships** `router.backends` / budget (currently `main`-only),
  the Dockerfile can switch from source-install to a pinned wheel.
