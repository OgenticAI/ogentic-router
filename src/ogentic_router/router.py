"""The ``Router`` class — Shield classifier + Policy DSL stitched together (OGE-580).

This is the v0.1 "sensitivity in → verdict out" pipeline. Construct a
``Router`` once at process start, then call :meth:`Router.route` per request
to get a :class:`~ogentic_router.RouteDecision`. The Router owns a single
shared :class:`ogentic_shield.Shield` instance (no per-call cold-start cost)
and feeds Shield's :class:`ogentic_shield.AnalysisResult` into the loaded
:class:`~ogentic_router.Policy`.

Design notes (mirrors the spec brief §7):

* **Sync only in v0.1.** ``Policy.evaluate`` is sync; an async ``AsyncRouter``
  is deferred until the OGE-583 server actually needs it.
* **Lazy Shield import.** ``ogentic-shield`` is an optional extra (``[shield]``).
  The Shield module is imported the first time a Router is constructed,
  not at ``import ogentic_router`` time — that keeps the base install
  failure-free for users who only need the Policy DSL.
* **Profile pass-through.** The Router never hardcodes a Shield profile
  list. Configs supply the profiles; the Router only forwards them to
  ``Shield(profiles=..., config=...)``. Sotto Desktop's domain-specific
  profile set is just one consumer.
* **Hash via Shield's helper.** ``text_hash_for(text)`` is the org-wide
  audit-fingerprint contract. Mirror it; do not roll our own ``hashlib``
  call — Router / Shield / Audit fingerprints must align byte-for-byte.

The Router exposes both :meth:`classify` (pure classification, no routing —
feeds the OGE-586 MCP tool surface) and :meth:`route` (classify + policy
evaluate in one call). Both share the same Shield instance.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast, runtime_checkable

import yaml

from .classification import ShieldClassification
from .errors import RouterError, ShieldUnavailableError
from .policy import Policy, RouteDecision

if TYPE_CHECKING:  # pragma: no cover - import only for type hints
    from ogentic_shield import AnalysisResult


# ─── Shield duck-type protocol (so tests can inject a SimpleNamespace mock) ──


@runtime_checkable
class _ShieldLike(Protocol):
    """The slice of :class:`ogentic_shield.Shield` we depend on.

    Declared as a ``Protocol`` so unit tests can inject a ``SimpleNamespace``
    or a hand-rolled stub without paying the Presidio / spaCy cold-start
    cost. The full Shield import is still wired through :func:`_import_shield`
    for the real-world path.
    """

    def analyze(self, text: str) -> AnalysisResult: ...


# ─── Lazy Shield import ──────────────────────────────────────────────────────


def _import_shield() -> tuple[type[Any], Callable[[str], str]]:
    """Lazy-import the Shield runtime.

    Raises :class:`ShieldUnavailableError` (which subclasses ``ImportError``)
    with the canonical install hint if the ``[shield]`` extra isn't
    installed. Keeps ``import ogentic_router`` free of a Shield dependency
    so the base install only pays for the Policy DSL.
    """
    try:
        from ogentic_shield import Shield  # noqa: PLC0415 — lazy by design
        from ogentic_shield.pipeline import text_hash_for  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover — exercised via monkeypatched sys.modules
        raise ShieldUnavailableError(
            "ogentic-shield is not installed. Install the [shield] extra with "
            "`pip install 'ogentic-router[shield]'` (or `uv pip install "
            "'ogentic-router[shield]'`)."
        ) from exc
    return Shield, text_hash_for


# ─── Internal: shield-result wrapper for the policy protocol ────────────────


class _ClassificationWithEntities:
    """Adapter that satisfies :class:`~ogentic_router.policy.policy._ShieldResultLike`.

    The user-facing :class:`ShieldClassification` projection deliberately
    omits the entity list (it's a heavyweight field consumers don't need).
    The Policy DSL's ``category_in`` / ``category_not_in`` predicates DO
    read the entity list, so when :meth:`Router.route` chains classify →
    evaluate we wrap the classification + the raw entities together so
    every Policy predicate fires correctly.

    Implemented as a tiny attribute-bag class (not a dataclass) because we
    don't need equality / hashing — this object lives for the duration of
    a single :meth:`Router.route` call and is never exposed to the caller.
    """

    __slots__ = ("score", "category_groups_found", "entities", "top_category")

    def __init__(
        self,
        *,
        score: int,
        category_groups_found: frozenset[str],
        entities: list[Any],
        top_category: str | None,
    ) -> None:
        self.score = score
        self.category_groups_found = category_groups_found
        self.entities = entities
        self.top_category = top_category


# ─── The Router class ────────────────────────────────────────────────────────


class Router:
    """The Shield-classifier + Policy-DSL pipeline.

    Construct via :meth:`from_config`, :meth:`from_yaml`, or the bare
    ``Router(policy, shield=...)`` constructor (the bare form is intended
    for tests / programmatic use where the Shield instance is supplied
    directly).

    Thread-safety: ``Shield.analyze`` is documented as thread-safe, so the
    Router itself is thread-safe for concurrent ``classify`` / ``route``
    calls. No internal mutation; ``__slots__`` locks the attribute set.
    """

    __slots__ = ("_policy", "_shield")

    def __init__(self, policy: Policy, shield: _ShieldLike | None = None) -> None:
        """Construct a Router from a pre-built ``Policy`` and optional Shield.

        Args:
            policy: A loaded :class:`~ogentic_router.Policy` instance.
            shield: An optional pre-constructed Shield (or stub matching
                the :class:`_ShieldLike` Protocol). If ``None``, defers
                Shield init — :meth:`classify` / :meth:`route` will lazily
                construct a default ``Shield()`` on first use, which means
                the cold-start cost is paid on the first request rather
                than at Router construction. Prefer :meth:`from_config`
                in production so init cost is paid once at boot.
        """
        self._policy = policy
        self._shield = shield

    # ── Constructors ────────────────────────────────────────────────────

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> Router:
        """Build a Router from an in-memory config dict.

        Config shape::

            {
                "policy_path": "policy.yaml",   # required; loaded via Policy.from_yaml
                "shield": {                      # optional; default = Shield()
                    "profiles": ["shield-legal", "shield-finance"],
                    "config": {...},             # passed to Shield.config
                },
            }

        The Shield instance is constructed eagerly here so the cold-start
        cost (Presidio / spaCy model load) is paid at boot, not on the
        first request. The single instance is reused across every
        :meth:`classify` / :meth:`route` call for the lifetime of this
        Router — one of the load-bearing v0.1 design decisions.

        Raises:
            RouterError: if ``policy_path`` is missing or the policy
                fails to load.
            ShieldUnavailableError: if the ``[shield]`` extra isn't
                installed.
        """
        if "policy_path" not in config:
            raise RouterError(
                "Router config missing required key 'policy_path' "
                "(should point at a YAML policy file)."
            )

        policy = Policy.from_yaml(config["policy_path"])

        shield_cfg = config.get("shield") or {}
        Shield, _text_hash_for = _import_shield()
        shield_kwargs: dict[str, Any] = {}
        if "profiles" in shield_cfg:
            shield_kwargs["profiles"] = shield_cfg["profiles"]
        if "config" in shield_cfg:
            shield_kwargs["config"] = shield_cfg["config"]
        shield = Shield(**shield_kwargs)
        return cls(policy=policy, shield=cast(_ShieldLike, shield))

    @classmethod
    def from_yaml(cls, path: str | Path) -> Router:
        """Build a Router from a YAML config file.

        The YAML is parsed with the same ``policy_path`` / ``shield``
        schema as :meth:`from_config`. ``policy_path`` is resolved
        **relative to the config file's directory** so operators can
        ship a ``router.yaml`` + ``policy.yaml`` side-by-side without
        absolute paths.

        Raises:
            RouterError: if the YAML is malformed or the config is
                rejected by :meth:`from_config`.
        """
        p = Path(path)
        try:
            text = p.read_text(encoding="utf-8")
        except OSError as exc:
            raise RouterError(f"Cannot read router config file {str(p)!r}: {exc}") from exc

        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise RouterError(f"Invalid YAML in {str(p)!r}: {exc}") from exc

        if not isinstance(data, dict):
            raise RouterError(
                f"Router config file {str(p)!r} must contain a YAML mapping "
                f"at the top level, got {type(data).__name__}",
            )

        # Resolve policy_path relative to the config file's directory if it
        # isn't already absolute. Lets users ship side-by-side configs.
        if "policy_path" in data:
            policy_path = Path(data["policy_path"])
            if not policy_path.is_absolute():
                data = {**data, "policy_path": str((p.parent / policy_path).resolve())}

        return cls.from_config(data)

    # ── Read-only accessors ─────────────────────────────────────────────

    @property
    def policy(self) -> Policy:
        """The loaded :class:`~ogentic_router.Policy`. Read-only."""
        return self._policy

    # ── Request-path methods ────────────────────────────────────────────

    def classify(self, prompt: str) -> ShieldClassification:
        """Run Shield over ``prompt`` and project to :class:`ShieldClassification`.

        Pure classification — no routing. This is the entry point the OGE-586
        MCP tool surface (``router.classify_route``) calls when it wants the
        sensitivity signal without committing to a backend.

        If the Router was constructed without an explicit Shield, the first
        call to :meth:`classify` (or :meth:`route`) lazily constructs a
        default ``Shield()`` and caches it for subsequent calls. Prefer
        :meth:`from_config` in production so this cost is paid at boot.
        """
        shield = self._ensure_shield()
        result = shield.analyze(prompt)
        return self._project(result)

    def route(self, prompt: str) -> RouteDecision:
        """Classify ``prompt`` and run it through the loaded Policy.

        Equivalent to ``policy.evaluate(classify(prompt))`` but slightly more
        efficient — internally retains the raw Shield ``entities`` list so
        the Policy's ``category_in`` / ``category_not_in`` predicates can
        fire against entities that aren't surfaced on the user-facing
        :class:`ShieldClassification` projection.
        """
        shield = self._ensure_shield()
        result = shield.analyze(prompt)
        projection = self._project(result)
        # Wrap the projection + raw entities so the Policy's full Protocol
        # surface is satisfied (entity_count alone isn't enough for
        # category_in / category_not_in predicates).
        wrapped = _ClassificationWithEntities(
            score=projection.score,
            category_groups_found=projection.category_groups_found,
            entities=list(getattr(result, "entities", []) or []),
            top_category=projection.top_category,
        )
        return self._policy.evaluate(wrapped)

    # ── Internals ───────────────────────────────────────────────────────

    def _ensure_shield(self) -> _ShieldLike:
        """Return the shared Shield, lazily constructing it on first use."""
        if self._shield is None:
            Shield, _text_hash_for = _import_shield()
            self._shield = cast(_ShieldLike, Shield())
        return self._shield

    @staticmethod
    def _project(result: Any) -> ShieldClassification:
        """Project Shield's ``AnalysisResult`` into :class:`ShieldClassification`.

        Pulls the audit fingerprint from the result's ``text_hash`` field —
        Shield already populates this via ``text_hash_for`` during
        ``analyze()``, so we don't re-hash. The static-method shape keeps
        the projection testable in isolation.
        """
        raw_groups = getattr(result, "category_groups_found", None) or set()
        # Project enum members (CategoryGroup) or bare strings to str.
        groups: frozenset[str] = frozenset(
            str(getattr(g, "value", g)) for g in raw_groups
        )
        return ShieldClassification(
            score=int(getattr(result, "score", 0)),
            category_groups_found=groups,
            top_category=getattr(result, "top_category", None),
            entity_count=int(getattr(result, "entity_count", 0)),
            text_hash=str(getattr(result, "text_hash", "")),
        )


__all__ = ["Router"]
