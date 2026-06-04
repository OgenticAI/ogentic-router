"""Test suite for the Router (OGE-580).

Eight+ unit tests against a stub Shield (``SimpleNamespace`` mock — same
pattern as ``tests/test_policy.py``, no Presidio cold-start cost) plus one
opt-in integration test against the real Shield. Tests are mapped 1:1 to the
spec brief's §4 AC → test table; comments call out which AC each test locks.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from ogentic_router import (
    Policy,
    RouteDecision,
    Router,
    RouterError,
    ShieldClassification,
    ShieldUnavailableError,
)
from ogentic_router.policy.policy import _ShieldResultLike

REPO_ROOT = Path(__file__).parent.parent
CANONICAL_POLICY_PATH = REPO_ROOT / "examples" / "policy.yaml"


# ─── Stub Shield + helpers ──────────────────────────────────────────────────


def _fake_analysis_result(
    *,
    score: int = 0,
    groups: list[str] | None = None,
    entity_categories: list[str] | None = None,
    top_category: str | None = None,
    text_hash: str = "sha256:deadbeefdeadbeef",
    entity_count: int | None = None,
) -> Any:
    """Build a duck-typed ``AnalysisResult`` for unit tests.

    Mirrors the slice of :class:`ogentic_shield.AnalysisResult` the Router
    projects + the policy evaluator reads. No Presidio import, no spaCy
    model load.
    """
    entities = [SimpleNamespace(category=c) for c in (entity_categories or [])]
    return SimpleNamespace(
        score=score,
        category_groups_found=set(groups or []),
        entities=entities,
        top_category=top_category,
        text_hash=text_hash,
        entity_count=entity_count if entity_count is not None else len(entities),
    )


class _FakeShield:
    """Tiny Shield-shaped stub. Records every ``analyze`` call.

    Lets tests assert call-count / argument-passing without monkeypatching
    the real Shield class (which would trigger Presidio init).
    """

    def __init__(self, result: Any | None = None) -> None:
        self._result = result if result is not None else _fake_analysis_result()
        self.calls: list[str] = []

    def analyze(self, text: str) -> Any:
        self.calls.append(text)
        return self._result


@pytest.fixture
def canonical_policy() -> Policy:
    """The canonical policy from ``examples/policy.yaml``."""
    return Policy.from_yaml(CANONICAL_POLICY_PATH)


# ─── 1. Bare constructor / programmatic use ─────────────────────────────────


def test_bare_constructor_holds_policy_and_shield(canonical_policy: Policy) -> None:
    """``Router(policy, shield=...)`` is the test/programmatic entry point.

    AC: bare constructor for tests / programmatic use.
    """
    shield = _FakeShield()
    router = Router(policy=canonical_policy, shield=shield)
    assert router.policy is canonical_policy
    # Internal attribute access is fine in a same-package test.
    assert router._shield is shield


# ─── 2. classify() projects AnalysisResult into ShieldClassification ────────


def test_classify_projects_analysis_result(canonical_policy: Policy) -> None:
    """AC: ``Router.classify`` returns a fully-populated ``ShieldClassification``."""
    shield = _FakeShield(
        _fake_analysis_result(
            score=42,
            groups=["PRIVILEGE", "PHI"],
            entity_categories=["LEGAL_PRIVILEGE", "MEDICAL_RECORD"],
            top_category="LEGAL_PRIVILEGE",
            text_hash="sha256:0123456789abcdef",
        )
    )
    router = Router(policy=canonical_policy, shield=shield)
    cls = router.classify("Privileged attorney-client memo...")

    assert isinstance(cls, ShieldClassification)
    assert cls.score == 42
    assert cls.category_groups_found == frozenset({"PRIVILEGE", "PHI"})
    assert cls.top_category == "LEGAL_PRIVILEGE"
    assert cls.entity_count == 2
    assert cls.text_hash == "sha256:0123456789abcdef"
    assert shield.calls == ["Privileged attorney-client memo..."]


# ─── 3. route() chains classify → policy.evaluate ───────────────────────────


def test_route_chains_classify_into_evaluate(canonical_policy: Policy) -> None:
    """AC: ``Router.route`` returns ``RouteDecision`` from chained evaluation.

    Privileged content should hit the ``privilege-stays-local`` rule per the
    canonical policy.
    """
    shield = _FakeShield(
        _fake_analysis_result(
            score=85,
            groups=["PRIVILEGE"],
            entity_categories=["LEGAL_PRIVILEGE"],
            top_category="LEGAL_PRIVILEGE",
        )
    )
    router = Router(policy=canonical_policy, shield=shield)
    decision = router.route("Privileged attorney-client memo...")

    assert isinstance(decision, RouteDecision)
    assert decision.backend_id == "ollama-local"
    assert decision.rule_id == "privilege-stays-local"
    assert decision.transform is None


def test_route_propagates_entity_categories_to_policy() -> None:
    """``category_in`` / ``category_not_in`` predicates fire on raw entities.

    Locks the load-bearing decision in router.py: the user-facing
    ``ShieldClassification`` projection omits the entity list, but
    ``Router.route`` wraps it in an internal adapter so Policy's full
    Protocol surface is satisfied.
    """
    policy = Policy.from_dict(
        {
            "version": 1,
            "default_backend": "fallback",
            "rules": [
                {
                    "id": "any-credit-card",
                    "when": {"category_in": ["CREDIT_CARD"]},
                    "route": "redact-pipeline",
                }
            ],
        }
    )
    shield = _FakeShield(
        _fake_analysis_result(
            score=10,
            entity_categories=["PERSON_NAME", "CREDIT_CARD"],
            top_category="PERSON_NAME",
        )
    )
    router = Router(policy=policy, shield=shield)
    decision = router.route("payload with a credit card number")
    assert decision.rule_id == "any-credit-card"
    assert decision.backend_id == "redact-pipeline"


# ─── 4. Single shared Shield instance ───────────────────────────────────────


def test_shield_instance_reused_across_calls(canonical_policy: Policy) -> None:
    """AC: no per-call cold start. The Shield instance is created once.

    Verified via ``id(router._shield)`` stability + ``_FakeShield.calls``
    accumulating instead of resetting.
    """
    shield = _FakeShield(_fake_analysis_result(score=5))
    router = Router(policy=canonical_policy, shield=shield)
    shield_id_before = id(router._shield)

    router.classify("first")
    router.classify("second")
    router.route("third")

    assert id(router._shield) == shield_id_before
    assert shield.calls == ["first", "second", "third"]


# ─── 5. ShieldClassification satisfies _ShieldResultLike Protocol ───────────


def test_classification_satisfies_shield_result_like_protocol() -> None:
    """AC: ``ShieldClassification`` is structurally compatible with the policy
    evaluator's duck-typed ``_ShieldResultLike`` Protocol on the fields the
    Protocol actually reads (``score``, ``category_groups_found``,
    ``top_category``).

    Note: the Protocol nominally declares ``entities: list[_EntityLike]``,
    but ``runtime_checkable`` Protocol checks in Python only verify
    attribute presence, not type. Router's ``route()`` path attaches the
    raw entity list via an internal adapter when entity-level predicates
    need to fire — see ``test_route_propagates_entity_categories_to_policy``.
    """
    cls = ShieldClassification(
        score=42,
        category_groups_found=frozenset({"PRIVILEGE"}),
        top_category="LEGAL_PRIVILEGE",
        entity_count=2,
        text_hash="sha256:abc",
    )
    # The three fields the Policy DSL's predicates read on the projection:
    assert hasattr(cls, "score")
    assert hasattr(cls, "category_groups_found")
    assert hasattr(cls, "top_category")
    # And it's a frozen dataclass: equality + hash work.
    assert cls == ShieldClassification(
        score=42,
        category_groups_found=frozenset({"PRIVILEGE"}),
        top_category="LEGAL_PRIVILEGE",
        entity_count=2,
        text_hash="sha256:abc",
    )
    assert hash(cls) == hash(
        ShieldClassification(
            score=42,
            category_groups_found=frozenset({"PRIVILEGE"}),
            top_category="LEGAL_PRIVILEGE",
            entity_count=2,
            text_hash="sha256:abc",
        )
    )
    # Reference the Protocol type so it's not flagged as dead import.
    assert _ShieldResultLike is not None


# ─── 6. text_hash projection ────────────────────────────────────────────────


def test_classification_text_hash_passes_through_shield_value(
    canonical_policy: Policy,
) -> None:
    """AC: audit-ref hash uses Shield's ``text_hash`` field (populated by
    Shield's ``text_hash_for`` helper during ``analyze()``). Router does not
    re-hash.
    """
    shield = _FakeShield(
        _fake_analysis_result(score=10, text_hash="sha256:0fedcba987654321")
    )
    router = Router(policy=canonical_policy, shield=shield)
    cls = router.classify("anything")
    assert cls.text_hash == "sha256:0fedcba987654321"


# ─── 7. Missing [shield] extra raises with hint ─────────────────────────────


def test_missing_shield_raises_with_install_hint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC: missing ``[shield]`` extra raises ``ShieldUnavailableError`` with
    the ``pip install 'ogentic-router[shield]'`` hint.

    Implemented by monkeypatching ``sys.modules`` to make
    ``ogentic_shield`` look uninstalled, then forcing the lazy import path
    via ``Router.from_config``.
    """
    # Drop ogentic_shield from sys.modules so the lazy import re-resolves.
    monkeypatch.setitem(sys.modules, "ogentic_shield", None)

    # Build a minimal config that triggers Shield import.
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(
        "version: 1\ndefault_backend: ollama-local\nrules: []\n"
    )

    with pytest.raises(ShieldUnavailableError) as exc_info:
        Router.from_config({"policy_path": str(policy_path)})

    msg = str(exc_info.value)
    assert "ogentic-router[shield]" in msg
    # Subclasses ImportError so the standard idiom catches it too.
    assert isinstance(exc_info.value, ImportError)
    assert isinstance(exc_info.value, RouterError)


