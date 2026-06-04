"""Backend adapters for ogentic-router.

Re-exports the public surface:

- ``Adapter`` — Protocol every backend implements
- ``AdapterError`` / ``AdapterImportError`` / ``AdapterConfigError`` — errors
- ``LocalhostOnlyError`` — loopback-only enforcement failure (local adapters)
- ``OpenAIAdapter`` / ``AnthropicAdapter`` — cloud adapters (require ``[cloud]``)
- ``OllamaAdapter`` / ``LlamaCppAdapter`` — local adapters (require ``[local]``)

Importing this module is safe without ``[cloud]`` or ``[local]`` installed;
the provider SDKs (and ``httpx`` for the local adapters) are lazy-imported
inside each adapter's ``__init__``. The ``ImportError`` surfaces at adapter
construction, not at package import.
"""

from __future__ import annotations

from ogentic_router.adapters._localhost import LocalhostOnlyError
from ogentic_router.adapters.anthropic_adapter import AnthropicAdapter
from ogentic_router.adapters.base import (
    Adapter,
    AdapterConfigError,
    AdapterError,
    AdapterImportError,
)
from ogentic_router.adapters.llamacpp_adapter import LlamaCppAdapter
from ogentic_router.adapters.ollama_adapter import OllamaAdapter
from ogentic_router.adapters.openai_adapter import OpenAIAdapter

__all__ = [
    "Adapter",
    "AdapterConfigError",
    "AdapterError",
    "AdapterImportError",
    "AnthropicAdapter",
    "LlamaCppAdapter",
    "LocalhostOnlyError",
    "OllamaAdapter",
    "OpenAIAdapter",
]
