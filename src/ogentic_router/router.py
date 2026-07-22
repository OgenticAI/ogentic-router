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

import logging
import time
from collections.abc import Callable
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast, runtime_checkable

import yaml

from .audit import AuditSink, NoopSink, RouteDecisionAudit, sink_from_config
from .audit._request_id import compute_request_id, resolve_salt
from .audit.sinks import safe_emit
from .classification import ShieldClassification
from .cost import estimate_cost
from .errors import (
    BudgetCeilingExceeded,
    CloudRouteDeniedError,
    RouterError,
    ShieldUnavailableError,
)
from .policy import Policy, RouteDecision

logger = logging.getLogger("ogentic_router")

# Backend-id substrings that indicate an on-device backend, used as a fallback
# when the config doesn't declare its backends explicitly. See
# ``Router._backend_is_local``.
_LOCAL_HINTS = ("local", "ollama", "llamacpp", "llama.cpp", "llama-cpp", "mlx")
_CLOUD_HINTS = ("cloud", "openai", "anthropic", "openrouter", "together")


class _Unset(Enum):
    """Sentinel so ``route(budget_ceiling=None)`` (disable enforcement for this
    call) is distinguishable from ``route()`` (use the policy's budget)."""

    UNSET = 0


_UNSET = _Unset.UNSET

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


