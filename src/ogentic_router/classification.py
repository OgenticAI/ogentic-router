"""``ShieldClassification`` — the Router's projection of Shield's ``AnalysisResult``.

This module defines the minimal frozen-dataclass projection Router uses to
hand Shield results into the Policy DSL. We could pass the raw
:class:`ogentic_shield.AnalysisResult` straight through (it already satisfies
the policy evaluator's duck-typed ``_ShieldResultLike`` Protocol), but a
dedicated projection buys us three things:

1. **A small public contract.** Shield's ``AnalysisResult`` has 12 fields, of
   which Router/Policy only read 4. Exposing the wider type to MCP/CLI
   consumers would lock us into Shield's surface forever; the projection is
   the contract we control.
2. **Stringly-typed groups.** Shield uses ``set[CategoryGroup]`` (enum). The
   Router-facing projection serialises the groups to ``frozenset[str]`` so
   MCP / JSON-RPC consumers can ship them across the wire without an
   enum-encoder, and so the audit row's stored shape is a stable string.
   Policy's ``_groups_set()`` accepts either form — no behaviour change.
3. **Additive-safe evolution.** ``frozen=True`` means equality + hash are
   defined; appending a new field in v0.2 (e.g. ``calibration_method:
   str | None = None``) won't break pickling, JSON round-trips, or existing
   consumers.

The ``text_hash`` field is sourced from Shield's
:func:`ogentic_shield.pipeline.text_hash_for` helper, *not* a hashlib call
inside the router. Keeping the audit-fingerprint algorithm centralised in
Shield is the org-wide convention — Router, Audit, and Shield all reference
the same fingerprint for cross-system forensic linking.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from .errors import ClassificationError, ShieldUnavailableError


@dataclass(frozen=True)
class ShieldClassification:
    """Frozen projection of Shield's ``AnalysisResult`` for the Router contract.

    Attributes:
        score: Calibrated sensitivity score, 0..100. Mirrors
            ``AnalysisResult.score`` verbatim.
        category_groups_found: Doc-level union of category groups detected
            across all profiles, projected to plain strings (e.g.
            ``{"PRIVILEGE", "PHI"}``). The Policy DSL's ``groups_include``
            / ``groups_exclude`` predicates key off this field.
        top_category: The dominant detected entity category (e.g.
            ``"LEGAL_PRIVILEGE"``), or ``None`` if no entities were
            detected. Forwarded from ``AnalysisResult.top_category``.
        entity_count: Number of distinct detected entities. Used by the
            MCP tool surface (OGE-586) and audit emit path for at-a-glance
            volume signal without forcing consumers to hold the entity
            list.
        text_hash: Stable audit fingerprint of the analysed text in
            ``"sha256:<16-hex-prefix>"`` form. Sourced from
            :func:`ogentic_shield.pipeline.text_hash_for` so Router, Shield,
            and Audit all reference the same fingerprint.

    Notes:
        * The frozen dataclass shape satisfies the policy evaluator's
          duck-typed ``_ShieldResultLike`` Protocol on the fields it reads
          (``score``, ``category_groups_found``, ``top_category``). It
          intentionally does NOT carry the full entity list — the
          ``entities`` field on Policy's Protocol is only used by the
          ``category_in`` / ``category_not_in`` predicates, and the Router
          attaches the raw entity list out-of-band in
          :meth:`~ogentic_router.Router.route` so those predicates still
          fire correctly. See :class:`~ogentic_router.Router` for the
          wiring detail.
    """

    score: int
    category_groups_found: frozenset[str]
    top_category: str | None
    entity_count: int
    text_hash: str

    def to_dict(self) -> dict[str, Any]:
        """Serialise to JSON-friendly primitives.

        ``frozenset`` is unwrapped to a sorted ``list[str]`` so the result
        round-trips through ``json.dumps`` unchanged and produces a stable
        ordering — important for the MCP tool surface (OGE-586) which
        ships these payloads to LLM agents that benefit from deterministic
        output.
        """
        return {
            "score": self.score,
            "category_groups_found": sorted(self.category_groups_found),
            "top_category": self.top_category,
            "entity_count": self.entity_count,
            "text_hash": self.text_hash,
        }


def analysis_from_json(doc: Any) -> SimpleNamespace:
    """Rebuild the policy-relevant slice of a Shield analysis from its JSON form.

    ``doc`` is the parsed document ``ogentic-shield analyze --output json``
    prints (0.6.x): ``score``, ``entities[]`` (each with ``category`` and
    ``category_group``; ``confidence`` optional), and optionally ``text_hash``
    and ``profiles_active``. Other keys (``sensitivity_level``,
    ``routing_suggestion``, ...) are ignored; the policy makes the decision.

    ``category_groups_found`` and ``top_category`` are derived from the entities
    exactly as Shield's pipeline derives them, so :meth:`Router.route_analysis`
    reaches the same decision :meth:`Router.route` would for the same analysis.
    Each entity's matched ``text`` is never read or kept; callers may drop it.

    Raises:
        ClassificationError: the document is not a well-formed Shield analysis
            (the message names the field), or names an unknown category group.
        ShieldUnavailableError: ``ogentic-shield`` is not installed, so group
            names cannot be checked.
    """
    if not isinstance(doc, dict):
        raise ClassificationError(f"expected a JSON object, got {type(doc).__name__}")
    score = doc.get("score")
    if isinstance(score, bool) or not isinstance(score, int) or not 0 <= score <= 100:
        raise ClassificationError(f"'score' must be an integer 0..100, got {score!r}")
    raw_entities = doc.get("entities")
    if not isinstance(raw_entities, list):
        raise ClassificationError(f"'entities' must be a list, got {raw_entities!r}")

    entities: list[SimpleNamespace] = []
    for i, raw in enumerate(raw_entities):
        if not isinstance(raw, dict):
            raise ClassificationError(f"entities[{i}] must be an object")
        category, group = raw.get("category"), raw.get("category_group")
        confidence = raw.get("confidence", 0.0)
        if not isinstance(category, str) or not category:
            raise ClassificationError(f"entities[{i}].category must be a non-empty string")
        if not isinstance(group, str) or not group:
            raise ClassificationError(f"entities[{i}].category_group must be a non-empty string")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            raise ClassificationError(f"entities[{i}].confidence must be a number")
        entities.append(
            SimpleNamespace(category=category, category_group=group, confidence=float(confidence))
        )

    groups = frozenset(e.category_group for e in entities)
    # Fail closed on an unknown group name: a typo like "phi" must not slip
    # past groups_include / deny_cloud by silently matching nothing.
    from .policy.models import _check_group_names  # noqa: PLC0415

    try:
        _check_group_names(sorted(groups))
    except ImportError as exc:
        raise ShieldUnavailableError(
            "ogentic-shield is needed to validate category groups. Install the "
            "[shield] extra: `pip install 'ogentic-router[shield]'`."
        ) from exc
    except ValueError as exc:
        raise ClassificationError(f"entities: {exc}") from exc

    text_hash = doc.get("text_hash", "")
    if not isinstance(text_hash, str):
        raise ClassificationError("'text_hash' must be a string")
    profiles = doc.get("profiles_active", [])
    if not isinstance(profiles, list) or not all(isinstance(p, str) for p in profiles):
        raise ClassificationError("'profiles_active' must be a list of strings")

    # Shield's top_category: the highest-confidence entity, first one on a tie.
    top = max(entities, key=lambda e: e.confidence) if entities else None
    return SimpleNamespace(
        score=score,
        category_groups_found=groups,
        entities=entities,
        top_category=top.category if top else None,
        entity_count=len(entities),
        text_hash=text_hash,
        profile_ids=profiles,
    )


__all__ = ["ShieldClassification", "analysis_from_json"]
