"""Exercise the runnable snippets shipped in README.md, docs/, and examples/.

These lock the "all snippets are tested" acceptance criterion of OGE-587. They
use a stub Shield (``SimpleNamespace``) — the same pattern as ``test_router.py``
and ``test_policy.py`` — so they run fast and deterministically without a
Presidio cold-start. The point is to prove the *public API shapes* the docs show
match the code, so a snippet that drifts from reality breaks CI.
"""

from __future__ import annotations

import runpy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from ogentic_router import BudgetCeilingExceeded, Policy, RouteDecision, Router, Transform

REPO_ROOT = Path(__file__).parent.parent
EXAMPLES = REPO_ROOT / "examples"
CANONICAL_POLICY = EXAMPLES / "policy.yaml"


def _stub_shield(*, score: int = 0, groups: list[str] | None = None) -> Any:
    """A duck-typed Shield: only needs ``analyze(text) -> AnalysisResult``."""

    def analyze(_text: str) -> Any:
        return SimpleNamespace(
            score=score,
            category_groups_found=set(groups or []),
            entities=[],
            top_category=None,
            text_hash="sha256:deadbeefdeadbeef",
            entity_count=0,
        )

    return SimpleNamespace(analyze=analyze)


# ── README: "Get started in 30 seconds" ──────────────────────────────────────


def test_readme_get_started_snippet() -> None:
    """`Policy.from_yaml` + `Router.route` return a decision with the shown fields."""
    policy = Policy.from_yaml(CANONICAL_POLICY)
    router = Router(policy, shield=_stub_shield(groups=["PRIVILEGE"]))

    decision = router.route("Draft a note about the privileged settlement memo.")

    assert isinstance(decision, RouteDecision)
    assert decision.backend_id == "ollama-local"  # privilege stays on-device
    assert decision.rule_id == "privilege-stays-local"
    assert isinstance(decision.reasoning, str) and decision.reasoning


def test_readme_low_sensitivity_goes_cloud() -> None:
    policy = Policy.from_yaml(CANONICAL_POLICY)
    router = Router(policy, shield=_stub_shield(score=5))

    decision = router.route("What's the weather in Lagos?")

    assert decision.backend_id == "openai-cloud"


def test_readme_medium_sensitivity_redacts() -> None:
    policy = Policy.from_yaml(CANONICAL_POLICY)
    router = Router(policy, shield=_stub_shield(score=45))

    decision = router.route("Redraft the earnings note before the numbers are public.")

    assert decision.backend_id == "openai-cloud"
    assert decision.transform is Transform.SHIELD_REDACT


# ── README: budget ceiling snippet ───────────────────────────────────────────


def test_readme_budget_ceiling_blocks() -> None:
    policy = Policy.from_yaml(CANONICAL_POLICY)
    router = Router(policy, shield=_stub_shield())

    with pytest.raises(BudgetCeilingExceeded) as exc:
        router.route("hello" * 100, model="gpt-4o-mini", budget_ceiling=0.0)

    assert exc.value.ceiling == 0.0
    assert exc.value.estimated_cost >= 0.0


# ── docs/POLICY_REFERENCE.md: to_dict() record shape ─────────────────────────


def test_policy_reference_to_dict_shape() -> None:
    policy = Policy.from_yaml(CANONICAL_POLICY)
    router = Router(policy, shield=_stub_shield(groups=["PHI"]))

    record = router.route("patient chart follow-up").to_dict()

    assert set(record) == {"backend_id", "rule_id", "transform", "reasoning"}


# ── examples/*.py: run end-to-end with a stubbed classifier ──────────────────


@pytest.fixture
def _patch_default_shield(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the examples' bare ``Router(policy)`` use the stub, not real Shield.

    The example scripts construct ``Router(Policy.from_yaml(...))`` with no
    shield, which would lazily import ogentic-shield and pay a Presidio
    cold-start. Patch the lazy default so the scripts run fast and offline.
    """
    import ogentic_router.router as router_mod

    # ``_ensure_shield`` unpacks ``Shield, _text_hash_for = _import_shield()`` and
    # then calls ``Shield()`` with no args. Return a matching 2-tuple.
    def _fake_import_shield() -> tuple[Any, Any]:
        return (lambda: _stub_shield()), (lambda text: "sha256:deadbeefdeadbeef")

    monkeypatch.setattr(router_mod, "_import_shield", _fake_import_shield)


def test_example_route_string_runs(_patch_default_shield: None, capsys: pytest.CaptureFixture[str]) -> None:
    runpy.run_path(str(EXAMPLES / "route_string.py"), run_name="__main__")
    out = capsys.readouterr().out
    assert "backend:" in out


def test_example_audit_replay_runs(_patch_default_shield: None, capsys: pytest.CaptureFixture[str]) -> None:
    runpy.run_path(str(EXAMPLES / "audit_replay.py"), run_name="__main__")
    out = capsys.readouterr().out
    assert "backend=" in out
