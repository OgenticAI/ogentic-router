"""The ``Policy`` class — load a YAML policy file, evaluate against a Shield
result, return a :class:`~.decision.RouteDecision`.

This module is the small layer between the Pydantic validation models and
the rest of the router. The models do the typing; this file does the
control-flow: load → validate → evaluate → reason-text.

Duck-typing on ``shield_result``: the evaluator reads ``.score: int``,
``.category_groups_found: set | Iterable``, ``.entities: list`` with
``.category`` attribute, and (optionally) ``.top_category: str | None``.
Tests use ``types.SimpleNamespace`` mocks; no Presidio cold-start cost in
the unit-test path.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import yaml
from pydantic import ValidationError

from .decision import RouteDecision
from .errors import PolicyError
from .models import (
    BudgetSpec,
    PolicySpec,
    RuleSpec,
    Transform,  # noqa: F401 — re-exported via __all__ for ``from ogentic_router.policy import Transform``
    WhenClause,
)

# ─── Duck-typed inputs ──────────────────────────────────────────────────────


@runtime_checkable
class _EntityLike(Protocol):
    """The slice of :class:`ogentic_shield.DetectedEntity` we read."""

    category: str


@runtime_checkable
class _ShieldResultLike(Protocol):
    """The slice of :class:`ogentic_shield.AnalysisResult` we read.

    Declared as a Protocol so we can document the contract without paying
    the spaCy / Presidio import cost in tests. Real consumers pass an
    ``AnalysisResult`` instance — duck-typing is just for the unit tests.
    """

    score: int
    category_groups_found: Any  # set[CategoryGroup] or Iterable[str]
    entities: list[_EntityLike]
    top_category: str | None


# ─── The Policy class ───────────────────────────────────────────────────────


class Policy:
    """Loaded, validated routing policy. Immutable after construction.

    Construct via :meth:`from_yaml` or :meth:`from_dict`. The bare
    ``Policy(spec)`` constructor is reserved for the loader path; callers
    should not instantiate directly.
    """

    __slots__ = ("_spec",)

    def __init__(self, spec: PolicySpec) -> None:
        self._spec = spec

    # ── Constructors ────────────────────────────────────────────────────

    @classmethod
    def from_yaml(cls, path: str | Path) -> Policy:
        """Load + validate a policy from a YAML file on disk.

        Raises:
            PolicyError: file is unreadable, YAML is malformed, or the
                document fails schema validation. The exception message
                names the path and points at the offending field.
        """
        p = Path(path)
        try:
            text = p.read_text(encoding="utf-8")
        except OSError as exc:
            raise PolicyError(f"Cannot read policy file {str(p)!r}: {exc}") from exc

        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise PolicyError(f"Invalid YAML in {str(p)!r}: {exc}") from exc

        if not isinstance(data, dict):
            raise PolicyError(
                f"Policy file {str(p)!r} must contain a YAML mapping at the top level, "
                f"got {type(data).__name__}",
            )

        try:
            return cls.from_dict(data)
        except PolicyError as exc:
            # Re-wrap so the path shows up in the message.
            raise PolicyError(f"In {str(p)!r}: {exc}") from exc.__cause__ or exc

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Policy:
        """Load + validate a policy from an in-memory dict.

        Useful for tests, for programmatic policy construction, and as the
        underpinning of :meth:`from_yaml`. Same error contract as
        :meth:`from_yaml` minus the file-IO failure modes.
        """
        try:
            spec = PolicySpec.model_validate(data)
        except ValidationError as exc:
            raise PolicyError(_format_validation_error(exc)) from exc
        return cls(spec)

    # ── Serialisation ───────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """Round-trip-safe serialisation.

        Postcondition (locked by ``test_to_dict_round_trips_canonical``):
        ``Policy.from_dict(p.to_dict()).to_dict() == p.to_dict()`` for
        every valid policy.

        ``exclude_none=True`` keeps optional fields ergonomic — a rule
        without a ``transform`` serialises as just
        ``{"id": ..., "when": {...}, "route": ...}`` rather than
        ``{..., "transform": null}``.
        """
        return self._spec.model_dump(exclude_none=True, mode="json")

    # ── Evaluation ──────────────────────────────────────────────────────

    @property
    def default_backend(self) -> str:
        """The fallback backend id, returned when no rule matches."""
        return self._spec.default_backend

    @property
    def rules(self) -> tuple[RuleSpec, ...]:
        """Rules in declared order (first-match-wins).

        Returned as a tuple so callers can't accidentally mutate the
        policy's internal state.
        """
        return tuple(self._spec.rules)

    @property
    def budget(self) -> BudgetSpec:
        """The policy's per-call cost ceiling (enforcement ON by default)."""
        return self._spec.budget

    def effective_ceiling(self) -> float | None:
        """The per-call USD ceiling to enforce, or ``None`` if opted out.

        Returns ``budget.ceiling_usd`` when ``budget.enforce`` is true (the
        default), else ``None``. This is what :meth:`Router.route` consults
        when the caller doesn't pass an explicit ``budget_ceiling``.
        """
        return self._spec.budget.ceiling_usd if self._spec.budget.enforce else None

    def evaluate(self, shield_result: Any) -> RouteDecision:
        """Pick a backend for ``shield_result`` per the loaded rules.

        First-match-wins. If no rule matches, returns a decision pointing
        at ``self.default_backend``.

        ``shield_result`` is duck-typed (see :class:`_ShieldResultLike`).
        We avoid an isinstance check so test mocks don't have to pull in
        the full Shield dependency.
        """
        for rule in self._spec.rules:
            if _matches(rule.when, shield_result):
                return RouteDecision(
                    backend_id=rule.route,
                    rule_id=rule.id,
                    transform=rule.transform,
                    reasoning=(
                        f"matched rule {rule.id!r}: "
                        f"{_reason_for_match(rule.when, shield_result)}"
                    ),
                )
        return RouteDecision(
            backend_id=self._spec.default_backend,
            rule_id=None,
            transform=None,
            reasoning="no rule matched; default_backend fired",
        )


