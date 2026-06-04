"""Pydantic v2 validation models for the policy DSL.

These models *are* the schema. Every public field is typed, every model has
``extra="forbid"`` so unknown YAML keys raise with a clear path, and every
constraint (score ranges, non-empty lists, known category names) lives in
the model layer — the runtime evaluator in :mod:`.policy` trusts whatever
gets past this gate.

The ``CategoryGroup`` enum is imported live from ``ogentic_shield`` rather
than hardcoded — drift between Shield (which periodically adds categories,
e.g. ``THERAPY_PRO`` in OGE-355) and Router was a known failure mode in
the design phase.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Transform(str, Enum):
    """Pre-flight transforms applied to a prompt before it crosses to the chosen backend.

    v0.1 ships exactly one value — ``SHIELD_REDACT`` (run ``Shield.redact``
    on the prompt before forwarding). Adding new values is additive and
    safe; the dataclass-shaped :class:`~.decision.RouteDecision` carries
    ``transform: Transform | None``.
    """

    SHIELD_REDACT = "shield_redact"


class WhenClause(BaseModel):
    """Predicate set for a single rule. AND-of-set semantics: every non-None
    predicate must be satisfied for the rule to match.

    Predicates supported in v0.1:

    - ``groups_include`` / ``groups_exclude`` — match the doc-level union of
      ``AnalysisResult.category_groups_found`` (mirrors Shield's existing
      rollup; matches even if no single profile contributed the group alone).
    - ``sensitivity_score_gte`` / ``sensitivity_score_lt`` — inclusive lower
      bound / exclusive upper bound on ``AnalysisResult.score`` (0–100).
    - ``category_in`` / ``category_not_in`` — match against any entity's
      ``category`` (not just ``top_category``). Covers the *"route based on
      the presence of any Y"* case, not just the dominant signal.

    Empty list values are rejected at validation (``min_length=1``) — silent
    "always-match" / "always-skip" semantics on operator typo would be a
    foot-gun.
    """

    model_config = ConfigDict(extra="forbid")

    groups_include: list[str] | None = Field(default=None, min_length=1)
    groups_exclude: list[str] | None = Field(default=None, min_length=1)
    sensitivity_score_gte: int | None = Field(default=None, ge=0, le=100)
    sensitivity_score_lt: int | None = Field(default=None, ge=1, le=101)
    category_in: list[str] | None = Field(default=None, min_length=1)
    category_not_in: list[str] | None = Field(default=None, min_length=1)

    @field_validator("groups_include", "groups_exclude")
    @classmethod
    def _validate_groups_against_shield(cls, value: list[str] | None) -> list[str] | None:
        """Reject unknown category-group names with a 'did you mean ...?' hint.

        Pins the valid set to ``ogentic_shield.CategoryGroup`` at validation
        time — when Shield adds a new group, the router accepts it
        immediately without any code change. If Shield isn't installed at
        all the import fails loudly here, which is exactly the right
        failure mode for a Shield-coupled DSL.
        """
        if value is None:
            return value
        from ogentic_shield import CategoryGroup  # noqa: PLC0415 — lazy by design

        valid = {g.value for g in CategoryGroup}
        unknown = [name for name in value if name not in valid]
        if unknown:
            from ._suggestions import suggest  # noqa: PLC0415

            hint = suggest(unknown[0], valid)
            msg = f"Unknown category group {unknown[0]!r}"
            if hint is not None:
                msg += f". Did you mean {hint!r}?"
            msg += f" Known groups: {', '.join(sorted(valid))}"
            raise ValueError(msg)
        return value


class RuleSpec(BaseModel):
    """A single routing rule. First-match-wins evaluation order is preserved
    by the surrounding :class:`PolicySpec.rules` list.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    when: WhenClause
    route: str = Field(min_length=1)
    transform: Transform | None = None


class PolicySpec(BaseModel):
    """The serialised form of a policy file.

    ``version: Literal[1]`` pins the schema version explicitly — a future v2
    will widen to ``Literal[1, 2]`` and the load path will branch on the
    discriminator. Keep this strict; silent version drift is exactly the
    kind of failure mode that bites in production years later.
    """

    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    default_backend: str = Field(min_length=1)
    rules: list[RuleSpec] = Field(default_factory=list)


__all__ = ["PolicySpec", "RuleSpec", "WhenClause", "Transform"]
