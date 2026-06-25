"""Tests for --budget-ceiling / budget_ceiling= fail-fast enforcement (OGE-1061).

Covers all acceptance criteria:

1. CLI: exits non-zero with BudgetCeilingExceeded on stderr when cost > ceiling
2. CLI: proceeds normally when cost <= ceiling
3. CLI: dry-run mode (ceiling=0) refuses all calls
4. Python API: router.route() raises BudgetCeilingExceeded
5. Exception attributes: estimated_cost, ceiling, model
6. No network call made when ceiling exceeded (no Shield.analyze called)
7. All 3 ceiling cases (None, 0, >0) on both surfaces
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from click.testing import CliRunner

from ogentic_router import BudgetCeilingExceeded, Policy, Router, RouterError
from ogentic_router.cli.main import cli
from ogentic_router.cost import estimate_cost

REPO_ROOT = Path(__file__).parent.parent
CANONICAL_POLICY_PATH = REPO_ROOT / "examples" / "policy.yaml"


# ─── Helpers ────────────────────────────────────────────────────────────────


def _fake_analysis_result(**kwargs: Any) -> Any:
    defaults: dict[str, Any] = {
        "score": 0,
        "category_groups_found": set(),
        "entities": [],
        "top_category": None,
        "text_hash": "sha256:test",
        "entity_count": 0,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


class _TrackingShield:
    """Stub that records analyze() calls so tests can assert no-network-call."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def analyze(self, text: str) -> Any:
        self.calls.append(text)
        return _fake_analysis_result()


@pytest.fixture
def canonical_policy() -> Policy:
    return Policy.from_yaml(CANONICAL_POLICY_PATH)


# ─── estimate_cost unit tests ────────────────────────────────────────────────


def test_estimate_cost_known_model_returns_positive() -> None:
    cost = estimate_cost("gpt-4-turbo", "hello world")
    assert cost > 0.0


def test_estimate_cost_expensive_model_higher_than_cheap() -> None:
    prompt = "A" * 1000
    opus_cost = estimate_cost("claude-3-opus", prompt)
    haiku_cost = estimate_cost("claude-3-haiku", prompt)
    assert opus_cost > haiku_cost


def test_estimate_cost_unknown_model_uses_conservative_fallback() -> None:
    cost = estimate_cost("some-unknown-model-xyz", "hello")
    assert cost > 0.0


def test_estimate_cost_version_suffix_prefix_match() -> None:
    cost_base = estimate_cost("claude-3-opus", "hello")
    cost_versioned = estimate_cost("claude-3-opus-20240229", "hello")
    assert cost_base == cost_versioned


def test_estimate_cost_case_insensitive() -> None:
    cost_lower = estimate_cost("gpt-4-turbo", "hello")
    cost_upper = estimate_cost("GPT-4-TURBO", "hello")
    assert cost_lower == cost_upper


def test_estimate_cost_scales_with_prompt_length() -> None:
    short_cost = estimate_cost("gpt-4-turbo", "hi")
    long_cost = estimate_cost("gpt-4-turbo", "A" * 10_000)
    assert long_cost > short_cost


# ─── BudgetCeilingExceeded exception attributes ──────────────────────────────


def test_budget_ceiling_exceeded_attributes() -> None:
    """AC: exception has estimated_cost + ceiling + model attributes."""
    exc = BudgetCeilingExceeded(estimated_cost=0.05, ceiling=0.001, model="gpt-4-turbo")
    assert exc.estimated_cost == 0.05
    assert exc.ceiling == 0.001
    assert exc.model == "gpt-4-turbo"
    assert isinstance(exc, RouterError)


def test_budget_ceiling_exceeded_message_includes_values() -> None:
    exc = BudgetCeilingExceeded(estimated_cost=0.05, ceiling=0.001, model="opus-4")
    msg = str(exc)
    assert "BudgetCeilingExceeded" in msg
    assert "0.001" in msg


# ─── Python API: Router.route() — 3 ceiling cases ───────────────────────────


def test_route_no_ceiling_passes_through(canonical_policy: Policy) -> None:
    """Case: budget_ceiling=None (default) — no enforcement, existing behaviour."""
    shield = _TrackingShield()
    router = Router(policy=canonical_policy, shield=shield)
    result = router.route("some prompt")
    # Shield was called (routing happened normally)
    assert len(shield.calls) == 1
    assert shield.calls[0] == "some prompt"
    assert result is not None