# ─── 8. Profile pass-through (no hardcoded profiles) ────────────────────────


def test_profiles_pass_through_from_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC: profile defaults inherit from Shield's ``ShieldConfig`` — Router
    never hardcodes profile choices. Verified by intercepting the Shield
    constructor and asserting the kwargs forwarded.
    """
    captured: dict[str, Any] = {}

    class _CapturingShield:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

        def analyze(self, text: str) -> Any:  # pragma: no cover - unused
            return _fake_analysis_result()

    # Patch the lazy import to hand back our capturing Shield.
    import ogentic_router.router as router_mod

    monkeypatch.setattr(
        router_mod,
        "_import_shield",
        lambda: (_CapturingShield, lambda t: "sha256:stub"),
    )

    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(
        "version: 1\ndefault_backend: ollama-local\nrules: []\n"
    )

    Router.from_config(
        {
            "policy_path": str(policy_path),
            "shield": {"profiles": ["shield-legal", "shield-finance"]},
        }
    )

    assert captured.get("profiles") == ["shield-legal", "shield-finance"]
    # And no profiles passed → no profiles kwarg (Shield's default kicks in).
    captured.clear()
    Router.from_config({"policy_path": str(policy_path)})
    assert "profiles" not in captured


# ─── 9. from_yaml resolves policy_path relative to the config file's dir ────


def test_from_yaml_resolves_policy_path_relative_to_config_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC: ``from_yaml`` resolves ``policy_path`` relative to the config
    file's directory so ``router.yaml`` + ``policy.yaml`` ship side-by-side
    without absolute paths.
    """

    class _StubShield:
        def __init__(self, **kwargs: Any) -> None: ...
        def analyze(self, text: str) -> Any:  # pragma: no cover - unused
            return _fake_analysis_result()

    import ogentic_router.router as router_mod

    monkeypatch.setattr(
        router_mod,
        "_import_shield",
        lambda: (_StubShield, lambda t: "sha256:stub"),
    )

    (tmp_path / "policy.yaml").write_text(
        "version: 1\ndefault_backend: ollama-local\nrules: []\n"
    )
    (tmp_path / "router.yaml").write_text("policy_path: policy.yaml\n")

    router = Router.from_yaml(tmp_path / "router.yaml")
    assert isinstance(router.policy, Policy)
    assert router.policy.default_backend == "ollama-local"


