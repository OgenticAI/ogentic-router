"""Budget-ceiling enforcement ON by default + per-engagement opt-out (OGE-1120).

The CTO standing order: flip budget ceilings from off-by-default to on-by-default,
opt-out per engagement, cap read from the policy DSL. These tests lock that
behavior — both the policy-schema half and the Router.route resolution half.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from ogentic_router import BudgetCeilingExceeded, Policy, Router
from ogentic_router.policy import BudgetSpec
from ogentic_router.policy.errors import PolicyError
from ogentic_router.policy.models import DEFAULT_CEILING_USD

CANONICAL_POLICY = Path(__file__).parent.parent / "examples" / "policy.yaml"


def _stub_shield() -> Any:
    def analyze(_text: str) -> Any:
        return SimpleNamespace(
            score=0,
            category_groups_found=set(),
            entities=[],
            top_category=None,
            text_hash="sha256:deadbeefdeadbeef",
            entity_count=0,
            profile_ids=["shield-legal"],
        )

    return SimpleNamespace(analyze=analyze)


def _policy(budget: dict[str, Any] | None = None) -> Policy:
    spec: dict[str, Any] = {"version": 1, "default_backend": "ollama-local"}
    if budget is not None:
        spec["budget"] = budget
    return Policy.from_dict(spec)


# ─── Schema: the flip lives in the model defaults ────────────────────────────


def test_budgetspec_defaults_to_enforce_on() -> None:
    b = BudgetSpec()
    assert b.enforce is True
    assert b.ceiling_usd == DEFAULT_CEILING_USD == 1.0


def test_policy_without_budget_block_enforces_by_default() -> None:
    p = _policy()  # no budget: block at all
    assert p.budget.enforce is True
    assert p.budget.ceiling_usd == 1.0
    assert p.effective_ceiling() == 1.0


def test_policy_opt_out_sets_effective_ceiling_none() -> None:
    p = _policy({"enforce": False})
    assert p.budget.enforce is False
    assert p.effective_ceiling() is None


def test_policy_custom_ceiling() -> None:
    p = _policy({"ceiling_usd": 0.25})
    assert p.effective_ceiling() == 0.25


def test_policy_rejects_non_positive_ceiling() -> None:
    with pytest.raises(PolicyError):
        _policy({"ceiling_usd": 0})
    with pytest.raises(PolicyError):
        _policy({"ceiling_usd": -1})


def test_policy_rejects_unknown_budget_key() -> None:
    with pytest.raises(PolicyError):
        _policy({"cieling_usd": 1.0})  # typo — extra=forbid catches it


def test_budget_round_trips_through_to_dict() -> None:
    p = _policy({"enforce": False, "ceiling_usd": 0.5})
    assert Policy.from_dict(p.to_dict()).to_dict() == p.to_dict()
    # And the default is present on round-trip too.
    p2 = _policy()
    assert p2.to_dict()["budget"] == {"enforce": True, "ceiling_usd": 1.0}


# ─── Router.route resolution ─────────────────────────────────────────────────


def test_route_enforces_policy_budget_by_default() -> None:
    """A runaway prompt is blocked with no explicit ceiling — the flip."""
    p = _policy({"ceiling_usd": 0.0000001})  # absurdly low → anything exceeds
    router = Router(p, shield=_stub_shield())
    with pytest.raises(BudgetCeilingExceeded) as exc:
        router.route("x" * 200)  # note: no budget_ceiling= passed
    assert exc.value.ceiling == 0.0000001


def test_route_normal_prompt_passes_under_default_ceiling() -> None:
    """The $1.00 default never bites a normal prompt."""
    router = Router(Policy.from_yaml(CANONICAL_POLICY), shield=_stub_shield())
    assert router.route("summarize this short note").backend_id  # no raise


def test_route_opt_out_disables_enforcement_even_for_huge_prompt() -> None:
    p = _policy({"enforce": False, "ceiling_usd": 0.0000001})
    router = Router(p, shield=_stub_shield())
    # Would exceed a $0.0000001 ceiling many times over, but enforcement is off.
    assert router.route("x" * 100_000).backend_id == "ollama-local"


def test_explicit_none_disables_for_this_call() -> None:
    """budget_ceiling=None overrides an enforcing policy, per call."""
    p = _policy({"ceiling_usd": 0.0000001})
    router = Router(p, shield=_stub_shield())
    # Without the kwarg this raises (see above); with None it must not.
    assert router.route("x" * 200, budget_ceiling=None).backend_id == "ollama-local"


def test_explicit_number_overrides_policy_ceiling() -> None:
    """An explicit ceiling wins over the policy's for that call."""
    p = _policy({"ceiling_usd": 100.0})  # policy would allow
    router = Router(p, shield=_stub_shield())
    with pytest.raises(BudgetCeilingExceeded):
        router.route("hello", model="gpt-4-turbo", budget_ceiling=0.0000001)


def test_budget_refusal_is_pre_classification() -> None:
    """The default-on ceiling refuses before Shield runs (no network/model)."""
    calls: list[str] = []

    def analyze(text: str) -> Any:
        calls.append(text)
        raise AssertionError("Shield must not be called when the ceiling refuses")

    p = _policy({"ceiling_usd": 0.0000001})
    router = Router(p, shield=SimpleNamespace(analyze=analyze))
    with pytest.raises(BudgetCeilingExceeded):
        router.route("x" * 200)
    assert calls == []