def test_route_ceiling_zero_refuses_all(canonical_policy: Policy) -> None:
    """Case: budget_ceiling=0.0 — dry-run mode, refuse every call."""
    shield = _TrackingShield()
    router = Router(policy=canonical_policy, shield=shield)
    with pytest.raises(BudgetCeilingExceeded) as exc_info:
        router.route("any prompt at all", model="gpt-4-turbo", budget_ceiling=0.0)
    assert exc_info.value.ceiling == 0.0
    assert exc_info.value.model == "gpt-4-turbo"
    # No Shield.analyze call — check happens before the call leaves the device
    assert len(shield.calls) == 0


def test_route_ceiling_exceeded_raises(canonical_policy: Policy) -> None:
    """Case: budget_ceiling>0 and cost exceeds it — raises BudgetCeilingExceeded."""
    shield = _TrackingShield()
    router = Router(policy=canonical_policy, shield=shield)
    # A very tight ceiling — even a tiny prompt against an expensive model exceeds it
    with pytest.raises(BudgetCeilingExceeded) as exc_info:
        router.route("hello", model="gpt-4-turbo", budget_ceiling=0.000001)
    exc = exc_info.value
    assert exc.model == "gpt-4-turbo"
    assert exc.ceiling == 0.000001
    assert exc.estimated_cost > exc.ceiling
    # No network call
    assert len(shield.calls) == 0


def test_route_ceiling_not_exceeded_proceeds(canonical_policy: Policy) -> None:
    """Case: budget_ceiling>0 and cost is within it — routing proceeds normally."""
    shield = _TrackingShield()
    router = Router(policy=canonical_policy, shield=shield)
    # Very generous ceiling — a tiny prompt won't exceed $100
    result = router.route("hi", model="gpt-4-turbo", budget_ceiling=100.0)
    assert result is not None
    # Shield was called — routing happened
    assert len(shield.calls) == 1


# ─── No network call when ceiling exceeded (AC: no provider hit) ─────────────


def test_no_shield_analyze_when_ceiling_exceeded(canonical_policy: Policy) -> None:
    """AC: no network call is made when the ceiling is exceeded.

    The BudgetCeilingExceeded check runs before _ensure_shield() so the
    Shield.analyze (which would eventually trigger a provider call) is
    never reached.
    """
    shield = _TrackingShield()
    router = Router(policy=canonical_policy, shield=shield)
    with pytest.raises(BudgetCeilingExceeded):
        router.route("hello world", model="claude-3-opus", budget_ceiling=0.0)
    assert shield.calls == [], "Shield.analyze must not be called when ceiling is exceeded"


# ─── CLI: route subcommand — 3 ceiling cases ─────────────────────────────────


def test_cli_route_ceiling_exceeded_exits_nonzero() -> None:
    """AC: CLI exits non-zero with BudgetCeilingExceeded on stderr."""
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["route", "--model", "gpt-4-turbo", "--prompt", "hello", "--budget-ceiling", "0.000001"],
    )
    assert result.exit_code != 0
    assert "BudgetCeilingExceeded" in (result.output + (result.stderr if hasattr(result, "stderr") else ""))


def test_cli_route_ceiling_not_exceeded_exits_zero() -> None:
    """AC: CLI proceeds normally when cost is within ceiling."""
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["route", "--model", "gpt-4-turbo", "--prompt", "hi", "--budget-ceiling", "100.0"],
    )
    assert result.exit_code == 0


def test_cli_route_ceiling_zero_refuses() -> None:
    """AC: CLI dry-run mode (--budget-ceiling 0) refuses all calls."""
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["route", "--model", "opus-4", "--prompt", "hi", "--budget-ceiling", "0"],
    )
    assert result.exit_code != 0
    assert "BudgetCeilingExceeded" in (result.output + (result.stderr if hasattr(result, "stderr") else ""))


def test_cli_route_no_ceiling_succeeds() -> None:
    """CLI route without --budget-ceiling succeeds (no enforcement)."""
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["route", "--model", "gpt-4-turbo", "--prompt", "hello"],
    )
    assert result.exit_code == 0
