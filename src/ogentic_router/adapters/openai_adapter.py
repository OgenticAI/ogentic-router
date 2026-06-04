"""OpenAI cloud adapter for ogentic-router.

Wraps ``openai.AsyncOpenAI`` to satisfy the ``Adapter`` Protocol. The SDK
manages its own httpx pool, so the client is constructed once in ``__init__``
and reused across ``chat()`` calls — no context manager required.

The OpenAI SDK is **lazy-imported inside __init__** (not at module load).
``ogentic_router.adapters`` itself must be importable without ``[cloud]``
installed; the failure surfaces only when the user actually tries to
construct an adapter.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from ogentic_router.adapters._allowlist import ALLOWED_OPENAI_HOSTS, _validate_host
from ogentic_router.adapters.base import AdapterImportError

if TYPE_CHECKING:
    from openai import AsyncOpenAI  # noqa: F401


class OpenAIAdapter:
    """Cloud adapter for OpenAI's chat completions API.

    Parameters
    ----------
    api_key:
        OpenAI API key. Passed straight to ``AsyncOpenAI``.
    base_url:
        Override the SDK's default endpoint. Validated against
        ``ALLOWED_OPENAI_HOSTS`` (+ ``OGENTIC_ROUTER_ALLOWED_OPENAI_HOSTS``
        env var) at construction. A host outside the allow-list raises
        ``AdapterConfigError`` — the library refuses to silently route
        prompts to an attacker-controlled host.
    default_model:
        Model used when ``chat()`` is called without an explicit ``model=``.
        Defaults to ``"gpt-4o-mini"``.
    backend_id:
        Identifier used in policy rules. Default ``"openai-cloud"``; override
        when running two ``OpenAIAdapter`` instances against different
        backends (e.g. an in-house proxy + the public endpoint).
    """

    is_local: bool = False

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str | None = None,
        default_model: str = "gpt-4o-mini",
        backend_id: str = "openai-cloud",
    ) -> None:
        _validate_host(base_url, ALLOWED_OPENAI_HOSTS, "OPENAI")

        try:
            from openai import AsyncOpenAI
        except ImportError as exc:  # pragma: no cover — exercised via monkeypatch in tests
            raise AdapterImportError(
                "OpenAIAdapter requires the 'openai' SDK. "
                "Install with: pip install 'ogentic-router[cloud]'"
            ) from exc

        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self.backend_id = backend_id
        self._default_model = default_model

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        stream: bool = False,
    ) -> Any | AsyncIterator[Any]:
        """Send a chat completion. Returns provider-native ``ChatCompletion``.

        When ``stream=True`` returns an ``AsyncStream[ChatCompletionChunk]``
        (iterate with ``async for``). All shape pass-through — no
        normalization. See ``adapters.base`` module docstring for rationale.
        """
        kwargs: dict[str, Any] = {
            "model": model or self._default_model,
            "messages": messages,
            "stream": stream,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if temperature is not None:
            kwargs["temperature"] = temperature

        return await self._client.chat.completions.create(**kwargs)
