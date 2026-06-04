"""Test suite for the policy DSL (OGE-579).

18 tests, mapped 1:1 to the spec brief's §4 table — every acceptance
criterion and UAT checkbox is exercised here. Tests use ``SimpleNamespace``
mocks for the Shield result so the unit-test path never pays the Presidio
cold-start cost; the duck-typed contract is documented on
:class:`ogentic_router.policy.policy._ShieldResultLike`.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from ogentic_router import Policy, PolicyError, RouteDecision, Transform

REPO_ROOT = Path(__file__).parent.parent
CANONICAL_POLICY_PATH = REPO_ROOT / "examples" / "policy.yaml"


# ─── Shared fixtures / helpers ──────────────────────────────────────────────


def _mk_result(
    *,
    score: int = 0,
    groups: list[str] | None = None,
    entity_categories: list[str] | None = None,
    top_category: str | None = None,
) -> Any:
    """Build a duck-typed Shield result for tests.

    Mirrors the slice of :class:`ogentic_shield.AnalysisResult` the policy
    evaluator reads (see ``policy.policy._ShieldResultLike``). Tests stay
    light — no Presidio import, no spaCy model load.
    """
    entities = [SimpleNamespace(category=c) for c in (entity_categories or [])]
    return SimpleNamespace(
        score=score,
        category_groups_found=set(groups or []),
        entities=entities,
        top_category=top_category,
    )


@pytest.fixture
def canonical_dict() -> dict:
    """The dict form of ``examples/policy.yaml``."""
    return {
        "version": 1,
        "default_backend": "ollama-local",
        "rules": [
            {
                "id": "privilege-stays-local",
                "when": {"groups_include": ["PRIVILEGE", "PHI", "MNPI"]},
                "route": "ollama-local",
            },
            {
                "id": "high-sensitivity-stays-local",
                "when": {"sensitivity_score_gte": 70},
                "route": "ollama-local",
            },
            {
                "id": "medium-redact-then-cloud",
                "when": {"sensitivity_score_gte": 30},
                "route": "openai-cloud",
                "transform": "shield_redact",
            },
            {
                "id": "low-cloud",
                "when": {"sensitivity_score_gte": 0},
                "route": "openai-cloud",
            },
        ],
    }


# ─── 1–3. Loader contracts ──────────────────────────────────────────────────


def test_from_yaml_loads_canonical_example() -> None:
    policy = Policy.from_yaml(CANONICAL_POLICY_PATH)
    assert isinstance(policy, Policy)
    assert policy.default_backend == "ollama-local"
    assert len(policy.rules) == 4


def test_from_dict_loads_canonical_example(canonical_dict: dict) -> None:
    policy = Policy.from_dict(canonical_dict)
    assert isinstance(policy, Policy)
    assert [r.id for r in policy.rules] == [
        "privilege-stays-local",
        "high-sensitivity-stays-local",
        "medium-redact-then-cloud",
        "low-cloud",
    ]


def test_to_dict_round_trips_canonical(canonical_dict: dict) -> None:
    """``Policy.from_dict(p.to_dict()).to_dict() == p.to_dict()``.

    The round-trip property is the contract that lets OGE-585's ``policies
    show`` and OGE-586's ``router.policies`` MCP tool serialise without a
    custom adapter.
    """
    p1 = Policy.from_dict(canonical_dict)
    d1 = p1.to_dict()
    p2 = Policy.from_dict(d1)
    d2 = p2.to_dict()
    assert d1 == d2


# ─── 4–9. Predicate semantics ───────────────────────────────────────────────


def test_groups_include_privilege_routes_to_local(canonical_dict: dict) -> None:
    policy = Policy.from_dict(canonical_dict)
    result = _mk_result(score=10, groups=["PRIVILEGE"])
    decision = policy.evaluate(result)
    assert decision.backend_id == "ollama-local"
    assert decision.rule_id == "privilege-stays-local"
    assert "PRIVILEGE" in decision.reasoning


def test_groups_exclude_skips_when_present() -> None:
    """A rule with ``groups_exclude: [PII]`` does NOT match when PII is present."""
    policy = Policy.from_dict(
        {
            "version": 1,
            "default_backend": "fallback",
            "rules": [
                {
                    "id": "cloud-unless-pii",
                    "when": {"groups_exclude": ["PII"]},
                    "route": "openai-cloud",
                },
            ],
        }
    )
    # PII present → rule skipped → default_backend fires.
    decision_with_pii = policy.evaluate(_mk_result(groups=["PII"]))
    assert decision_with_pii.backend_id == "fallback"
    assert decision_with_pii.rule_id is None
    # PII absent → rule matches.
    decision_clean = policy.evaluate(_mk_result(groups=["SAFE"]))
    assert decision_clean.backend_id == "openai-cloud"
    assert decision_clean.rule_id == "cloud-unless-pii"


def test_sensitivity_score_gte_routes_correctly(canonical_dict: dict) -> None:
    policy = Policy.from_dict(canonical_dict)
    # score=70 hits the high-sensitivity rule (gte 70).
    decision = policy.evaluate(_mk_result(score=70))
    assert decision.rule_id == "high-sensitivity-stays-local"
    assert decision.backend_id == "ollama-local"
    # score=69 falls through to medium (gte 30, with shield_redact).
    decision_below = policy.evaluate(_mk_result(score=69))
    assert decision_below.rule_id == "medium-redact-then-cloud"
    assert decision_below.transform == Transform.SHIELD_REDACT


def test_sensitivity_score_lt_routes_correctly() -> None:
    policy = Policy.from_dict(
        {
            "version": 1,
            "default_backend": "fallback",
            "rules": [
                {
                    "id": "low-score-only",
                    "when": {"sensitivity_score_lt": 50},
                    "route": "openai-cloud",
                },
            ],
        }
    )
    assert policy.evaluate(_mk_result(score=49)).rule_id == "low-score-only"
    assert policy.evaluate(_mk_result(score=50)).rule_id is None  # fallback
    assert policy.evaluate(_mk_result(score=60)).rule_id is None


def test_category_in_matches_any_entity_category() -> None:
    """``category_in`` is **any-entity** match, not top-category-only.

    Locks the spec brief's §7 'load-bearing decision' — covers
    "route based on the presence of any Y", not just the dominant signal.
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
                },
            ],
        }
    )
    # top_category is PERSON_NAME but a non-top entity is CREDIT_CARD → matches.
    result = _mk_result(
        top_category="PERSON_NAME",
        entity_categories=["PERSON_NAME", "CREDIT_CARD"],
    )
    assert policy.evaluate(result).rule_id == "any-credit-card"
    # No CREDIT_CARD anywhere → fallback.
    result_clean = _mk_result(top_category="PERSON_NAME", entity_categories=["PERSON_NAME"])
    assert policy.evaluate(result_clean).rule_id is None


