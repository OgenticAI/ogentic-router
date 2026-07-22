"""Shared fixtures for the CLI tests (OGE-585).

Patches ``Router._import_shield`` with a fast, keyword-driven stub so tests never
pay the Presidio / spaCy cold-start and routing is deterministic:

- text mentioning privilege/attorney → score 85, group PRIVILEGE → local rule
- text mentioning "redact"/"medium"    → score 45 → medium-redact-then-cloud
- anything else                        → score 0 → low-cloud
"""

from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
CANONICAL_POLICY = REPO_ROOT / "examples" / "policy.yaml"


def _fake_text_hash(text: str) -> str:
    import hashlib

    return f"sha256:{hashlib.sha256(text.encode()).hexdigest()[:16]}"


class _StubShield:
    def __init__(self, **_kwargs: Any) -> None:  # accepts profiles=/config=
        pass

    def analyze(self, text: str) -> Any:
        low = text.lower()
        if "privileg" in low or "attorney" in low:
            score, groups, top = 85, {"PRIVILEGE"}, "LEGAL_PRIVILEGE"
        elif "redact" in low or "medium" in low:
            score, groups, top = 45, set(), None
        else:
            score, groups, top = 0, set(), None
        return SimpleNamespace(
            score=score,
            category_groups_found=groups,
            entities=[],
            top_category=top,
            text_hash=_fake_text_hash(text),
            entity_count=0,
            profile_ids=["shield-legal"],
        )


@pytest.fixture(autouse=True)
def stub_shield(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "ogentic_router.router._import_shield",
        lambda: (_StubShield, _fake_text_hash),
    )


@pytest.fixture()
def policy_file(tmp_path: Path) -> Path:
    dest = tmp_path / "policy.yaml"
    shutil.copy(CANONICAL_POLICY, dest)
    return dest


@pytest.fixture()
def router_config(tmp_path: Path, policy_file: Path) -> Path:
    """A router.yaml next to the policy, with one local backend declared."""
    cfg = tmp_path / "router.yaml"
    cfg.write_text(
        "version: 1\n"
        "policy_path: policy.yaml\n"
        "backends:\n"
        "  - id: ollama-local\n"
        "    kind: ollama\n"
        "    base_url: http://localhost:11434\n"
        "    default_model: llama3.2:3b\n",
        encoding="utf-8",
    )
    return cfg
