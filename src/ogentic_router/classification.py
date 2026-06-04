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
from typing import Any


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


__all__ = ["ShieldClassification"]
