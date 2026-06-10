"""Tests for router.yaml config loading (OGE-583).

AC:
- Valid router.yaml parses into a RouterConfig with correct fields.
- Missing required fields raise ConfigError.
- Invalid YAML raises ConfigError.
- policy_path is resolved relative to the config file's directory.
- Unknown backend kind raises a Pydantic ValidationError (wrapped as ConfigError).
- resolve_api_key reads from the environment and raises ConfigError when missing.
"""

from __future__ import annotations

import os
import textwrap
from pathlib import Path

import pytest

from ogentic_router.errors import ConfigError
from ogentic_router.server.config import (
    BackendConfig,
    RouterConfig,
    load_router_config,
    resolve_api_key,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


class TestLoadRouterConfig:
    """load_router_config() tests."""

    def test_load_canonical_fixture(self) -> None:
        """AC: the canonical test fixture parses to a valid RouterConfig."""
        cfg = load_router_config(FIXTURES_DIR / "router.yaml")
        assert isinstance(cfg, RouterConfig)
        assert cfg.version == 1
        assert len(cfg.backends) == 3

    def test_backends_parsed_correctly(self) -> None:
        """AC: backend fields (id, kind, api_key_env, default_model) parse correctly."""
        cfg = load_router_config(FIXTURES_DIR / "router.yaml")
        backend_ids = [b.id for b in cfg.backends]
        assert "openai-cloud" in backend_ids
        assert "anthropic-cloud" in backend_ids
        assert "ollama-local" in backend_ids

    def test_openai_backend_fields(self) -> None:
        """AC: openai backend has api_key_env and default_model."""
        cfg = load_router_config(FIXTURES_DIR / "router.yaml")
        openai_backend = next(b for b in cfg.backends if b.id == "openai-cloud")
        assert openai_backend.kind == "openai"
        assert openai_backend.api_key_env == "OPENAI_API_KEY"
        assert openai_backend.default_model == "gpt-4o-mini"

    def test_missing_file_raises_config_error(self, tmp_path: Path) -> None:
        """AC: non-existent file raises ConfigError."""
        with pytest.raises(ConfigError, match="Cannot read"):
            load_router_config(tmp_path / "nonexistent.yaml")

    def test_invalid_yaml_raises_config_error(self, tmp_path: Path) -> None:
        """AC: malformed YAML raises ConfigError."""
        bad = tmp_path / "bad.yaml"
        bad.write_text("version: 1\npolicy_path: [\nunclosed bracket", encoding="utf-8")
        with pytest.raises(ConfigError, match="Invalid YAML"):
            load_router_config(bad)

    def test_missing_version_raises_config_error(self, tmp_path: Path) -> None:
        """AC: missing `version` raises ConfigError (Pydantic validation)."""
        yaml_content = textwrap.dedent("""\
            policy_path: policy.yaml
            backends: []
        """)
        f = tmp_path / "router.yaml"
        f.write_text(yaml_content, encoding="utf-8")
        with pytest.raises(ConfigError):
            load_router_config(f)

    def test_missing_policy_path_raises_config_error(self, tmp_path: Path) -> None:
        """AC: missing `policy_path` raises ConfigError."""
        yaml_content = textwrap.dedent("""\
            version: 1
            backends: []
        """)
        f = tmp_path / "router.yaml"
        f.write_text(yaml_content, encoding="utf-8")
        with pytest.raises(ConfigError):
            load_router_config(f)

    def test_policy_path_resolved_relative_to_config(self, tmp_path: Path) -> None:
        """AC: relative policy_path is resolved relative to the config file's directory."""
        yaml_content = textwrap.dedent("""\
            version: 1
            policy_path: policy.yaml
            backends: []
        """)
        config_file = tmp_path / "router.yaml"
        config_file.write_text(yaml_content, encoding="utf-8")

        cfg = load_router_config(config_file)
        # policy_path should be an absolute path under tmp_path
        assert Path(cfg.policy_path).is_absolute()
        assert str(tmp_path) in cfg.policy_path

    def test_top_level_non_mapping_raises_config_error(self, tmp_path: Path) -> None:
        """AC: YAML that is not a dict at the top level raises ConfigError."""
        f = tmp_path / "list.yaml"
        f.write_text("- item1\n- item2\n", encoding="utf-8")
        with pytest.raises(ConfigError, match="mapping"):
            load_router_config(f)

    def test_unknown_backend_kind_raises_config_error(self, tmp_path: Path) -> None:
        """AC: unknown backend kind raises ConfigError (Pydantic validation)."""
        yaml_content = textwrap.dedent("""\
            version: 1
            policy_path: policy.yaml
            backends:
              - id: bad-backend
                kind: unknown_provider
        """)
        f = tmp_path / "router.yaml"
        f.write_text(yaml_content, encoding="utf-8")
        with pytest.raises(ConfigError):
            load_router_config(f)


class TestResolveApiKey:
    """resolve_api_key() tests."""

    def test_returns_none_when_no_api_key_env(self) -> None:
        """AC: backend with no api_key_env returns None."""
        backend = BackendConfig(id="ollama-local", kind="ollama", base_url="http://localhost:11434")
        assert resolve_api_key(backend) is None

    def test_reads_from_environment(self) -> None:
        """AC: api_key_env is resolved from the environment."""
        backend = BackendConfig(id="openai-cloud", kind="openai", api_key_env="TEST_OPENAI_KEY_XYZ")
        os.environ["TEST_OPENAI_KEY_XYZ"] = "sk-test-value"
        try:
            key = resolve_api_key(backend)
            assert key == "sk-test-value"
        finally:
            del os.environ["TEST_OPENAI_KEY_XYZ"]

    def test_raises_config_error_when_env_var_missing(self) -> None:
        """AC: missing env var raises ConfigError with helpful message."""
        backend = BackendConfig(id="openai-cloud", kind="openai", api_key_env="MISSING_API_KEY_ZZYZ")
        # Ensure the var is not set.
        os.environ.pop("MISSING_API_KEY_ZZYZ", None)
        with pytest.raises(ConfigError, match="MISSING_API_KEY_ZZYZ"):
            resolve_api_key(backend)
