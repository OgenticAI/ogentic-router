"""Tests for the Adapter Protocol and error hierarchy."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from ogentic_router.adapters import (
    Adapter,
    AdapterConfigError,
    AdapterError,
    AdapterImportError,
)


class _FakeAdapter:
    """Stand-in that satisfies the Protocol without subclassing."""

    backend_id = "fake"
    is_local = True

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        stream: bool = False,
    ) -> Any | AsyncIterator[Any]:
        return {"messages": messages, "model": model}


def test_protocol_runtime_checkable() -> None:
    """A duck-typed class satisfies the Adapter Protocol with isinstance()."""
    fake = _FakeAdapter()
    assert isinstance(fake, Adapter)


def test_protocol_rejects_missing_methods() -> None:
    """Classes without ``chat`` are not Adapters."""

    class Missing:
        backend_id = "x"
        is_local = False

    assert not isinstance(Missing(), Adapter)


def test_error_hierarchy() -> None:
    """AdapterImportError is both AdapterError and ImportError; same idea for AdapterConfigError."""
    assert issubclass(AdapterImportError, AdapterError)
    assert issubclass(AdapterImportError, ImportError)
    assert issubclass(AdapterConfigError, AdapterError)
    assert issubclass(AdapterConfigError, ValueError)


def test_adapter_error_catches_subclasses() -> None:
    """A bare ``except AdapterError`` catches every adapter failure."""
    with pytest.raises(AdapterError):
        raise AdapterImportError("nope")
    with pytest.raises(AdapterError):
        raise AdapterConfigError("nope")
