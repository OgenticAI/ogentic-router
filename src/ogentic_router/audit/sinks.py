"""Audit sinks — where :class:`RouteDecisionAudit` rows land (OGE-584).

Three sinks ship in v0.1:

* :class:`NoopSink` — the default. Drops every row. Zero behaviour change.
* :class:`LocalFileSink` — appends one JSON line per row, ``fsync`` per write,
  cross-platform file lock. The default *non-Noop* sink.
* :class:`OgenticAuditSink` — forward-compat hook for the ``ogentic-audit``
  HMAC-chained log. Raises at construction until that library is on PyPI.

Every sink's ``emit`` is wrapped so a sink-internal failure (disk full, lock
timeout) is logged at WARNING and swallowed — the routing decision proceeds
regardless. See :func:`safe_emit`.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from .base import AuditSink, AuditUnavailableError, RouteDecisionAudit

logger = logging.getLogger("ogentic_router.audit")


def safe_emit(sink: AuditSink, row: RouteDecisionAudit) -> None:
    """Emit ``row`` to ``sink``, swallowing (and logging) any failure.

    This is the discipline the router uses on the request path: the audit is a
    recorder, not a gate. A misconfigured or full log must never crash the
    router — mirrors ``ogentic-shield``'s ``safe_emit``.
    """
    try:
        sink.emit(row)
    except Exception as exc:  # noqa: BLE001 — fire-and-forget by contract
        logger.warning(
            "audit sink %s dropped a row: %s: %s",
            type(sink).__name__,
            type(exc).__name__,
            exc,
        )


class NoopSink:
    """Drops every row. The default sink — no file output, never raises."""

    def emit(self, row: RouteDecisionAudit) -> None:  # noqa: D102 - see AuditSink
        return None


class LocalFileSink:
    """Append one JSON line per row to a file, durably and lock-safe.

    Each :meth:`emit` acquires a cross-platform advisory lock (``filelock``),
    appends ``json.dumps(row) + "\\n"``, flushes, and ``fsync``s before
    releasing — so two processes writing the same path never interleave rows
    and a row that returns is on disk. ``~`` in the path is expanded.

    Construction is cheap; the lock file lives beside the log as ``<path>.lock``.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(os.path.expanduser(str(path)))
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock_path = self._path.with_suffix(self._path.suffix + ".lock")
        try:
            from filelock import FileLock  # noqa: PLC0415 - optional-but-base dep
        except ImportError as exc:  # pragma: no cover - filelock is a base dependency
            raise AuditUnavailableError(
                "LocalFileSink needs 'filelock'. It ships with ogentic-router; "
                "reinstall with `pip install ogentic-router` if it's missing."
            ) from exc
        # 10s is generous — the critical section is a single appended line.
        self._lock = FileLock(str(self._lock_path), timeout=10)

    @property
    def path(self) -> Path:
        """The resolved, ``~``-expanded log path."""
        return self._path

    def emit(self, row: RouteDecisionAudit) -> None:
        """Append ``row`` as one JSON line, fsync'd, under an exclusive lock."""
        line = json.dumps(row.to_dict(), ensure_ascii=False) + "\n"
        with self._lock:
            with open(self._path, "a", encoding="utf-8") as fh:
                fh.write(line)
                fh.flush()
                os.fsync(fh.fileno())


class OgenticAuditSink:
    """Forward-compat sink for the ``ogentic-audit`` HMAC-chained log.

    ``ogentic-audit`` is an alpha OSS project that is **not yet on PyPI**. This
    sink lazy-imports ``ogentic_audit.Writer`` at construction; until the
    library publishes, that import fails and construction raises
    :class:`AuditUnavailableError` with the install hint. The class exists now
    so the import-error UX is locked and the real adapter has a home in v0.2.
    """

    def __init__(self, writer_config: dict[str, Any] | None = None) -> None:
        try:
            from ogentic_audit import Writer  # noqa: PLC0415 - optional extra
        except ImportError as exc:
            raise AuditUnavailableError(
                "ogentic-audit is not installed (it is not yet published to "
                "PyPI). Install the [audit] extra once available with "
                "`pip install 'ogentic-router[audit]'`."
            ) from exc
        self._writer = Writer(**(writer_config or {}))

    def emit(self, row: RouteDecisionAudit) -> None:  # pragma: no cover - lib not on PyPI
        """Append ``row`` to the ``ogentic-audit`` writer (which HMAC-chains it)."""
        self._writer.append(row.to_dict())


__all__ = ["LocalFileSink", "NoopSink", "OgenticAuditSink", "safe_emit"]
