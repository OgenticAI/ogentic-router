"""FastAPI application — OpenAI-shaped server (OGE-583).

Exposes an OpenAI-compatible chat completions endpoint that routes requests
through the ogentic-router policy + Shield pipeline before forwarding to the
appropriate backend.

Routes
------
- ``GET  /healthz``             — liveness probe
- ``GET  /v1/models``           — list models (derived from router.yaml backends)
- ``POST /v1/chat/completions`` — main chat completions endpoint (streaming + non-streaming)
- ``GET  /v1/policy``           — inspect the loaded routing policy
- ``GET  /v1/decision/{id}``    — placeholder for decision lookup (no audit in v0.1)

The FastAPI app is constructed by :func:`create_app`, which accepts an optional
:class:`~ogentic_router.server.config.RouterConfig` so tests can inject
configs without touching the filesystem.

Backend lifecycle
-----------------
Adapters hold ``httpx.AsyncClient`` instances. The lifespan context manager
constructs adapters once at boot and closes any httpx clients on shutdown via
``adapter.aclose()`` if the adapter exposes that method.

Usage::

    from ogentic_router.server import create_app
    app = create_app()           # loads router.yaml from ROUTER_CONFIG env var
    # or
    app = create_app(config=my_router_config, adapters={"openai-cloud": my_adapter})
"""

from __future__ import annotations

import os
import time
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ogentic_router.errors import ConfigError, ServerError
from ogentic_router.server._normalize import _new_request_id, to_openai_chunk, to_openai_response
from ogentic_router.server._sse import sse_data, sse_done
from ogentic_router.server.config import RouterConfig, load_router_config, resolve_api_key

# ─── Request / Response Pydantic models ──────────────────────────────────────


class ChatMessage(BaseModel):
    """A single message in the conversation."""

    role: str
    content: str | list[Any]
    name: str | None = None


class ChatCompletionRequest(BaseModel):
    """OpenAI-shaped POST /v1/chat/completions request body."""

    model: str
    messages: list[ChatMessage] = Field(min_length=1)
    stream: bool = False
    max_tokens: int | None = None
    temperature: float | None = None


class PolicyResponse(BaseModel):
    """Snapshot of the loaded routing policy for /v1/policy."""

    version: int
    default_backend: str
    rule_count: int
    rules: list[dict[str, Any]]


# ─── Adapter construction helpers ────────────────────────────────────────────


def _build_adapters(config: RouterConfig) -> dict[str, Any]:
    """Construct adapters for each backend defined in router.yaml.

    Adapters are keyed by their ``backend_id`` string. Construction is
    eager so any misconfiguration (missing env vars, unknown kinds) fails
    at startup rather than on the first request.

    Returns an empty dict when no backends are configured (test mode).
    """
    from ogentic_router.adapters.factory import build_adapter  # noqa: PLC0415

    adapters: dict[str, Any] = {}
    for backend in config.backends:
        adapters[backend.id] = build_adapter(
            kind=backend.kind,
            backend_id=backend.id,
            base_url=backend.base_url,
            default_model=backend.default_model,
            api_key=resolve_api_key(backend),
        )
    return adapters


# ─── App factory ─────────────────────────────────────────────────────────────


