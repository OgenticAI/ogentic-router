"""The decision dataclass — what every call to ``Policy.evaluate()`` returns.

Frozen dataclass with all-explicit-Optional fields. The *additive-safe* shape
matters: when ``ogentic-audit``'s approval-gate feature (OGE-273) lands in
v0.2, adding ``approval_required: bool = False`` to this class doesn't
break pickling, equality, or JSON round-trips for existing consumers.

``RouteDecision`` is part of the public API surface and is re-exported from
the top-level package.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .models import Transform


@dataclass(frozen=True)
class RouteDecision:
    """The verdict of ``Policy.evaluate(shield_result)``.

    Attributes:
        backend_id: The ``route`` of the matched rule, or the policy's
            ``default_backend`` if no rule matched. Consumers (adapters,
            server, audit writer) key off this string to pick a backend.
        rule_id: The ``id`` of the matched rule, or ``None`` when
            ``default_backend`` fired. Audit rows carry this for
            forensic attribution.
        transform: Pre-flight transform to apply to the prompt before
            forwarding to the backend (e.g. ``Transform.SHIELD_REDACT``).
            ``None`` means "send as-is".
        reasoning: Human-readable explanation of why this verdict was
            chosen. Goes into the audit row's debug field; helpful for
            "why did this prompt route to cloud?" forensics.
    """

    backend_id: str
    rule_id: str | None
    transform: Transform | None
    reasoning: str

    def to_dict(self) -> dict[str, Any]:
        """Serialise to JSON-friendly primitives.

        ``Transform`` (a ``str`` enum) is unwrapped to its string value so
        the result round-trips through ``json.dumps`` unchanged.
        """
        out = asdict(self)
        out["transform"] = self.transform.value if self.transform is not None else None
        return out


__all__ = ["RouteDecision"]