def test_category_not_in_excludes_when_present() -> None:
    policy = Policy.from_dict(
        {
            "version": 1,
            "default_backend": "fallback",
            "rules": [
                {
                    "id": "cloud-unless-credit-card",
                    "when": {"category_not_in": ["CREDIT_CARD"]},
                    "route": "openai-cloud",
                },
            ],
        }
    )
    has_cc = _mk_result(entity_categories=["CREDIT_CARD"])
    assert policy.evaluate(has_cc).rule_id is None  # fallback
    no_cc = _mk_result(entity_categories=["PERSON_NAME"])
    assert policy.evaluate(no_cc).rule_id == "cloud-unless-credit-card"


# ─── 10–11. Precedence + fallback ───────────────────────────────────────────


def test_first_match_wins() -> None:
    """When two rules both match, the earlier one's backend is chosen."""
    policy = Policy.from_dict(
        {
            "version": 1,
            "default_backend": "fallback",
            "rules": [
                {
                    "id": "first",
                    "when": {"sensitivity_score_gte": 50},
                    "route": "backend-A",
                },
                {
                    "id": "second",
                    "when": {"sensitivity_score_gte": 50},
                    "route": "backend-B",
                },
            ],
        }
    )
    decision = policy.evaluate(_mk_result(score=80))
    assert decision.rule_id == "first"
    assert decision.backend_id == "backend-A"