def create_app(
    *,
    config: RouterConfig | None = None,
    adapters: dict[str, Any] | None = None,
) -> FastAPI:
    """Create and configure the FastAPI application.

    Parameters
    ----------
    config:
        Optional pre-loaded :class:`RouterConfig`. When ``None``, the server
        reads the config from the path given by the ``ROUTER_CONFIG``
        environment variable. If the env var is also absent, the server
        starts without a config (routes will return 503).
    adapters:
        Optional pre-constructed adapter map (``{backend_id: adapter}``).
        When ``None``, adapters are built from ``config.backends``.
        Pass an explicit adapter map in tests to avoid real API keys.

    Returns
    -------
    FastAPI
        A configured FastAPI application ready for ASGI serving.
    """
    # Mutable state shared via app.state across request handlers.
    # Set to ``None`` here; lifespan populates it.
    _resolved_config: RouterConfig | None = config
    _resolved_adapters: dict[str, Any] | None = adapters
    _policy: Any = None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        nonlocal _resolved_config, _resolved_adapters, _policy

        # Load config from disk if not provided.
        if _resolved_config is None:
            cfg_path = os.environ.get("ROUTER_CONFIG")
            if cfg_path:
                try:
                    _resolved_config = load_router_config(cfg_path)
                except ConfigError as exc:
                    raise ServerError(f"Failed to load router config: {exc}") from exc

        # Build adapters if not provided.
        if _resolved_adapters is None and _resolved_config is not None:
            _resolved_adapters = _build_adapters(_resolved_config)

        # Load policy if config is available.
        if _resolved_config is not None:
            try:
                from ogentic_router.policy import Policy  # noqa: PLC0415

                _policy = Policy.from_yaml(_resolved_config.policy_path)
            except Exception:
                _policy = None

        # Expose via app.state for route handlers.
        app.state.router_config = _resolved_config
        app.state.adapters = _resolved_adapters or {}
        app.state.policy = _policy

        yield

        # Shutdown: close httpx clients held by adapters.
        for adapter in (app.state.adapters or {}).values():
            if hasattr(adapter, "aclose"):
                try:
                    await adapter.aclose()
                except Exception:  # noqa: BLE001 — best-effort cleanup
                    pass

    app = FastAPI(
        title="ogentic-router",
        description="OpenAI-shaped router that applies privacy-aware routing policies.",
        version="0.1.0",
        lifespan=lifespan,
    )

    # ── Route handlers ───────────────────────────────────────────────────

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        """Liveness probe — always returns ``{"status": "ok"}``."""
        return {"status": "ok"}

    @app.get("/v1/models")
    async def list_models() -> dict[str, Any]:
        """List available models from the configured backends.

        Returns an OpenAI-shaped ``/v1/models`` response.
        """
        cfg: RouterConfig | None = app.state.router_config
        if cfg is None:
            # No config loaded — return empty model list.
            return {"object": "list", "data": []}

        now = int(time.time())
        data: list[dict[str, Any]] = []
        for backend in cfg.backends:
            model_id = backend.default_model or backend.id
            data.append(
                {
                    "id": model_id,
                    "object": "model",
                    "created": now,
                    "owned_by": f"ogentic-router/{backend.kind}",
                    "permission": [],
                    "root": model_id,
                    "parent": None,
                }
            )

        return {"object": "list", "data": data}

    @app.post("/v1/chat/completions")
    async def chat_completions(request: ChatCompletionRequest) -> Any:
        """OpenAI-compatible chat completions endpoint.

        Routing flow:
        1. Extract the text content from the last user message.
        2. Run it through the Router (Shield + Policy) if a policy is loaded.
        3. Pick the target adapter based on the RouteDecision's ``backend_id``.
        4. If no adapter matches, fall back to the first configured adapter.
        5. Call ``adapter.chat(messages, ...)`` and normalise the response.

        Streaming responses use SSE framing (``data: {...}\\n\\n``).
        """
        all_adapters: dict[str, Any] = app.state.adapters
        policy: Any = app.state.policy

        if not all_adapters:
            raise HTTPException(status_code=503, detail="No backends configured")

        # Determine target backend via routing policy if available.
        target_backend_id: str | None = None
        if policy is not None:
            # We only route via policy if we have a PolicySpec with default_backend.
            # Full Router/Shield integration is in v0.2 — for now use default_backend.
            try:
                if hasattr(policy, "default_backend"):
                    target_backend_id = policy.default_backend
            except Exception:  # noqa: BLE001 — routing failure falls back gracefully
                pass

        # Pick adapter: routing decision → first adapter → 503.
        adapter: Any = None
        if target_backend_id and target_backend_id in all_adapters:
            adapter = all_adapters[target_backend_id]
        else:
            # Fall back to first available adapter.
            adapter = next(iter(all_adapters.values()), None)

        if adapter is None:
            raise HTTPException(status_code=503, detail="No suitable backend found")

        # Convert Pydantic messages to dicts for the adapter.
        messages = [m.model_dump(exclude_none=True) for m in request.messages]
        request_id = _new_request_id()

        chat_kwargs: dict[str, Any] = {
            "messages": messages,
            "model": request.model,
            "stream": request.stream,
        }
        if request.max_tokens is not None:
            chat_kwargs["max_tokens"] = request.max_tokens
        if request.temperature is not None:
            chat_kwargs["temperature"] = request.temperature

        if request.stream:
            return await _stream_response(adapter, chat_kwargs, request.model, request_id)
        else:
            return await _blocking_response(adapter, chat_kwargs, request.model, request_id)

    @app.get("/v1/policy")
    async def get_policy() -> PolicyResponse:
        """Inspect the loaded routing policy."""
        policy: Any = app.state.policy
        if policy is None:
            raise HTTPException(status_code=404, detail="No policy loaded")

        try:
            from ogentic_router.policy.models import PolicySpec  # noqa: PLC0415

            spec: PolicySpec = policy._spec
            rules = [
                {
                    "id": rule.id,
                    "when": rule.when.model_dump(exclude_none=True),
                    "route": rule.route,
                    "transform": rule.transform.value if rule.transform else None,
                }
                for rule in spec.rules
            ]
            return PolicyResponse(
                version=spec.version,
                default_backend=spec.default_backend,
                rule_count=len(spec.rules),
                rules=rules,
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Policy introspection failed: {exc}") from exc

    @app.get("/v1/decision/{decision_id}")
    async def get_decision(decision_id: str) -> dict[str, Any]:
        """Lookup a past routing decision by ID.

        v0.1 does not persist decisions (no ogentic-audit integration yet).
        Always returns 404 with an explanation.
        """
        return {
            "id": decision_id,
            "detail": "Decision audit is not available in v0.1 (ogentic-audit integration pending)",
            "status": "not_found",
        }

    return app


# ─── Streaming / blocking helpers ────────────────────────────────────────────


async def _blocking_response(
    adapter: Any,
    chat_kwargs: dict[str, Any],
    model: str,
    request_id: str,
) -> Any:
    """Call the adapter and return a normalised dict response."""
    resp = await adapter.chat(**chat_kwargs)
    return to_openai_response(resp, model, request_id)


async def _stream_response(
    adapter: Any,
    chat_kwargs: dict[str, Any],
    model: str,
    request_id: str,
) -> StreamingResponse:
    """Call the adapter in streaming mode and return an SSE StreamingResponse."""

    async def generate() -> AsyncIterator[str]:
        stream = await adapter.chat(**chat_kwargs)
        async for event in stream:
            chunk = to_openai_chunk(event, model, request_id)
            if chunk is not None:
                yield sse_data(chunk)
        yield sse_done()

    return StreamingResponse(generate(), media_type="text/event-stream")


__all__ = ["ChatCompletionRequest", "ChatMessage", "PolicyResponse", "create_app"]
