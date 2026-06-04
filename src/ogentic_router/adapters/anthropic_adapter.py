"""Anthropic cloud adapter for ogentic-router.

Wraps ``anthropic.AsyncAnthropic`` to satisfy the ``Adapter`` Protocol. The
key bit of work this adapter does — beyond what OpenAIAdapter does — is the
``role: system`` translation: the Anthropic Messages API takes ``system=``
as a separate top-level parameter, not inlined into the messages list.
Translation lives in ``_anthropic_translate`` for testability.

The Anthropic SDK is **lazy-imported inside __init__** (not at module load),
mirroring ``OpenAIAdapter``. The base ``ogentic-router`` install does not
pull ``[cloud]``; failure surfaces at adapter construction, not at package
import.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from ogentic_router.adapters._allowlist import ALLOWED_ANTHROPIC_HOSTS, _validate_host
from ogentic_router.adapters._anthropic_translate import extract_system_and_messages
from ogentic_router.adapters.base import AdapterImportError

if TYPE_CHECKING:
    from anthropic import AsyncAnthropic  # noqa: F401

# Anthropic's Messages API requires ``max_tokens``. When the caller doesn't
# pass one we fall back to this default; tuned to be comfortably larger than
# typical chat replies but well below the model ceiling.
_DEFAULT_MAX_TOKENS = 1024


class AnthropicAdapter:
    """Cloud adapter for Anthropic's Messages API.

    Parameters
    ----------
    api_key:
        Anthropic API key. Passed straight to ``AsyncAnthropic``.
    base_url:
        Override the SDK's default endpoint. Validated against
        ``ALLOWED_ANTHROPIC_HOSTS`` (+
        ``OGENTIC_ROUTER_ALLOWED_ANTHROPIC_HOSTS`` env var) at construction.
        A host outside the allow-list raises ``AdapterConfigError``.
    default_model:
        Model used when ``chat()`` is called without an explicit ``model=``.
        Defaults to ``"claude-sonnet-4-5"``.
    backend_id:
        Identifier used in policy rules. Default ``"anthropic-cloud"``.
    """

    is_local: bool = False

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str | None = None,
        default_model: str = "claude-sonnet-4-5",
        backend_id: str = "anthropic-cloud",
    ) -> None:
        _validate_host(base_url, ALLOWED_ANTHROPIC_HOSTS, "ANTHROPIC")

        try:
            from anthropic import AsyncAnthropic
        except ImportError as exc:  # pragma: no cover — exercised via monkeypatch in tests
            raise AdapterImportError(
                "AnthropicAdapter requires the 'anthropic' SDK. "
                "Install with: pip install 'ogentic-router[cloud]'"
            ) from exc

        client_kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url is not None:
            client_kwargs["base_url"] = base_url
        self._client = AsyncAnthropic(**client_kwargs)
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
        """Send a Messages API request. Returns provider-native ``Message``.

        Translates the OpenAI-shaped ``messages`` list — any ``role: system``
        entries are extracted (joined with ``"\\n\\n"`` if multiple) into the
        Anthropic-native ``system=`` parameter; the rest of the messages
        flow through unchanged.

        When ``stream=True`` returns an ``AsyncStream[RawMessageStreamEvent]``.
        Response shape is pass-through — see ``adapters.base`` module
        docstring for rationale.
        """
        system, msgs_without_system = extract_system_and_messages(messages)

        kwargs: dict[str, Any] = {
            "model": model or self._default_model,
            "messages": msgs_without_system,
            "max_tokens": max_tokens if max_tokens is not None else _DEFAULT_MAX_TOKENS,
            "stream": stream,
        }
        if system is not None:
            kwargs["system"] = system
        if temperature is not None:
            kwargs["temperature"] = temperature

        return await self._client.messages.create(**kwargs)
