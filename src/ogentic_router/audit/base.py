"""Audit primitives — the ``AuditSink`` protocol and the row schema (OGE-584).

The router records **that a routing decision was made and what shape it had** —
never the prompt content itself. A :class:`RouteDecisionAudit` row is emitted
once per :meth:`ogentic_router.Router.route` call, including on error paths, so a
compliance officer can grep a forensic record instead of trusting a marketing
claim.

Two load-bearing disciplines live here:

* **Shape-only.** The row carries a ``prompt_hash`` fingerprint, a sensitivity
  score, category labels, and the chosen backend — never the raw prompt. The
  contract is locked by ``tests/test_audit_privacy.py``.
* **Fire-and-forget.** ``AuditSink.emit`` MUST NOT raise. The audit is a
  recorder, not a gate — a misconfigured log must never crash the router. See
  :func:`ogentic_router.audit.sinks.safe_emit`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Protocol, runtime_checkable


class AuditUnavailableError(ImportError):
    """Raised when an audit backend's optional dependency isn't installed.

    Subclasses :class:`ImportError` so callers can catch it as either. Carries
    the canonical ``pip install 'ogentic-router[audit]'`` hint.
    """


@dataclass(frozen=True, slots=True)
class RouteDecisionAudit:
    """One shape-only audit row for a single routing decision.

    Additive-safe: new fields are appended with defaults so older readers keep
    parsing. Serialize with :meth:`to_dict` (JSON-ready) — never add a field
    that could carry raw prompt text.

    Fields:
        ts: ISO-8601 UTC timestamp, ``Z``-suffixed (e.g. ``2026-06-04T17:00:00Z``).
        request_id: HMAC-SHA256 hex digest of ``ts + prompt_hash`` under the
            deployment salt. Reproducible across restarts only when
            ``OGENTIC_ROUTER_AUDIT_SALT`` is set.
        prompt_hash: ``sha256:<hex>`` fingerprint from Shield's ``text_hash_for``.
        sensitivity_score: 0–100 score, or ``None`` on an error before classify.
        profile: The Shield profile that classified, if the result exposes one.
        top_category: Highest-signal category label, or ``None``.
        groups_found: Category groups (e.g. ``["PRIVILEGE"]``); empty on error.
        route_decision: Chosen backend id, or ``None`` on error.
        rule_id: The matched policy rule id, or ``None`` (default_backend / error).
        transform: ``"shield_redact"`` or ``None``.
        backend_is_local: Whether the chosen backend stays on-device; ``None``
            when it can't be determined.
        latency_ms: Wall-clock of the ``route()`` call in milliseconds.
        error: Exception **class name only** on an error path, else ``None``.
            Never the message text.
    """

    ts: str
    request_id: str
    prompt_hash: str
    sensitivity_score: int | None
    profile: str | None
    top_category: str | None
    groups_found: list[str] = field(default_factory=list)
    route_decision: str | None = None
    rule_id: str | None = None
    transform: str | None = None
    backend_is_local: bool | None = None
    latency_ms: float = 0.0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict of the row, field order preserved."""
        return asdict(self)


@runtime_checkable
class AuditSink(Protocol):
    """A destination for :class:`RouteDecisionAudit` rows.

    Implementations MUST treat :meth:`emit` as fire-and-forget: catch every
    internal failure, log at WARNING, and return — never raise into the caller.
    """

    def emit(self, row: RouteDecisionAudit) -> None:
        """Record ``row``. Must not raise."""
        ...


__all__ = ["AuditSink", "AuditUnavailableError", "RouteDecisionAudit"]
