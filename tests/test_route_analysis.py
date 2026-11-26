"""Routing from an existing Shield analysis: ``Router.route_analysis`` +
``analysis_from_json`` (the ``route --classification`` path).

The load-bearing property: for the same Shield analysis, deciding from Shield's
JSON output gives exactly the decision the Shield-run path (``Router.route``)
gives. Proved on ``fixtures/shield_0.6.1_analysis.json``, recorded verbatim from
``ogentic-shield analyze --output json`` (0.6.1) on ``SAMPLE`` below.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from ogentic_router import CloudRouteDeniedError, Policy, Router
from ogentic_router.audit import RouteDecisionAudit
from ogentic_router.classification import analysis_from_json
from ogentic_router.errors import ClassificationError

FIXTURE = Path(__file__).parent / "fixtures" / "shield_0.6.1_analysis.json"
CANONICAL_POLICY = Path(__file__).parent.parent / "examples" / "policy.yaml"

# Synthetic text the fixture was recorded from (no real person or matter).
SAMPLE = (
    "ATTORNEY-CLIENT PRIVILEGED MEMO. From counsel to John Smith, SSN "
    "123-45-6789, regarding the pending M&A transaction with Acme Corp. "
    "The patient was diagnosed with type 2 diabetes."
)


def _doc() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _shield_result(doc: dict[str, Any]) -> Any:
    """The ``AnalysisResult`` Shield returned for the recorded run, rebuilt with
    Shield's own types (enum groups, ``DetectedEntity``) — what ``Router.route``
    receives from ``Shield.analyze``."""
    from ogentic_shield import (
        AnalysisResult,
        CategoryGroup,
        DetectedEntity,
        DetectionLayer,
        SensitivityLevel,
    )

    entities = [
        DetectedEntity(
            text=e["text"],
            category=e["category"],
            category_group=CategoryGroup(e["category_group"]),
            confidence=e["confidence"],
            detection_layer=DetectionLayer(e["detection_layer"]),
            start=e["start"],
            end=e["end"],
        )
        for e in doc["entities"]
    ]
    top = max(entities, key=lambda e: e.confidence) if entities else None
    return AnalysisResult(
        text_hash=doc["text_hash"],
        entities=entities,
        score=doc["score"],
        sensitivity_level=SensitivityLevel(doc["sensitivity_level"]),
        category_groups_found={e.category_group for e in entities},
        top_category=top.category if top else None,
        top_confidence=top.confidence if top else 0.0,
        entity_count=doc["entity_count"],
        processing_time_ms=doc["processing_time_ms"],
        layers_invoked=[DetectionLayer(x) for x in doc["layers_invoked"]],
        profile_ids=doc["profiles_active"],
        routing_suggestion=doc["routing_suggestion"],
    )


class _NoShield:
    def analyze(self, _text: str) -> Any:
        raise AssertionError("Shield must not run on the route_analysis path")


def _policies(doc: dict[str, Any]) -> list[Policy]:
    """One policy per predicate, each in a matching and a non-matching variant,
    plus the canonical example policy."""
    from ogentic_shield import CategoryGroup

    groups = sorted({e["category_group"] for e in doc["entities"]})
    absent_group = next(g.value for g in CategoryGroup if g.value not in groups)
    score = doc["score"]
    category = doc["entities"][0]["category"]
    whens: list[dict[str, Any]] = [
        {"groups_include": [groups[0]]},
        {"groups_include": [absent_group]},
        {"sensitivity_score_gte": score},
        {"sensitivity_score_gte": min(score + 1, 100)},
        {"category_in": [category]},
        {"category_in": ["NOT_A_CATEGORY"]},
    ]
    out = [
        Policy.from_dict({
            "version": 1,
            "default_backend": "fallback-local",
            "rules": [{"id": "hit", "when": when, "route": "matched-local"}],
        })
        for when in whens
    ]
    return [*out, Policy.from_yaml(CANONICAL_POLICY)]


def test_fixture_was_recorded_from_sample() -> None:
    from ogentic_shield.pipeline import text_hash_for

    doc = _doc()
    assert doc["text_hash"] == text_hash_for(SAMPLE)
    assert doc["entities"], "fixture must exercise entity-based predicates"


def test_decision_matches_shield_run_path_for_every_predicate() -> None:
    doc = _doc()
    policies = _policies(doc)
    shield_result = _shield_result(doc)
    stub = SimpleNamespace(analyze=lambda _text: shield_result)
    hits = 0
    for policy in policies:
        via_shield = Router(policy, shield=stub).route(SAMPLE, budget_ceiling=None)
        via_json = Router(policy, shield=_NoShield()).route_analysis(analysis_from_json(doc))
        assert via_json == via_shield, policy.to_dict()
        hits += via_json.rule_id is not None
    # Sanity: the variants really split between match and no-match.
    assert 0 < hits < len(policies)


@pytest.mark.integration
@pytest.mark.skipif(
    not os.environ.get("OGENTIC_ROUTER_SHIELD_INTEGRATION"),
    reason="Set OGENTIC_ROUTER_SHIELD_INTEGRATION=1 to exercise the real Shield.",
)
def test_live_shield_json_matches_route() -> None:
    """Same equivalence against a live Shield run, through Shield's own CLI formatter."""
    from ogentic_shield import Shield
    from ogentic_shield.cli.formatters import format_json

    shield = Shield()
    doc = json.loads(format_json(shield.analyze(SAMPLE)))
    for policy in _policies(_doc()):
        via_shield = Router(policy, shield=shield).route(SAMPLE, budget_ceiling=None)
        via_json = Router(policy, shield=_NoShield()).route_analysis(analysis_from_json(doc))
        assert via_json == via_shield, policy.to_dict()


