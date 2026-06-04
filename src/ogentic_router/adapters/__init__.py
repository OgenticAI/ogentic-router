"""Backend adapters for ogentic-router.

Re-exports the public surface:

- ``Adapter`` — Protocol every backend implements
- ``AdapterError`` / ``AdapterImportError`` / ``AdapterConfigError`` — errors
- ``OpenAIAdapter`` / ``AnthropicAdapter`` — cloud adapters (require ``[cloud]``)

Importing this module is safe without ``[cloud]`` installed; the cloud
adapter SDKs are lazy-imported inside each adapter's ``__init__``. The
``ImportError`` surfaces at adapter construction, not at package import.
"""

from __future__ import annotations

from ogentic_router.adapters.anthropic_adapter import AnthropicAdapter
from ogentic_router.adapters.base import (
    Adapter,
    AdapterConfigError,
    AdapterError,
    AdapterImportError,
)
from ogentic_router.adapters.openai_adapter import OpenAIAdapter

__all__ = [
    "Adapter",
    "AdapterConfigError",
    "AdapterError",
    "AdapterImportError",
    "AnthropicAdapter",
    "OpenAIAdapter",
]
