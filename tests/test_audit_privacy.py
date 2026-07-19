"""The load-bearing shape-only privacy test (OGE-584).

If a future refactor accidentally puts raw prompt text into an audit row, this
test fails. It is the single contract that keeps the audit "shape-only" promise
honest, so it lives at the top level (not under tests/audit/) to stay obvious.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from ogentic_router import Policy, Router
from ogentic_router.audit import LocalFileSink

CANONICAL_POLICY = Path(__file__).parent.parent / "examples" / "policy.yaml"

# A unique, unmistakable substring. If it appears in the emitted row, we leaked.
SECRET = "4111-1111-1111-1111"


def _stub_shield(*, score: int, groups: list[str] | None = None) -> Any:
    def analyze(_text: str) -> Any:
        return SimpleNamespace(
            score=score,
            category_groups_found=set(groups or []),
            entities=[],
            top_category=None,
            text_hash="sha256:deadbeefdeadbeef",
            entity_count=0,
            profile_ids=["shield-legal"],
        )

    return SimpleNamespace(analyze=analyze)


def test_emitted_row_never_contains_the_prompt(tmp_path: Path) -> None:
    log = tmp_path / "audit.jsonl"
    router = Router(
        Policy.from_yaml(CANONICAL_POLICY),
        shield=_stub_shield(score=45),
        audit_sink=LocalFileSink(log),
    )

    prompt = f"My card is {SECRET}, please summarize the statement."
    router.route(prompt)

    raw = log.read_text(encoding="utf-8")
    assert SECRET not in raw, "audit row leaked the raw prompt text"

    row = json.loads(raw.strip())
    # And nothing prompt-shaped hides in a field — the hash is the only echo.
    assert row["prompt_hash"].startswith("sha256:")
    for key, value in row.items():
        if isinstance(value, str):
            assert SECRET not in value, f"leak in field {key!r}"


def test_error_row_carries_class_name_not_message(tmp_path: Path) -> None:
    """Error rows record the exception class only — never the message text,
    which could echo the prompt."""
    log = tmp_path / "audit.jsonl"

    def _boom(_text: str) -> Any:
        raise ValueError(f"boom: {SECRET}")  # message deliberately contains the secret

    router = Router(
        Policy.from_yaml(CANONICAL_POLICY),
        shield=SimpleNamespace(analyze=_boom),
        audit_sink=LocalFileSink(log),
    )

    try:
        router.route(f"prompt with {SECRET}")
    except ValueError:
        pass

    raw = log.read_text(encoding="utf-8")
    assert SECRET not in raw, "error path leaked the secret"
    row = json.loads(raw.strip())
    assert row["error"] == "ValueError"
