"""Ollama local adapter for ogentic-router.

Talks to a loopback-bound ``ollama serve`` over its OpenAI-compatible
``POST /v1/chat/completions`` endpoint. No use of the official ``ollama``
Python SDK — raw ``httpx`` covers both Ollama and llama.cpp with one
streaming parser (see :mod:`ogentic_router.adapters._sse`).

Key invariants:

* **Loopback-only at construction.** ``_validate_localhost`` runs *before*
  ``httpx`` is imported, so a misconfigured host fails before the
  ``[local]`` extra is even loaded. See :mod:`ogentic_router.adapters._localhost`.
* **Instance-held ``httpx.AsyncClient``.** Constructed in ``__init__``,
  reused across ``chat()`` calls, closed via ``async __aexit__``. No
  per-call connection setup.
* **120-second default timeout.** Cold model load on Ollama can exceed 30s
  on first request; Shield's 5s default is for classification (small
  input), not chat completions. Per-call ``timeout_s`` overrides.
* **No model lifecycle management.** This adapter never calls ``/api/pull``,
  ``/api/tags``, or any other model-management endpoint. Sotto Desktop
  owns model installation and warm-up; if the requested model isn't
  loaded, the upstream 404 surfaces and ``chat()`` raises.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import TracebackType
from typing import TYPE_CHECKING, Any

from ogentic_router.adapters._localhost import _validate_localhost
from ogentic_router.adapters._sse import aiter_sse_deltas
from ogentic_router.adapters.base import AdapterImportError

if TYPE_CHECKING:
    import httpx


class OllamaAdapter:
    """Local adapter for an embedded or stand-alone Ollama runtime.

    Parameters
    ----------
    endpoint:
        Loopback URL where Ollama is listening. Must be one of the
        :data:`~ogentic_router.adapters._localhost.LOOPBACK_HOSTS` (any port
        OK). Default ``"http://localhost:11434"`` matches Ollama's stock
        configuration.
    default_model:
        Model used when ``chat()`` is called without an explicit ``model=``.
        Default ``"llama3.2:3b"`` — small enough for a developer laptop,
        large enough to give policy-routed prompts a real chance.
    backend_id:
        Identifier used in policy rules (``policy.rules[].route``). The
        canonical ``examples/policy.yaml`` from OGE-579 uses
        ``"ollama-local"`` for this adapter; do not override unless you
        also update your policy file.
    timeout_s:
        Per-request timeout in seconds. Defaults to ``120.0`` so cold model
        loads don't trip the timeout. Per-call ``timeout_s`` argument on
        ``chat()`` overrides for individual requests.

    Raises
    ------
    LocalhostOnlyError
        If ``endpoint`` host is outside the loopback allow-list.
    AdapterImportError
        If the ``[local]`` extra (i.e. ``httpx``) isn't installed.

    Example
    -------
    >>> async with OllamaAdapter() as adapter:        # doctest: +SKIP
    ...     response = await adapter.chat(
    ...         messages=[{"role": "user", "content": "Hi."}],
    ...         max_tokens=50,
    ...     )
    ...     print(response["choices"][0]["message"]["content"])
    """

    is_local: bool = True

    def __init__(
        self,
        endpoint: str = "http://localhost:11434",
        *,
        default_model: str = "llama3.2:3b",
        backend_id: str = "ollama-local",
        timeout_s: float = 120.0,
    ) -> None:
        _validate_localhost(endpoint, kind="Ollama")

        try:
            import httpx
        except ImportError as exc:  # pragma: no cover — exercised via monkeypatch in tests
            raise AdapterImportError(
                "OllamaAdapter requires the 'httpx' SDK. "
                "Install with: pip install 'ogentic-router[local]'"
            ) from exc

        self.endpoint = endpoint.rstrip("/")
        self.backend_id = backend_id
        self._default_model = default_model
        self._default_timeout_s = timeout_s
        self._client: httpx.AsyncClient = httpx.AsyncClient(
            base_url=self.endpoint,
            timeout=timeout_s,
        )

    async def __aenter__(self) -> OllamaAdapter:
        """Enter the async context — returns ``self`` for ``async with`` use."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Close the underlying ``httpx.AsyncClient`` to release sockets."""
        await self._client.aclose()

    async def aclose(self) -> None:
        """Close the underlying client explicitly without using ``async with``.

        Callers that don't use the adapter as an async context manager
        should call ``aclose()`` to avoid leaked sockets.
        """
        await self._client.aclose()

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        stream: bool = False,
        timeout_s: float | None = None,
    ) -> Any | AsyncIterator[dict[str, Any]]:
        """Send a chat completion. Returns OpenAI-shaped dict (``stream=False``)
        or an async iterator of delta dicts (``stream=True``).

        Parameters
        ----------
        messages:
            OpenAI-shaped list of ``{"role": ..., "content": ...}`` dicts.
            Ollama's ``/v1/chat/completions`` endpoint accepts the OpenAI
            schema verbatim — no translation needed (unlike Anthropic).
        model:
            Overrides ``default_model`` for this call. ``None`` (default)
            falls back to the constructor's ``default_model``.
        max_tokens, temperature:
            Forwarded as-is. ``None`` omits them so Ollama uses its own
            defaults.
        stream:
            ``False`` returns the full response dict; ``True`` returns an
            async iterator yielding delta event dicts (see
            :func:`~ogentic_router.adapters._sse.aiter_sse_deltas`).
        timeout_s:
            Per-call timeout override. ``None`` uses the constructor's
            ``timeout_s``.

        Returns
        -------
        dict or async iterator
            OpenAI-shaped ``ChatCompletion`` dict, or async iterator of
            delta dicts when streaming. Shape pass-through — no
            normalization (see :mod:`ogentic_router.adapters.base`).
        """
        payload: dict[str, Any] = {
            "model": model or self._default_model,
            "messages": messages,
            "stream": stream,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if temperature is not None:
            payload["temperature"] = temperature

        request_timeout = timeout_s if timeout_s is not None else self._default_timeout_s

        if stream:
            return self._stream_chat(payload, request_timeout)

        response = await self._client.post(
            "/v1/chat/completions",
            json=payload,
            timeout=request_timeout,
        )
        response.raise_for_status()
        # Pass-through dict — let the caller (or upstream MCP/server layer)
        # decide whether to coerce into a typed model. v0.1 stays untyped.
        data: dict[str, Any] = response.json()
        return data

    async def _stream_chat(
        self,
        payload: dict[str, Any],
        request_timeout: float,
    ) -> AsyncIterator[dict[str, Any]]:
        """Stream delta events from the SSE response. Owns the stream lifecycle."""
        async with self._client.stream(
            "POST",
            "/v1/chat/completions",
            json=payload,
            timeout=request_timeout,
        ) as response:
            response.raise_for_status()
            async for delta in aiter_sse_deltas(response):
                yield delta
