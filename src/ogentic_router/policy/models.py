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

# Default per-call estimated-USD ceiling when a policy declares no explicit one.
# Generous by design: a normal prompt estimates well under a cent, so this never
# bites real usage — it's a fat-finger / runaway guard. Tune down per engagement.
DEFAULT_CEILING_USD: float = 1.0

# Category groups that must never resolve to a cloud backend by default — the
# fail-closed core of the privacy promise (OGE-1135). Enforcement is ON by
# default; the router refuses to dispatch content in any of these groups to a
# non-local backend, even if a rule (or a mis-ordered failover) would send it
# there. Legal privilege, protected health information, material non-public info.
DEFAULT_DENY_CLOUD_GROUPS: tuple[str, ...] = ("PRIVILEGE", "PHI", "MNPI")


def _check_group_names(value: list[str] | None) -> list[str] | None:
    """Reject unknown category-group names with a 'did you mean …?' hint.

    Pins the valid set to ``ogentic_shield.CategoryGroup`` at validation time —
    when Shield adds a new group the router accepts it with no code change. If
    Shield isn't installed the import fails loudly here, which is the right
    failure mode for a Shield-coupled DSL. Shared by every group-valued field.
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
        return _check_group_names(value)


class RuleSpec(BaseModel):
    """A single routing rule. First-match-wins evaluation order is preserved
    by the surrounding :class:`PolicySpec.rules` list.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    when: WhenClause
    route: str = Field(min_length=1)
    transform: Transform | None = None


class BudgetSpec(BaseModel):
    """Per-engagement cost ceiling for the policy.

    **Enforcement is ON by default** (CTO standing order, OGE-1120): a policy
    with no ``budget:`` block still enforces at :data:`DEFAULT_CEILING_USD`.
    The ceiling is a *per-call* estimated-USD cap — the router estimates the
    input cost of a prompt before it leaves the device and refuses the call if
    the estimate exceeds the ceiling (:class:`~ogentic_router.BudgetCeilingExceeded`).

    Opt out **per engagement** by setting ``enforce: false`` in that
    deployment's policy — the intended, explicit escape hatch.

    ```yaml
    budget:
      enforce: true        # default; set false to opt this engagement out
      ceiling_usd: 1.00     # default; per-call estimated-cost cap
    ```

    The default ceiling is deliberately generous — a single normal prompt
    estimates at fractions of a cent, so the default never bites real usage;
    it catches fat-finger / runaway mega-prompts and misconfigured batch jobs.
    Tune it down per engagement for tighter control.
    """

    model_config = ConfigDict(extra="forbid")

    enforce: bool = True
    ceiling_usd: float = Field(default=DEFAULT_CEILING_USD, gt=0)


class DenyCloudSpec(BaseModel):
    """Fail-closed guarantee: named category groups never resolve to cloud.

    **Enforcement is ON by default** (OGE-1135), which is the whole product
    thesis — content flagged as privilege / PHI / MNPI must never leave the
    device, even if a rule (or a mis-ordered failover) would route it to a cloud
    backend. The router treats a decision that sends a denied group to a
    non-local backend as a policy violation and raises
    :class:`~ogentic_router.CloudRouteDeniedError` **before** any dispatch.

    ```yaml
    deny_cloud:
      enforce: true                    # default; set false to opt out entirely
      groups: [PRIVILEGE, PHI, MNPI]    # default; the groups that must stay local
    ```

    This is a **backstop, not the routing itself** — a correct policy already
    routes these groups to a local backend, so the guard never fires in normal
    operation. It exists to catch misconfiguration (a rule that sends PHI to
    cloud, a reordered rule list) loudly instead of leaking silently.

    Opt out per engagement with ``enforce: false``, or narrow ``groups`` (e.g.
    drop ``PHI`` if your deployment routes de-identified PHI to cloud on
    purpose). Group names are validated against ``ogentic_shield.CategoryGroup``.
    """

    model_config = ConfigDict(extra="forbid")

    enforce: bool = True
    groups: list[str] = Field(default_factory=lambda: list(DEFAULT_DENY_CLOUD_GROUPS))

    @field_validator("groups")
    @classmethod
    def _validate_groups(cls, value: list[str]) -> list[str]:
        return _check_group_names(value) or []


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
    budget: BudgetSpec = Field(default_factory=BudgetSpec)
    deny_cloud: DenyCloudSpec = Field(default_factory=DenyCloudSpec)


__all__ = ["BudgetSpec", "DenyCloudSpec", "PolicySpec", "RuleSpec", "WhenClause", "Transform"]
