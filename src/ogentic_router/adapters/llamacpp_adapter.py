"""llama.cpp local adapter for ogentic-router.

Talks to a loopback-bound ``./server`` (built from ``llama.cpp``) over its
OpenAI-compatible ``POST /v1/chat/completions`` endpoint. Same wire
protocol as :class:`~ogentic_router.adapters.ollama_adapter.OllamaAdapter`,
same loopback-only enforcement, same SSE streaming parser.

What's different from the Ollama adapter:

* ``default_model = None`` because llama.cpp's ``./server`` pins the model
  at launch time (``./server -m models/foo.gguf``). Passing ``"model":
  "anything"`` in the request body is ignored by the server. We pass
  through whatever the caller specifies (in case a future ``./server``
  version honours it), but we don't claim a default we don't control.
* **Defensive response parsing.** The researcher flagged that
  ``./server``'s response isn't 100% OpenAI-parity: ``usage`` may be
  absent on some builds, ``finish_reason`` enum values vary
  (``"stop"`` / ``"end_turn"`` / ``"length"``), and the top-level ``id``
  field is occasionally missing. This adapter never accesses those
  fields directly — it returns the dict pass-through and lets callers
  decide what to do with whatever shape arrives.
* Default port ``8080`` matches ``./server``'s out-of-the-box behaviour.

Beyond that, the file mirrors ``ollama_adapter.py`` line-by-line to keep
maintenance cheap. If you patch one, patch the other.
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


class LlamaCppAdapter:
    """Local adapter for a stand-alone ``llama.cpp ./server`` runtime.

    Parameters
    ----------
    endpoint:
        Loopback URL where ``./server`` is listening. Must be one of the
        :data:`~ogentic_router.adapters._localhost.LOOPBACK_HOSTS` (any port
        OK). Default ``"http://localhost:8080"`` matches ``./server``'s
        stock port.
    default_model:
        ``None`` by default because ``./server`` pins the model at launch.
        Callers may still pass ``model="foo"`` on ``chat()`` — it'll be
        forwarded, but most ``./server`` builds ignore it. Set to a real
        string if you're running a build that honours the parameter.
    backend_id:
        Identifier used in policy rules. The canonical
        ``examples/policy.yaml`` from OGE-579 uses ``"llamacpp-local"``.
    timeout_s:
        Per-request timeout in seconds. Defaults to ``120.0`` — same
        rationale as Ollama (cold model load can take a while).

    Raises
    ------
    LocalhostOnlyError
        If ``endpoint`` host is outside the loopback allow-list.
    AdapterImportError
        If the ``[local]`` extra (i.e. ``httpx``) isn't installed.
    """

    is_local: bool = True

    def __init__(
        self,
        endpoint: str = "http://localhost:8080",
        *,
        default_model: str | None = None,
        backend_id: str = "llamacpp-local",
        timeout_s: float = 120.0,
    ) -> None:
        _validate_localhost(endpoint, kind="llama.cpp")

        try:
            import httpx
        except ImportError as exc:  # pragma: no cover — exercised via monkeypatch in tests
            raise AdapterImportError(
                "LlamaCppAdapter requires the 'httpx' SDK. "
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

    async def __aenter__(self) -> LlamaCppAdapter:
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
        """Close the underlying client explicitly without using ``async with``."""
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

        Defensive parsing note: the returned dict may lack ``usage``, may
        carry an unfamiliar ``finish_reason`` value, or may be missing the
        top-level ``id`` field. This adapter pass-throughs whatever
        ``./server`` returns — handle these shapes in the caller.

        See :meth:`OllamaAdapter.chat` for the full parameter table.
        """
        payload: dict[str, Any] = {
            # We forward whatever ``model`` resolves to even if it's None,
            # because some ``./server`` builds accept it and others ignore
            # it. ``None`` becomes ``null`` in JSON, which ``./server``
            # treats the same as "use the loaded model".
            "messages": messages,
            "stream": stream,
        }
        resolved_model = model if model is not None else self._default_model
        if resolved_model is not None:
            payload["model"] = resolved_model
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
        data: dict[str, Any] = response.json()
        return data

    async def _stream_chat(
        self,
        payload: dict[str, Any],
        request_timeout: float,
    ) -> AsyncIterator[dict[str, Any]]:
        """Stream delta events from the SSE response."""
        async with self._client.stream(
            "POST",
            "/v1/chat/completions",
            json=payload,
            timeout=request_timeout,
        ) as response:
            response.raise_for_status()
            async for delta in aiter_sse_deltas(response):
                yield delta
