"""router.yaml loader — RouterConfig / BackendConfig (OGE-583).

The server config is a SEPARATE file from policy.yaml. It adds:
  - policy_path: path to the routing-policy YAML
  - shield.profiles: list of Shield profiles (forwarded to Router)
  - audit: future audit configuration stanza
  - backends: list of backend connection configs

Schema::

    version: 1
    policy_path: policy.yaml     # required; resolved relative to config file
    shield:
      profiles: []               # optional; forwarded to Router
    audit: {}                    # optional; reserved for ogentic-audit
    backends:
      - id: openai-cloud
        kind: openai             # openai | anthropic | ollama | llamacpp
        api_key_env: OPENAI_API_KEY
        default_model: gpt-4o-mini
      - id: anthropic-cloud
        kind: anthropic
        api_key_env: ANTHROPIC_API_KEY
        default_model: claude-sonnet-4-5
      - id: ollama-local
        kind: ollama
        base_url: http://localhost:11434
        default_model: llama3
      - id: llamacpp-local
        kind: llamacpp
        base_url: http://localhost:8080

Raises :class:`~ogentic_router.errors.ConfigError` on any parse or
validation failure — callers should catch this at startup.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from ogentic_router.errors import ConfigError

# ─── Pydantic models ─────────────────────────────────────────────────────────


class BackendConfig(BaseModel):
    """Configuration for a single backend entry in router.yaml.

    The ``kind`` field drives adapter construction:

    - ``"openai"``   → :class:`~ogentic_router.adapters.openai_adapter.OpenAIAdapter`
    - ``"anthropic"`` → :class:`~ogentic_router.adapters.anthropic_adapter.AnthropicAdapter`
    - ``"ollama"``   → :class:`~ogentic_router.adapters.ollama_adapter.OllamaAdapter`
    - ``"llamacpp"`` → :class:`~ogentic_router.adapters.llamacpp_adapter.LlamaCppAdapter`
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    kind: Literal["openai", "anthropic", "ollama", "llamacpp"]
    api_key_env: str | None = None
    """Name of the environment variable that holds the API key.

    Resolved at adapter-construction time, not at config-load time, so the
    process can start before secrets are injected (useful in containerised
    deployments where the env var arrives via a secrets-manager sidecar).
    """
    base_url: str | None = None
    default_model: str | None = None


class ShieldConfig(BaseModel):
    """Optional shield stanza inside router.yaml."""

    model_config = ConfigDict(extra="forbid")

    profiles: list[str] = Field(default_factory=list)
    config: dict[str, Any] = Field(default_factory=dict)


class RouterConfig(BaseModel):
    """Top-level schema for a router.yaml file.

    ``version: Literal[1]`` pins the schema. A future v2 will widen the
    discriminator, load-path branches on the value — keeping this strict
    prevents silent version drift.
    """

    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    policy_path: str = Field(min_length=1)
    shield: ShieldConfig = Field(default_factory=ShieldConfig)
    audit: dict[str, Any] = Field(default_factory=dict)
    backends: list[BackendConfig] = Field(default_factory=list)


# ─── Loader ───────────────────────────────────────────────────────────────────


def load_router_config(path: str | Path) -> RouterConfig:
    """Parse and validate a router.yaml file.

    ``policy_path`` is resolved **relative to the config file's directory**
    when it is not absolute — mirrors :meth:`Router.from_yaml` so operators
    can ship a ``router.yaml`` + ``policy.yaml`` side-by-side.

    Raises:
        ConfigError: if the file cannot be read, is not valid YAML, does
            not match the :class:`RouterConfig` schema, or ``policy_path``
            is absent.
    """
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"Cannot read router config file {str(p)!r}: {exc}") from exc

    try:
        data: Any = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {str(p)!r}: {exc}") from exc

    if not isinstance(data, dict):
        raise ConfigError(
            f"Router config {str(p)!r} must be a YAML mapping at the top level, "
            f"got {type(data).__name__!r}"
        )

    # Resolve policy_path relative to config file directory when not absolute.
    if "policy_path" in data:
        pp = Path(data["policy_path"])
        if not pp.is_absolute():
            data = {**data, "policy_path": str((p.parent / pp).resolve())}

    try:

        return RouterConfig.model_validate(data)
    except Exception as exc:  # ValidationError subclasses ValueError
        raise ConfigError(f"router.yaml validation failed: {exc}") from exc


def resolve_api_key(backend: BackendConfig) -> str | None:
    """Read the API key for a backend from the environment.

    Returns ``None`` if the backend has no ``api_key_env`` set (local
    backends don't need a key).  Raises :class:`ConfigError` if
    ``api_key_env`` is set but the variable is not present in the
    environment.
    """
    if backend.api_key_env is None:
        return None
    value = os.environ.get(backend.api_key_env)
    if value is None:
        raise ConfigError(
            f"Backend {backend.id!r} requires env var {backend.api_key_env!r} "
            f"but it is not set. Export it before starting the server."
        )
    return value


__all__ = ["BackendConfig", "RouterConfig", "ShieldConfig", "load_router_config", "resolve_api_key"]