# ─── Internal: predicate evaluation ─────────────────────────────────────────


def _matches(when: WhenClause, sr: Any) -> bool:
    """True iff every set (non-None) predicate is satisfied for ``sr``.

    AND-of-set semantics. An empty ``WhenClause`` (every predicate None)
    is the always-true case — by intent: it lets a rule act as a catch-all
    *before* the policy's ``default_backend`` if the operator wants more
    nuanced fallback ("everything else goes to a third backend").
    """
    if when.groups_include is not None:
        if not _groups_set(sr).intersection(when.groups_include):
            return False
    if when.groups_exclude is not None:
        if _groups_set(sr).intersection(when.groups_exclude):
            return False
    if when.sensitivity_score_gte is not None:
        if int(getattr(sr, "score", 0)) < when.sensitivity_score_gte:
            return False
    if when.sensitivity_score_lt is not None:
        if int(getattr(sr, "score", 0)) >= when.sensitivity_score_lt:
            return False
    if when.category_in is not None:
        if not _entity_categories(sr).intersection(when.category_in):
            return False
    if when.category_not_in is not None:
        if _entity_categories(sr).intersection(when.category_not_in):
            return False
    return True


def _groups_set(sr: Any) -> set[str]:
    """Project ``shield_result.category_groups_found`` to a ``set[str]``.

    Handles both the canonical ``set[CategoryGroup]`` (enum members carry
    ``.value`` for the string projection) and the mock-friendly
    ``set[str]`` shape tests sometimes use.
    """
    raw = getattr(sr, "category_groups_found", None) or set()
    out: set[str] = set()
    for item in raw:
        # Enum members expose ``.value``; bare strings pass through.
        value = getattr(item, "value", item)
        out.add(str(value))
    return out


def _entity_categories(sr: Any) -> set[str]:
    """All ``entity.category`` strings on the result.

    Iterates every entity rather than just ``top_category`` — the v0.1
    design choice documented in the spec brief, §7 "load-bearing
    decisions". Covers *"route on the presence of any Y"* not just the
    dominant signal.
    """
    entities = getattr(sr, "entities", None) or []
    return {str(getattr(e, "category", "")) for e in entities if getattr(e, "category", None)}


def _reason_for_match(when: WhenClause, sr: Any) -> str:
    """Human-readable explanation of which predicate(s) fired.

    Goes into ``RouteDecision.reasoning`` and from there into the audit
    row. Optimised for forensics: an operator reading "why did this
    prompt route to cloud?" should be able to answer without reading the
    code.
    """
    reasons: list[str] = []
    if when.groups_include is not None:
        hits = sorted(_groups_set(sr).intersection(when.groups_include))
        reasons.append(f"groups_include matched {hits}")
    if when.groups_exclude is not None:
        # Match-on-rule means none of the excluded groups were present.
        reasons.append(f"groups_exclude not triggered (none of {sorted(when.groups_exclude)} present)")
    if when.sensitivity_score_gte is not None:
        reasons.append(f"score {getattr(sr, 'score', 0)} ≥ {when.sensitivity_score_gte}")
    if when.sensitivity_score_lt is not None:
        reasons.append(f"score {getattr(sr, 'score', 0)} < {when.sensitivity_score_lt}")
    if when.category_in is not None:
        hits = sorted(_entity_categories(sr).intersection(when.category_in))
        reasons.append(f"category_in matched {hits}")
    if when.category_not_in is not None:
        reasons.append(
            f"category_not_in not triggered (none of {sorted(when.category_not_in)} present)",
        )
    if not reasons:
        # Empty WhenClause — catch-all rule.
        reasons.append("empty when-clause (catch-all)")
    return "; ".join(reasons)


# ─── Internal: format validation errors ─────────────────────────────────────


def _format_validation_error(exc: ValidationError) -> str:
    """Turn a pydantic ``ValidationError`` into a one-paragraph operator-facing message.

    Lists every error with its JSON-path (``rules → 0 → when →
    groups_include``) and the underlying message. JSON-path is the v0.1
    compromise; raw YAML line numbers need a ``ruamel.yaml`` migration
    flagged for v0.2 in the spec brief.
    """
    lines: list[str] = ["Policy validation failed:"]
    for err in exc.errors():
        loc = " → ".join(str(p) for p in err.get("loc", ())) or "<root>"
        message = err.get("msg", "invalid value")
        lines.append(f"  - {loc}: {message}")
    return "\n".join(lines)


# Re-export Transform here so callers can ``from ogentic_router.policy import Transform``
# without reaching into the models submodule.
__all__ = ["Policy", "Transform"]
