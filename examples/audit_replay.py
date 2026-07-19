#!/usr/bin/env python3
"""Replay routing decisions from a JSON-lines file.

Useful for post-incident review: read back the sequence of routing decisions
and see, for each prompt, which backend it went to and why.

    pip install -e ".[shield]"
    python examples/audit_replay.py

The Router emits these rows for you when configured with a ``LocalFileSink``
(``audit: {sink: local_file, path: ...}``) — one shape-only row per ``route()``
call. This example is self-contained: it makes real decisions and writes them as
JSON-lines exactly as ``LocalFileSink`` does, then replays that file. Point the
replay at your real ``audit.jsonl`` to review a production log the same way.

The ``RouteDecisionAudit`` row shape (one JSON object per line) includes
``ts``, ``request_id``, ``prompt_hash``, ``sensitivity_score``, ``route_decision``,
``rule_id``, ``transform``, ``backend_is_local``, ``error``, and more — shape-only,
never the raw prompt.

Note: the HMAC-*chained*, tamper-evident ``OgenticAuditSink`` lights up once
``ogentic-audit`` publishes to PyPI; ``LocalFileSink`` is the v0.1 sink.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from ogentic_router import Policy, Router
from ogentic_router.audit import LocalFileSink

POLICY_PATH = Path(__file__).parent / "policy.yaml"


def write_sample_log(path: Path) -> None:
    """Make real decisions with a LocalFileSink — exactly as production would."""
    router = Router(Policy.from_yaml(POLICY_PATH), audit_sink=LocalFileSink(path))
    prompts = [
        "What time is the standup?",
        "Summarize the sealed deposition transcript for the privileged file.",
        "Redraft the earnings note before the numbers are public.",
    ]
    for prompt in prompts:
        router.route(prompt)  # the sink appends one RouteDecisionAudit row


def replay(path: Path) -> None:
    """Read the JSON-lines audit file back and print each decision."""
    with path.open(encoding="utf-8") as fh:
        for i, line in enumerate(fh, start=1):
            row = json.loads(line)
            transform = row.get("transform") or "(none)"
            local = {True: "local", False: "cloud", None: "?"}[row.get("backend_is_local")]
            print(
                f"[{i}] backend={row['route_decision'] or '(default)':<14} "
                f"({local})  rule={row['rule_id'] or '(default)':<28} "
                f"transform={transform}"
            )
            print(
                f"     score={row['sensitivity_score']} "
                f"groups={row['groups_found']} hash={row['prompt_hash']} "
                f"error={row['error']}"
            )


def main() -> None:
    with tempfile.TemporaryDirectory() as d:
        log = Path(d) / "decisions.jsonl"
        write_sample_log(log)
        print(f"wrote {log.name}, replaying:\n")
        replay(log)


if __name__ == "__main__":
    main()
