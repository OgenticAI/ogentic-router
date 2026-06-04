"""Router-level exception types (OGE-580).

Two-class hierarchy on purpose. ``RouterError`` is the catch-all base — higher
layers (the OGE-583 server, OGE-585 CLI, OGE-586 MCP tool surface) should be
able to ``except RouterError`` to catch every failure mode of the router
construction / runtime path.

``ShieldUnavailableError`` additionally subclasses :class:`ImportError` so
callers using the standard ``except ImportError`` pattern for optional-extra
handling also catch it. That dual-inheritance is the v0.1 ergonomics call: it
matches the way Python typically signals "you didn't install the extra"
without forcing consumers to know about a router-specific exception type.
"""

from __future__ import annotations


class RouterError(Exception):
    """Base for all router-level errors.

    External users (Sotto Desktop, third-party integrators, the OGE-585 CLI's
    ``router route``) should only need to ``except RouterError`` to handle
    every failure mode of :class:`~ogentic_router.Router` construction and
    runtime evaluation.

    Distinct from :class:`ogentic_router.PolicyError` (which is scoped to
    policy-file load / validate failures) — a malformed policy bubbles up
    as ``PolicyError``, a Shield init failure as ``ShieldUnavailableError``,
    and any future router-specific failure mode as a new ``RouterError``
    subclass.
    """


class ShieldUnavailableError(RouterError, ImportError):
    """Raised when the ``[shield]`` extra is missing or Shield init fails.

    Subclasses :class:`ImportError` so callers using ``except ImportError``
    for optional-dependency handling also catch it — this is the standard
    Python idiom for "you didn't install the extra". Callers who want the
    router-specific type can still ``except ShieldUnavailableError`` or
    the broader :class:`RouterError`.

    The exception message always includes the canonical install hint
    (``pip install 'ogentic-router[shield]'``) so the operator-facing
    traceback is self-explanatory without docs spelunking.
    """


__all__ = ["RouterError", "ShieldUnavailableError"]
