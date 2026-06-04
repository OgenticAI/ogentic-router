"""Tests for cloud-adapter host allow-list enforcement."""

from __future__ import annotations

import pytest

from ogentic_router.adapters._allowlist import (
    ALLOWED_ANTHROPIC_HOSTS,
    ALLOWED_OPENAI_HOSTS,
    _validate_host,
)
from ogentic_router.adapters.base import AdapterConfigError


def test_accepts_none_base_url() -> None:
    """``None`` means 'use the SDK default endpoint' and short-circuits."""
    _validate_host(None, ALLOWED_OPENAI_HOSTS, "OPENAI")
    _validate_host(None, ALLOWED_ANTHROPIC_HOSTS, "ANTHROPIC")


def test_accepts_default_host() -> None:
    _validate_host("https://api.openai.com", ALLOWED_OPENAI_HOSTS, "OPENAI")
    _validate_host("https://api.anthropic.com/v1", ALLOWED_ANTHROPIC_HOSTS, "ANTHROPIC")


def test_rejects_host_outside_allowlist() -> None:
    """A typo'd subdomain like ``api.openai.com.evil.invalid`` must raise."""
    with pytest.raises(AdapterConfigError) as exc:
        _validate_host("https://evil.invalid", ALLOWED_OPENAI_HOSTS, "OPENAI")
    msg = str(exc.value)
    assert "evil.invalid" in msg
    assert "OGENTIC_ROUTER_ALLOWED_OPENAI_HOSTS" in msg


def test_rejects_lookalike_host() -> None:
    """A confusingly similar host (``api.openai.com.evil``) still fails."""
    with pytest.raises(AdapterConfigError):
        _validate_host("https://api.openai.com.evil.invalid", ALLOWED_OPENAI_HOSTS, "OPENAI")


def test_rejects_unparseable_url() -> None:
    """A bare string with no host raises a config error."""
    with pytest.raises(AdapterConfigError):
        _validate_host("not-a-url", ALLOWED_OPENAI_HOSTS, "OPENAI")


def test_env_override_extends_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    """The env var lets deployers add an in-house proxy without losing the default."""
    monkeypatch.setenv("OGENTIC_ROUTER_ALLOWED_OPENAI_HOSTS", "api.example.com, api.openai.com")
    # extension host accepted
    _validate_host("https://api.example.com", ALLOWED_OPENAI_HOSTS, "OPENAI")
    # default still accepted
    _validate_host("https://api.openai.com", ALLOWED_OPENAI_HOSTS, "OPENAI")


def test_env_override_only_affects_matching_kind(monkeypatch: pytest.MonkeyPatch) -> None:
    """Setting the OpenAI env var does not unlock Anthropic hosts."""
    monkeypatch.setenv("OGENTIC_ROUTER_ALLOWED_OPENAI_HOSTS", "api.example.com")
    with pytest.raises(AdapterConfigError):
        _validate_host("https://api.example.com", ALLOWED_ANTHROPIC_HOSTS, "ANTHROPIC")


def test_env_override_handles_empty_and_whitespace(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty/whitespace entries in the env var are dropped, not parsed as hosts."""
    monkeypatch.setenv("OGENTIC_ROUTER_ALLOWED_OPENAI_HOSTS", "  , ,api.example.com,  ")
    _validate_host("https://api.example.com", ALLOWED_OPENAI_HOSTS, "OPENAI")
