"""Regression tests for the server's config → adapter construction.

Guards the bug found by the OGE-587 first-run walkthrough: the config schema
names every backend's address ``base_url``, but the *local* adapters
(Ollama, llama.cpp) name that constructor argument ``endpoint``. The server was
forwarding ``base_url=`` to them, so it died at startup with a ``TypeError`` the
moment a local backend was configured — i.e. the documented
``ROUTER_CONFIG=examples/router.yaml ogentic-router serve`` path was broken.
"""

from __future__ import annotations

import pytest

from ogentic_router.server.app import _build_adapters
from ogentic_router.server.config import BackendConfig, RouterConfig


def _config(*backends: BackendConfig) -> RouterConfig:
    return RouterConfig(version=1, policy_path="policy.yaml", backends=list(backends))


def test_ollama_backend_builds_with_base_url() -> None:
    """A base_url in config must reach OllamaAdapter as `endpoint`."""
    cfg = _config(
        BackendConfig(
            id="ollama-local",
            kind="ollama",
            base_url="http://localhost:11434",
            default_model="llama3.2:3b",
        )
    )
    adapters = _build_adapters(cfg)
    adapter = adapters["ollama-local"]
    assert adapter.backend_id == "ollama-local"
    assert adapter.is_local is True


def test_llamacpp_backend_builds_with_base_url() -> None:
    cfg = _config(
        BackendConfig(id="llamacpp-local", kind="llamacpp", base_url="http://127.0.0.1:8080")
    )
    adapters = _build_adapters(cfg)
    assert adapters["llamacpp-local"].is_local is True


def test_local_backend_builds_without_base_url() -> None:
    """Omitting base_url falls back to the adapter's own loopback default."""
    cfg = _config(BackendConfig(id="ollama-local", kind="ollama"))
    adapters = _build_adapters(cfg)
    assert adapters["ollama-local"].is_local is True


def test_non_loopback_local_backend_is_rejected() -> None:
    """The loopback guard still fires through the server's construction path."""
    from ogentic_router.adapters import LocalhostOnlyError

    cfg = _config(BackendConfig(id="ollama-remote", kind="ollama", base_url="http://10.0.0.5:11434"))
    with pytest.raises(LocalhostOnlyError):
        _build_adapters(cfg)


def test_cloud_backend_still_uses_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cloud adapters genuinely take `base_url` — don't over-translate."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    cfg = _config(
        BackendConfig(
            id="openai-cloud",
            kind="openai",
            api_key_env="OPENAI_API_KEY",
            default_model="gpt-4o-mini",
        )
    )
    adapters = _build_adapters(cfg)
    assert adapters["openai-cloud"].is_local is False