def test_default_backend_when_no_rule_matches() -> None:
    policy = Policy.from_dict(
        {
            "version": 1,
            "default_backend": "my-default",
            "rules": [
                {
                    "id": "high-only",
                    "when": {"sensitivity_score_gte": 99},
                    "route": "x",
                },
            ],
        }
    )
    decision = policy.evaluate(_mk_result(score=10))
    # Use the public RouteDecision type explicitly — exercises the top-level re-export.
    assert isinstance(decision, RouteDecision)
    assert decision.backend_id == "my-default"
    assert decision.rule_id is None
    assert decision.transform is None
    assert "default_backend fired" in decision.reasoning
    # Round-trips through to_dict so MCP / CLI consumers can serialise.
    payload = decision.to_dict()
    assert payload == {
        "backend_id": "my-default",
        "rule_id": None,
        "transform": None,
        "reasoning": decision.reasoning,
    }


# ─── 12–13. Validation error UX ─────────────────────────────────────────────


def test_unknown_field_rejected_with_path() -> None:
    """``extra="forbid"`` produces a clear JSON-path on unknown keys."""
    with pytest.raises(PolicyError) as exc_info:
        Policy.from_dict(
            {
                "version": 1,
                "default_backend": "x",
                "rules": [],
                "unknown_top_field": "oops",
            }
        )
    assert "unknown_top_field" in str(exc_info.value)


def test_unknown_category_suggests_closest() -> None:
    """Levenshtein hint on typo'd category names."""
    with pytest.raises(PolicyError) as exc_info:
        Policy.from_dict(
            {
                "version": 1,
                "default_backend": "x",
                "rules": [
                    {
                        "id": "typo-rule",
                        "when": {"groups_include": ["PRIVILEDGE"]},  # typo
                        "route": "x",
                    },
                ],
            }
        )
    message = str(exc_info.value)
    assert "PRIVILEDGE" in message
    assert "PRIVILEGE" in message
    assert "did you mean" in message.lower()


# ─── 14. Multi-profile union semantics ──────────────────────────────────────


def test_multi_profile_union_match(canonical_dict: dict) -> None:
    """``groups_include`` matches the doc-level UNION of ``category_groups_found``.

    This is the load-bearing semantics decision documented in the spec
    brief: a Shield result whose union contains PRIVILEGE matches
    ``groups_include: [PRIVILEGE]`` even if no single profile contributed
    PRIVILEGE alone. Mirrors Shield's existing rollup behaviour.
    """
    policy = Policy.from_dict(canonical_dict)
    result = _mk_result(score=5, groups=["PRIVILEGE", "PHI", "PII"])
    decision = policy.evaluate(result)
    assert decision.rule_id == "privilege-stays-local"
    assert decision.backend_id == "ollama-local"


# ─── 15–18. Schema enforcement ──────────────────────────────────────────────


def test_transform_enum_only_accepts_known() -> None:
    """Unknown ``transform`` strings raise at load time."""
    with pytest.raises(PolicyError):
        Policy.from_dict(
            {
                "version": 1,
                "default_backend": "x",
                "rules": [
                    {
                        "id": "bad-transform",
                        "when": {"sensitivity_score_gte": 0},
                        "route": "x",
                        "transform": "encrypt_with_quantum",
                    },
                ],
            }
        )


def test_invalid_version_raises() -> None:
    """Only ``version: 1`` is accepted in v0.1 — explicit literal pin."""
    with pytest.raises(PolicyError):
        Policy.from_dict({"version": 2, "default_backend": "x", "rules": []})


def test_missing_default_backend_raises() -> None:
    with pytest.raises(PolicyError):
        Policy.from_dict({"version": 1, "rules": []})


def test_yaml_parse_error_wraps_in_policy_error(tmp_path: Path) -> None:
    """A malformed YAML file wraps in :class:`PolicyError`, not the raw YAML error."""
    bad = tmp_path / "bad.yaml"
    bad.write_text("version: 1\ndefault_backend: [unclosed-list\n")
    with pytest.raises(PolicyError) as exc_info:
        Policy.from_yaml(bad)
    assert "Invalid YAML" in str(exc_info.value) or "bad.yaml" in str(exc_info.value)


# ─── Bonus: file-not-found path coverage ────────────────────────────────────


def test_from_yaml_missing_file_raises_policy_error(tmp_path: Path) -> None:
    with pytest.raises(PolicyError) as exc_info:
        Policy.from_yaml(tmp_path / "does-not-exist.yaml")
    assert "Cannot read" in str(exc_info.value)