def test_route_analysis_still_fails_closed_on_cloud() -> None:
    policy = Policy.from_dict({"version": 1, "default_backend": "openai-cloud"})
    with pytest.raises(CloudRouteDeniedError):
        Router(policy, shield=_NoShield()).route_analysis(analysis_from_json(_doc()))


def test_route_analysis_audit_row_is_shape_only() -> None:
    rows: list[RouteDecisionAudit] = []
    sink = SimpleNamespace(emit=rows.append)
    router = Router(Policy.from_yaml(CANONICAL_POLICY), shield=_NoShield(), audit_sink=sink)
    doc = _doc()
    router.route_analysis(analysis_from_json(doc))
    assert len(rows) == 1
    row = json.dumps(rows[0].to_dict())
    assert doc["text_hash"] in row
    for entity in doc["entities"]:
        assert entity["text"] not in row


def test_entity_text_is_not_needed() -> None:
    doc = _doc()
    for e in doc["entities"]:
        del e["text"]
    policy = Policy.from_yaml(CANONICAL_POLICY)
    full = Router(policy, shield=_NoShield()).route_analysis(analysis_from_json(_doc()))
    stripped = Router(policy, shield=_NoShield()).route_analysis(analysis_from_json(doc))
    assert stripped == full


def _mutated(path: str, value: Any) -> Any:
    doc = _doc()
    if path == "<root>":
        return value
    keys: list[Any] = [int(k) if k.isdigit() else k for k in path.split(".")]
    target: Any = doc
    for key in keys[:-1]:
        target = target[key]
    if value is _DELETE:
        del target[keys[-1]]
    else:
        target[keys[-1]] = value
    return doc


_DELETE = object()


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        ("<root>", [], "JSON object"),
        ("score", _DELETE, "score"),
        ("score", "85", "score"),
        ("score", True, "score"),
        ("score", 101, "score"),
        ("entities", _DELETE, "entities"),
        ("entities", {}, "entities"),
        ("entities.0", "x", "entities[0]"),
        ("entities.0.category", _DELETE, "entities[0].category"),
        ("entities.0.category_group", _DELETE, "entities[0].category_group"),
        ("entities.0.category_group", "phi", "Unknown category group"),
        ("entities.0.confidence", "high", "entities[0].confidence"),
        ("text_hash", 7, "text_hash"),
        ("profiles_active", "shield-legal", "profiles_active"),
    ],
)
def test_malformed_analysis_is_rejected(path: str, value: Any, message: str) -> None:
    with pytest.raises(ClassificationError, match=re.escape(message)):
        analysis_from_json(_mutated(path, value))


def test_empty_analysis_routes_to_default() -> None:
    doc = {"score": 0, "entities": []}
    policy = Policy.from_dict({"version": 1, "default_backend": "ollama-local"})
    decision = Router(policy, shield=_NoShield()).route_analysis(analysis_from_json(doc))
    assert decision.backend_id == "ollama-local" and decision.rule_id is None
