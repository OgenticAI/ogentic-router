"""Router-level exception types (OGE-580 / OGE-583 / OGE-1061).

Two-class hierarchy on purpose. ``RouterError`` is the catch-all base — higher
layers (the OGE-583 server, OGE-585 CLI, OGE-586 MCP tool surface) should be
able to ``except RouterError`` to catch every failure mode of the router
construction / runtime path.

``ShieldUnavailableError`` additionally subclasses :class:`ImportError` so
callers using the standard ``except ImportError`` pattern for optional-extra
handling also catch it. That dual-inheritance is the v0.1 ergonomics call: it
matches the way Python typically signals "you didn't install the extra"
without forcing consumers to know about a router-specific exception type.

OGE-583 additions (server layer):

- :class:`ServerError` — generic server-level failure (startup, routing, etc.)
- :class:`ConfigError` — router.yaml parse / validation failure
- :class:`ServerImportError` — ``[server]`` extra (fastapi / uvicorn) missing

OGE-1061 additions (budget ceiling):

- :class:`BudgetCeilingExceeded` — estimated call cost exceeds configured ceiling
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


class ServerError(RouterError):
    """Raised for server-level failures (startup, routing, response errors).

    Added in OGE-583 for the FastAPI server layer. Covers:
    - Startup failures (config load, adapter construction, etc.)
    - Runtime routing failures that cannot be surfaced as HTTP errors
    - Any unexpected condition inside the server that doesn't fit a more
      specific subclass.
    """


class ConfigError(RouterError, ValueError):
    """Raised when router.yaml fails to parse or validate.

    Added in OGE-583. Subclasses :class:`ValueError` so callers using
    ``except ValueError`` for config-validation patterns also catch it.
    The message always includes the file path and the exact validation
    failure so operators can fix the config without reading source code.
    """


class ServerImportError(ServerError, ImportError):
    """Raised when the ``[server]`` extra (fastapi / uvicorn) is not installed.

    Added in OGE-583. Mirrors the :class:`ShieldUnavailableError` dual-
    inheritance pattern — callers using ``except ImportError`` will catch it.
    The message includes the canonical install hint
    (``pip install 'ogentic-router[server]'``).
    """


class BudgetCeilingExceeded(RouterError):
    """Raised when the estimated cost of a call exceeds the configured ceiling.

    Added in OGE-1061. The check happens **before** any network call — the
    prompt is never sent to a provider when this exception is raised.

    Attributes:
        estimated_cost: Estimated USD cost for the call (input tokens only).
        ceiling: The configured budget ceiling in USD.
        model: The model identifier used for cost estimation.
    """

    def __init__(self, estimated_cost: float, ceiling: float, model: str) -> None:
        self.estimated_cost = estimated_cost
        self.ceiling = ceiling
        self.model = model
        super().__init__(
            f"BudgetCeilingExceeded: estimated ${estimated_cost:.6g} exceeds ceiling ${ceiling}"
        )


class CloudRouteDeniedError(RouterError):
    """Raised when regulated content would be routed to a non-local backend.

    The fail-closed core of the privacy promise (OGE-1135). When a prompt is
    classified into one of the policy's ``deny_cloud`` groups (default:
    privilege / PHI / MNPI) and the chosen backend is not on-device, the router
    refuses the decision **before** any dispatch — a misconfigured or mis-ordered
    policy can never leak regulated content to the cloud.

    Attributes:
        groups: The denied category groups present on the prompt.
        backend_id: The cloud backend the policy would have routed to.
    """

    def __init__(self, groups: list[str], backend_id: str) -> None:
        self.groups = groups
        self.backend_id = backend_id
        super().__init__(
            f"CloudRouteDeniedError: content in {groups} must stay local, but the "
            f"policy routed it to non-local backend {backend_id!r}. Route these "
            "groups to a local backend, or opt out with deny_cloud.enforce=false."
        )


__all__ = [
    "BudgetCeilingExceeded",
    "CloudRouteDeniedError",
    "ConfigError",
    "RouterError",
    "ServerError",
    "ServerImportError",
    "ShieldUnavailableError",
]