# ─── 10. Public re-exports resolve ──────────────────────────────────────────


def test_public_reexports_resolve() -> None:
    """AC: public re-exports — ``from ogentic_router import Router,
    ShieldClassification, RouterError`` works at the top level.
    """
    # These are the new symbols introduced by OGE-580.
    from ogentic_router import Router as R
    from ogentic_router import RouterError as RE
    from ogentic_router import ShieldClassification as SC
    from ogentic_router import ShieldUnavailableError as SUE

    assert R is Router
    assert RE is RouterError
    assert SC is ShieldClassification
    assert SUE is ShieldUnavailableError


# ─── 11. Missing policy_path raises clear RouterError ───────────────────────


def test_from_config_missing_policy_path_raises_router_error() -> None:
    """Defensive: a config without ``policy_path`` raises ``RouterError``
    with a helpful message before any Shield work happens.
    """
    with pytest.raises(RouterError) as exc_info:
        Router.from_config({"shield": {"profiles": ["x"]}})
    assert "policy_path" in str(exc_info.value)


# ─── 12. Integration test (opt-in, real Shield) ─────────────────────────────


@pytest.mark.integration
@pytest.mark.skipif(
    not os.environ.get("OGENTIC_ROUTER_SHIELD_INTEGRATION"),
    reason="Set OGENTIC_ROUTER_SHIELD_INTEGRATION=1 to exercise the real Shield "
    "(slow — Presidio / spaCy cold start).",
)
def test_real_shield_integration(canonical_policy: Policy) -> None:
    """AC: integration test against real Shield, opt-in via env var.

    Asserts the full classify → route pipeline produces a sensible
    classification on a privileged sample. Also verifies the Shield
    instance is reused — second call's wall-time is ≤ first call's
    (cold-start amortised).
    """
    from ogentic_shield import Shield

    router = Router(policy=canonical_policy, shield=Shield())

    sample = (
        "ATTORNEY-CLIENT PRIVILEGED MEMO. From counsel to John Smith, SSN "
        "123-45-6789, regarding the pending M&A transaction with Acme Corp."
    )

    t0 = time.perf_counter()
    cls = router.classify(sample)
    first_call_s = time.perf_counter() - t0

    t1 = time.perf_counter()
    decision = router.route(sample)
    second_call_s = time.perf_counter() - t1

    assert cls.score > 0, "real Shield should flag the sample"
    assert len(cls.category_groups_found) > 0
    assert isinstance(decision, RouteDecision)
    # Reused instance → second call should be much faster than the first.
    # 50% slack to keep the test stable on noisy CI.
    assert second_call_s <= first_call_s * 1.5, (
        f"second call ({second_call_s:.3f}s) should be ≤ 1.5x first ({first_call_s:.3f}s) "
        "— Shield instance is not being reused."
    )
