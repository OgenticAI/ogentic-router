"""One place that turns a backend spec into a constructed adapter.

Both the OpenAI-shaped server and the CLI build adapters from the same
``router.yaml`` ``backends[]`` entries. Keeping the construction here — rather
than duplicated at each call site — means the address-key translation (the
config says ``base_url`` for every kind, but the *local* adapters take
``endpoint``) lives in exactly one spot and can't drift between callers.

Import-light on purpose: no FastAPI, no server package. The concrete adapter
SDKs are lazy-imported by the adapters themselves.
"""

from __future__ import annotations

from typing import Any

from .anthropic_adapter import AnthropicAdapter
from .base import Adapter
from .llamacpp_adapter import LlamaCppAdapter
from .ollama_adapter import OllamaAdapter
from .openai_adapter import OpenAIAdapter

# Backend kinds that run on-device (loopback-enforced by their adapters).
LOCAL_KINDS = frozenset({"ollama", "llamacpp"})


def build_adapter(
    *,
    kind: str,
    backend_id: str,
    base_url: str | None = None,
    default_model: str | None = None,
    api_key: str | None = None,
) -> Adapter:
    """Construct one adapter from primitive backend fields.

    Args:
        kind: One of ``openai``, ``anthropic``, ``ollama``, ``llamacpp``.
        backend_id: The policy-facing backend id (matches ``route:`` values).
        base_url: The backend address. For **cloud** kinds it maps to the
            adapter's ``base_url`` (host-allowlisted); for **local** kinds it
            maps to ``endpoint`` (loopback-enforced) — the single translation
            this module exists to own.
        default_model: Optional default model id for the adapter.
        api_key: Resolved API key (cloud kinds only).

    Raises:
        ValueError: on an unknown ``kind``.
    """
    if kind == "openai":
        kwargs: dict[str, Any] = {"api_key": api_key or "", "backend_id": backend_id}
        if default_model:
            kwargs["default_model"] = default_model
        if base_url:
            kwargs["base_url"] = base_url
        return OpenAIAdapter(**kwargs)

    if kind == "anthropic":
        kwargs = {"api_key": api_key or "", "backend_id": backend_id}
        if default_model:
            kwargs["default_model"] = default_model
        if base_url:
            kwargs["base_url"] = base_url
        return AnthropicAdapter(**kwargs)

    if kind == "ollama":
        kwargs = {"backend_id": backend_id}
        if base_url:
            kwargs["endpoint"] = base_url  # local adapters name it `endpoint`
        if default_model:
            kwargs["default_model"] = default_model
        return OllamaAdapter(**kwargs)

    if kind == "llamacpp":
        kwargs = {"backend_id": backend_id}
        if base_url:
            kwargs["endpoint"] = base_url
        if default_model:
            kwargs["default_model"] = default_model
        return LlamaCppAdapter(**kwargs)

    raise ValueError(
        f"unknown backend kind {kind!r} "
        "(expected 'openai', 'anthropic', 'ollama', or 'llamacpp')."
    )


__all__ = ["LOCAL_KINDS", "build_adapter"]
