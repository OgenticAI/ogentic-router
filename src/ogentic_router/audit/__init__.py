"""Audit subpackage — shape-only routing-decision records (OGE-584).

Public surface::

    from ogentic_router.audit import (
        AuditSink, RouteDecisionAudit, AuditUnavailableError,
        NoopSink, LocalFileSink, OgenticAuditSink,
    )

See :mod:`ogentic_router.audit.base` for the row schema and the fire-and-forget
contract, and :mod:`ogentic_router.audit.sinks` for the three shipped sinks.
"""

from __future__ import annotations

from typing import Any

from .base import AuditSink, AuditUnavailableError, RouteDecisionAudit
from .sinks import LocalFileSink, NoopSink, OgenticAuditSink, safe_emit

__all__ = [
    "AuditSink",
    "AuditUnavailableError",
    "LocalFileSink",
    "NoopSink",
    "OgenticAuditSink",
    "RouteDecisionAudit",
    "safe_emit",
]


def sink_from_config(audit_cfg: dict[str, Any] | None) -> AuditSink:
    """Build a sink from a ``router.yaml`` ``audit:`` block.

    Recognized shapes::

        None / {}                          -> NoopSink()
        {"sink": "noop"}                   -> NoopSink()
        {"sink": "local_file", "path": p}  -> LocalFileSink(p)
        {"sink": "ogentic_audit",
         "writer_config": {...}}           -> OgenticAuditSink(writer_config)

    Raises:
        ValueError: on an unknown ``sink`` value or a missing required key.
    """
    if not audit_cfg:
        return NoopSink()
    kind = audit_cfg.get("sink", "noop")
    if kind == "noop":
        return NoopSink()
    if kind == "local_file":
        path = audit_cfg.get("path")
        if not path:
            raise ValueError("audit.sink 'local_file' requires a 'path'.")
        return LocalFileSink(path)
    if kind == "ogentic_audit":
        return OgenticAuditSink(audit_cfg.get("writer_config"))
    raise ValueError(
        f"unknown audit.sink {kind!r} "
        "(expected 'noop', 'local_file', or 'ogentic_audit')."
    )
