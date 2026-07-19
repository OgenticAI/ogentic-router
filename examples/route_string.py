#!/usr/bin/env python3
"""Minimal: load a policy, route one prompt, print the decision.

This is the smallest end-to-end use of the library. It classifies the prompt
with Shield, evaluates the policy, and prints the resulting RouteDecision —
which backend the content is *allowed* to go to, and why.

Run:
    pip install -e ".[shield]"          # Shield is the default classifier
    python examples/route_string.py

Note: ``Router.route()`` returns a *decision*, not a completion. It never
dispatches the call itself — you pick the adapter named by
``decision.backend_id`` and invoke it (see examples/openai_sdk_swap.py, or the
server, for the dispatch half). Keeping the decision and the dispatch separate
is deliberate: the privacy-relevant choice is made and auditable before a
single byte is sent anywhere.
"""

from __future__ import annotations

from pathlib import Path

from ogentic_router import Policy, Router

POLICY_PATH = Path(__file__).parent / "policy.yaml"


def main() -> None:
    policy = Policy.from_yaml(POLICY_PATH)
    # shield=None lets the Router lazily construct the default ogentic-shield
    # classifier on first use. Pass your own object (anything with
    # ``analyze(text) -> AnalysisResult``) to swap the classifier.
    router = Router(policy)

    prompts = [
        "What's the weather in Lagos today?",
        "Draft a note to opposing counsel about the privileged settlement memo.",
    ]

    for prompt in prompts:
        decision = router.route(prompt)
        print(f"\nprompt:    {prompt!r}")
        print(f"backend:   {decision.backend_id}")
        print(f"rule:      {decision.rule_id or '(default_backend)'}")
        print(f"transform: {decision.transform.value if decision.transform else '(none)'}")
        print(f"why:       {decision.reasoning}")


if __name__ == "__main__":
    main()
