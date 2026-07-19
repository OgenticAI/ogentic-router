#!/usr/bin/env python3
"""Drop-in for the OpenAI SDK: point base_url at the local router.

Any code that already talks to the OpenAI SDK routes through the privacy layer
by changing one line — the ``base_url``. No other code changes.

Start the server first (separate terminal):

    pip install -e ".[shield,cloud,server]"
    ROUTER_CONFIG=examples/router.yaml ogentic-router serve
    # -> Uvicorn running on http://127.0.0.1:8080

Then run this script:

    pip install openai
    python examples/openai_sdk_swap.py

What changed vs. talking to OpenAI directly:

    client = OpenAI(
        base_url="http://127.0.0.1:8080/v1",   # <- the only change
        api_key="not-used-by-the-router",       # server holds real keys via ROUTER_CONFIG
    )

Verifying the routing decision: the server surfaces the loaded policy at
GET /v1/policy, and (in v0.2) each response will carry the decision id for
lookup at GET /v1/decision/{id}. Today that endpoint returns a
"pending ogentic-audit integration" stub — see docs/ARCHITECTURE.md.

v0.1 caveat: the server dispatches to the policy's `default_backend`; the full
per-request Shield -> policy selection lands in v0.2 (OGE-584). The library
Router (examples/route_string.py) already makes the full decision today.
"""

from __future__ import annotations

import sys
import urllib.request

SERVER = "http://127.0.0.1:8080"


def _server_up() -> bool:
    try:
        with urllib.request.urlopen(f"{SERVER}/healthz", timeout=1) as r:  # noqa: S310
            return r.status == 200
    except Exception:
        return False


def main() -> None:
    if not _server_up():
        print(f"No router server at {SERVER}. Start one with:")
        print("  ROUTER_CONFIG=examples/router.yaml ogentic-router serve")
        sys.exit(1)

    try:
        from openai import OpenAI
    except ImportError:
        print("pip install openai to run this example.")
        sys.exit(1)

    client = OpenAI(base_url=f"{SERVER}/v1", api_key="not-used-by-the-router")

    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": "Say hello in five words."}],
    )
    print(resp.choices[0].message.content)


if __name__ == "__main__":
    main()