def _fingerprint(text: str) -> str:
    """Return ``sha256:<16 hex>`` — the same shape as Shield's ``text_hash_for``.

    Used only for the pre-classification error path (e.g. a budget-ceiling
    refusal before Shield runs) so an audit row always carries a shape-only
    fingerprint, never raw prompt text. The normal path uses the Shield-sourced
    ``ShieldClassification.text_hash``; this mirrors its format byte-for-byte.
    """
    import hashlib

    return f"sha256:{hashlib.sha256(text.encode()).hexdigest()[:16]}"


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

    __slots__ = (
        "_policy",
        "_shield",
        "_audit_sink",
        "_audit_salt",
        "_local_backends",
        "_backends",
    )

    def __init__(
        self,
        policy: Policy,
        shield: _ShieldLike | None = None,
        *,
        audit_sink: AuditSink | None = None,
        local_backends: frozenset[str] | None = None,
        backends: list[dict[str, Any]] | None = None,
    ) -> None:
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
            audit_sink: Where per-``route`` decision rows are emitted.
                Defaults to :class:`~ogentic_router.audit.NoopSink` (drops
                rows). Emission is fire-and-forget — a sink failure never
                interrupts routing.
            local_backends: Backend ids known to be on-device. When a config
                declares its ``backends`` (with ``kind``), this is populated
                so ``backend_is_local`` in the audit row is exact; otherwise
                the Router falls back to a naming heuristic. Derived from
                ``backends`` when that is given and this is not.
            backends: Optional descriptors of the declared backends —
                ``{"backend_id", "is_local", "default_model"}`` each — surfaced
                by the MCP ``router.adapters`` tool and used to make
                ``backend_is_local`` exact.
        """
        self._policy = policy
        self._shield = shield
        self._audit_sink: AuditSink = audit_sink if audit_sink is not None else NoopSink()
        self._audit_salt = resolve_salt()
        self._backends: tuple[dict[str, Any], ...] = tuple(backends) if backends else ()
        if local_backends is None and self._backends:
            local_backends = frozenset(
                b["backend_id"] for b in self._backends if b.get("is_local")
            )
        self._local_backends = local_backends

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

        # Audit sink from the optional ``audit:`` block (default: NoopSink).
        audit_sink = sink_from_config(config.get("audit"))

        # If the config declares backends (server-style), build descriptors so
        # ``backend_is_local`` is exact and the MCP router.adapters tool can list
        # them. ``kind`` in {ollama, llamacpp} → on-device.
        local_kinds = {"ollama", "llamacpp"}
        backend_descriptors: list[dict[str, Any]] = [
            {
                "backend_id": b["id"],
                "is_local": b.get("kind") in local_kinds,
                "default_model": b.get("default_model"),
            }
            for b in (config.get("backends") or [])
            if isinstance(b, dict) and b.get("id")
        ]

        return cls(
            policy=policy,
            shield=cast(_ShieldLike, shield),
            audit_sink=audit_sink,
            backends=backend_descriptors or None,
        )

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

    @property
    def backends(self) -> tuple[dict[str, Any], ...]:
        """Declared backend descriptors (``backend_id``, ``is_local``,
        ``default_model``). Empty when the config didn't declare ``backends``.
        Surfaced by the MCP ``router.adapters`` tool."""
        return self._backends

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

    def route(
        self,
        prompt: str,
        *,
        model: str | None = None,
        budget_ceiling: float | None | _Unset = _UNSET,
    ) -> RouteDecision:
        """Classify ``prompt`` and run it through the loaded Policy.

        Equivalent to ``policy.evaluate(classify(prompt))`` but slightly more
        efficient — internally retains the raw Shield ``entities`` list so
        the Policy's ``category_in`` / ``category_not_in`` predicates can
        fire against entities that aren't surfaced on the user-facing
        :class:`ShieldClassification` projection.

        **Budget enforcement is ON by default** (OGE-1120). When
        ``budget_ceiling`` is not passed, the ceiling comes from the loaded
        policy's ``budget`` block (default: enforce at
        ``$1.00``/call). Opt out per engagement with ``budget: {enforce: false}``
        in the policy, or per call by passing ``budget_ceiling=None``.

        Args:
            prompt: The prompt text to route.
            model: Optional model identifier used for budget estimation
                (e.g. ``"gpt-4-turbo"``, ``"opus-4"``). Defaults to
                ``"unknown"`` (a conservative price) when a ceiling applies.
            budget_ceiling: Per-call maximum estimated USD cost.
                *Omitted* (default) — use the policy's budget block (ON by
                default). ``None`` — disable enforcement for this call.
                ``0.0`` — refuse all calls (dry-run). ``> 0`` — override the
                policy ceiling for this call. When enforcement is active and
                the estimated input-token cost exceeds the ceiling,
                :class:`~ogentic_router.BudgetCeilingExceeded` is raised
                **before** any Shield analysis or network call.

        Raises:
            BudgetCeilingExceeded: if an active ceiling is exceeded by the
                estimated cost of sending ``prompt`` to ``model``. The call is
                never sent to any provider.
        """
        # Resolve the effective ceiling: an explicit kwarg (a float, or None to
        # disable) always wins; otherwise fall back to the policy's budget,
        # which enforces by default.
        if isinstance(budget_ceiling, _Unset):
            effective_ceiling = self._policy.effective_ceiling()
        else:
            effective_ceiling = budget_ceiling
        start = time.perf_counter()
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        # Pre-classification fallback fingerprint (same format as Shield's
        # text_hash_for) so an error before classify still carries a hash, not
        # raw text. Overwritten with the Shield-sourced hash once we classify.
        prompt_hash = _fingerprint(prompt)
        result: Any = None
        projection: ShieldClassification | None = None
        decision: RouteDecision | None = None
        error: str | None = None
        try:
            if effective_ceiling is not None:
                effective_model = model or "unknown"
                cost = estimate_cost(effective_model, prompt)
                if cost > effective_ceiling:
                    raise BudgetCeilingExceeded(
                        estimated_cost=cost,
                        ceiling=effective_ceiling,
                        model=effective_model,
                    )

            shield = self._ensure_shield()
            result = shield.analyze(prompt)
            projection = self._project(result)
            if projection.text_hash:
                prompt_hash = projection.text_hash
            # Wrap the projection + raw entities so the Policy's full Protocol
            # surface is satisfied (entity_count alone isn't enough for
            # category_in / category_not_in predicates).
            wrapped = _ClassificationWithEntities(
                score=projection.score,
                category_groups_found=projection.category_groups_found,
                entities=list(getattr(result, "entities", []) or []),
                top_category=projection.top_category,
            )
            decision = self._policy.evaluate(wrapped)
            self._enforce_deny_cloud(decision, projection.category_groups_found)
            return decision
        except Exception as exc:
            error = type(exc).__name__
            raise
        finally:
            latency_ms = (time.perf_counter() - start) * 1000.0
            self._emit_audit(
                ts=ts,
                prompt_hash=prompt_hash,
                projection=projection,
                result=result,
                decision=decision,
                error=error,
                latency_ms=latency_ms,
            )

    # ── Internals ───────────────────────────────────────────────────────

    def _ensure_shield(self) -> _ShieldLike:
        """Return the shared Shield, lazily constructing it on first use."""
        if self._shield is None:
            Shield, _text_hash_for = _import_shield()
            self._shield = cast(_ShieldLike, Shield())
        return self._shield

    def _enforce_deny_cloud(
        self, decision: RouteDecision, groups_found: frozenset[str]
    ) -> None:
        """Fail closed: refuse a cloud-bound decision for regulated content (OGE-1135).

        If the prompt carries any of the policy's ``deny_cloud`` groups (default
        privilege / PHI / MNPI) and the chosen backend is not on-device, raise
        :class:`CloudRouteDeniedError` — before any dispatch. A correct policy
        routes these groups local, so this never fires in normal operation; it's
        the backstop against a misconfigured or mis-ordered policy.

        Locality: when the config declares its backends, ``backend_is_local`` is
        authoritative and an *undeterminable* backend for regulated content is
        also denied (fail closed). With no declared backends (policy-only /
        dry-run), a backend the naming heuristic can't classify is allowed with
        a WARNING rather than breaking inspection.
        """
        denied = self._policy.denied_groups()
        if not denied:
            return
        hit = sorted(groups_found & denied)
        if not hit:
            return
        is_local = self._backend_is_local(decision.backend_id)
        if is_local is True:
            return
        if is_local is False or self._backends:
            raise CloudRouteDeniedError(groups=hit, backend_id=decision.backend_id)
        logger.warning(
            "cannot confirm backend %r is local for denied groups %s — allowing. "
            "Declare backends in the config for authoritative fail-closed enforcement.",
            decision.backend_id,
            hit,
        )

    def _backend_is_local(self, backend_id: str | None) -> bool | None:
        """Best-effort: is ``backend_id`` an on-device backend?

        When the config declared its ``backends`` (with ``kind``), locality is
        exact via ``self._local_backends``. Otherwise fall back to a naming
        heuristic (``ollama-local`` → local, ``openai-cloud`` → cloud). Returns
        ``None`` when neither the declared set nor the heuristic can decide, so
        the audit row never asserts a locality it didn't actually determine.
        """
        if backend_id is None:
            return None
        if self._local_backends is not None:
            return backend_id in self._local_backends
        lowered = backend_id.lower()
        if any(h in lowered for h in _LOCAL_HINTS):
            return True
        if any(h in lowered for h in _CLOUD_HINTS):
            return False
        return None

    def _emit_audit(
        self,
        *,
        ts: str,
        prompt_hash: str,
        projection: ShieldClassification | None,
        result: Any,
        decision: RouteDecision | None,
        error: str | None,
        latency_ms: float,
    ) -> None:
        """Build one :class:`RouteDecisionAudit` row and emit it (never raises).

        Called from :meth:`route`'s ``finally`` so it runs on both success and
        error paths. Emission goes through :func:`safe_emit`, so a sink failure
        is logged and swallowed — routing already happened.
        """
        profile_ids = getattr(result, "profile_ids", None)
        profile = profile_ids[0] if profile_ids else None
        transform = decision.transform.value if decision and decision.transform else None
        row = RouteDecisionAudit(
            ts=ts,
            request_id=compute_request_id(self._audit_salt, ts, prompt_hash),
            prompt_hash=prompt_hash,
            sensitivity_score=projection.score if projection else None,
            profile=profile,
            top_category=projection.top_category if projection else None,
            groups_found=sorted(projection.category_groups_found) if projection else [],
            route_decision=decision.backend_id if decision else None,
            rule_id=decision.rule_id if decision else None,
            transform=transform,
            backend_is_local=self._backend_is_local(decision.backend_id if decision else None),
            latency_ms=round(latency_ms, 4),
            error=error,
        )
        safe_emit(self._audit_sink, row)

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
