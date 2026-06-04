"""Adapter Protocol and error hierarchy for ogentic-router backends.

Every backend the router can route to — cloud (OpenAI, Anthropic) and local
(Ollama, llama.cpp) — implements this Protocol. Backends are duck-typed; no
inheritance required, which keeps test doubles cheap (a ``FakeAdapter`` in a
test does not have to subclass anything).

Response shape is intentionally **pass-through**: each adapter returns its
provider's native response type (``openai.types.chat.ChatCompletion``,
``anthropic.types.Message``, etc.). v0.1 does not normalize across providers
because the MCP consumer (OGE-586) and OpenAI-shaped server (OGE-583) may
legitimately want provider-specific fields. Adding normalization later is
additive; ripping it out is breaking.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol, runtime_checkable


class AdapterError(Exception):
    """Base class for all adapter-level errors.

    Subclasses cover specific failure modes (missing optional extras,
    misconfiguration). Caller code should ``except AdapterError`` to catch
    every adapter failure.
    """


class AdapterImportError(AdapterError, ImportError):
    """Raised at adapter instantiation when the optional extra is missing.

    The base ``ogentic-router`` install does not pull cloud SDKs. Importing
    ``ogentic_router.adapters`` is fine without ``[cloud]`` installed — the
    failure surfaces only when the user tries to construct an adapter whose
    SDK is not on ``sys.path``. The message includes a ``pip install`` hint.
    """


class AdapterConfigError(AdapterError, ValueError):
    """Raised when an adapter is constructed with an invalid configuration.

    The flagship example is a ``base_url`` whose host falls outside the
    allow-list — that's a typo'd-config-could-exfiltrate-prompts class
    bug and must fail LOUDLY at construction, not silently route traffic
    to an attacker-controlled host.
    """


@runtime_checkable
class Adapter(Protocol):
    """The single Protocol every router backend implements.

    Attributes
    ----------
    backend_id:
        Stable identifier used in policy rules (``policy.rules[].route``).
        Cloud adapters default to ``"openai-cloud"`` / ``"anthropic-cloud"``;
        local adapters use ``"ollama-local"`` / ``"llamacpp-local"``. The
        constructor accepts an override so two ``OpenAIAdapter`` instances
        with different ``base_url``s can carry different ids.
    is_local:
        ``True`` for adapters that talk to a loopback-bound runtime, ``False``
        for adapters that talk to a remote cloud endpoint. The router uses
        this to enforce the local-first / cloud-redact-then-fallback flow.
    """

    backend_id: str
    is_local: bool

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        stream: bool = False,
    ) -> Any | AsyncIterator[Any]:
        """Send a chat completion to the backend.

        Parameters
        ----------
        messages:
            OpenAI-shaped list of ``{"role": ..., "content": ...}`` dicts.
            Anthropic adapter translates ``role: system`` entries into the
            Anthropic-native ``system=`` param before sending.
        model:
            Overrides the adapter's default model when provided.
        max_tokens:
            Hard ceiling on completion length. Forwarded to the provider.
        temperature:
            Sampling temperature, forwarded as-is.
        stream:
            When ``False`` (default), returns the full provider-native
            response object. When ``True``, returns an async iterator that
            yields provider-native delta events.

        Returns
        -------
        Provider-native response type or async iterator of delta events.
        Intentionally not normalized — see module docstring.
        """
        ...
