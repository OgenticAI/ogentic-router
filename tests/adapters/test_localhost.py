"""Loopback-only enforcement — verbatim contract with Shield's Layer 3.

These tests pin the ``_validate_localhost`` behaviour against the same
host set ``ogentic-shield`` enforces, so drift between Shield and Router
on what counts as "localhost" raises a test failure rather than a
silent privacy regression.
"""

from __future__ import annotations

import pytest

from ogentic_router.adapters import LocalhostOnlyError
from ogentic_router.adapters._localhost import (
    LOOPBACK_HOSTS,
    _validate_localhost,
)
from ogentic_router.adapters.base import AdapterError


def test_localhost_only_error_is_adapter_error() -> None:
    """``LocalhostOnlyError`` is a subclass of ``AdapterError`` so callers
    can ``except AdapterError`` to catch every adapter failure mode."""
    assert issubclass(LocalhostOnlyError, AdapterError)


def test_loopback_hosts_matches_shield() -> None:
    """The accepted-host set is the same one ``ogentic-shield`` uses.

    If Shield ever expands its loopback allow-list, this test should fail
    until the Router intentionally follows. Verbatim copy is the contract.
    """
    assert LOOPBACK_HOSTS == frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://localhost:11434",
        "http://localhost:8080",
        "https://localhost:11434",
        "http://127.0.0.1:11434",
        "http://127.0.0.1:8080",
        "http://[::1]:11434",
        "http://[::1]:8080",
    ],
)
def test_accepts_loopback_endpoints(endpoint: str) -> None:
    """Any port is fine; both IPv4 and bracketed-IPv6 loopback accepted."""
    _validate_localhost(endpoint, kind="Ollama")  # must not raise


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://my-ollama.internal:11434",
        "http://10.0.0.5:11434",
        "http://192.168.1.50:8080",
        "https://api.openai.com",
        "http://example.com:11434",
        "http://0.0.0.0:11434",  # bind-all is NOT a loopback address
    ],
)
def test_rejects_non_loopback_endpoints(endpoint: str) -> None:
    """Internal DNS, private CIDRs, and 0.0.0.0 all raise."""
    with pytest.raises(LocalhostOnlyError) as exc:
        _validate_localhost(endpoint, kind="Ollama")
    msg = str(exc.value)
    assert "Ollama" in msg
    assert "loopback" in msg.lower()
    # Sanity: the accepted-host list is referenced in the error so the user
    # knows what's allowed.
    assert "127.0.0.1" in msg


@pytest.mark.parametrize(
    "endpoint",
    [
        "ftp://localhost:11434",
        "ws://localhost:11434",
        "file:///etc/passwd",
        "localhost:11434",  # no scheme
    ],
)
def test_rejects_non_http_schemes(endpoint: str) -> None:
    """Only ``http``/``https`` accepted; the message points at the scheme."""
    with pytest.raises(LocalhostOnlyError) as exc:
        _validate_localhost(endpoint, kind="Ollama")
    assert "http(s)" in str(exc.value)


def test_kind_is_interpolated_into_error() -> None:
    """The ``kind`` arg shows up in the error so users see "llama.cpp" not "Ollama"
    in llama.cpp-specific failures."""
    with pytest.raises(LocalhostOnlyError) as exc:
        _validate_localhost("http://10.0.0.5:8080", kind="llama.cpp")
    assert "llama.cpp" in str(exc.value)


def test_case_insensitive_host() -> None:
    """``LOCALHOST`` should be accepted (host normalized to lowercase)."""
    _validate_localhost("http://LOCALHOST:11434", kind="Ollama")  # must not raise
