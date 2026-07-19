#!/usr/bin/env python3
"""Replay routing decisions from a JSON-lines file.

Useful for post-incident review: read back the sequence of routing decisions
and see, for each prompt, which backend it went to and why.

    pip install -e ".[shield]"
    python examples/audit_replay.py

Honest scope note: automatic emission of decisions to ``ogentic-audit`` is a
v0.2 deliverable (OGE-584) — the Router does not yet write an audit log on its
own. What IS shipped today is the decision record itself: ``RouteDecision``
serializes to a stable dict via ``.to_dict()``. This example demonstrates the
replay contract end-to-end by (1) making real decisions, (2) writing them as
JSON-lines exactly as the audit sink will, and (3) replaying that file. When
audit emission lands, step 1/2 move into the Router and only step 3 remains.

The replayed record shape (one JSON object per line):
    {"backend_id": ..., "rule_id": ..., "transform": ..., "reasoning": ...}
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from ogentic_router import Policy, Router

POLICY_PATH = Path(__file__).parent / "policy.yaml"


def write_sample_log(path: Path) -> None:
    """Make real decisions and serialize them as the audit sink will."""
    router = Router(Policy.from_yaml(POLICY_PATH))
    prompts = [
        "What time is the standup?",
        "Summarize the sealed deposition transcript for the privileged file.",
        "Redraft the earnings note before the numbers are public.",
    ]
    with path.open("w", encoding="utf-8") as fh:
        for prompt in prompts:
            record = router.route(prompt).to_dict()
            fh.write(json.dumps(record) + "\n")


def replay(path: Path) -> None:
    """Read the JSON-lines file back and print each decision."""
    with path.open(encoding="utf-8") as fh:
        for i, line in enumerate(fh, start=1):
            record = json.loads(line)
            transform = record.get("transform") or "(none)"
            print(
                f"[{i}] backend={record['backend_id']:<14} "
                f"rule={record['rule_id'] or '(default)':<28} "
                f"transform={transform}"
            )
            print(f"     why: {record['reasoning']}")


def main() -> None:
    with tempfile.TemporaryDirectory() as d:
        log = Path(d) / "decisions.jsonl"
        write_sample_log(log)
        print(f"wrote {log.name}, replaying:\n")
        replay(log)


if __name__ == "__main__":
    main()
