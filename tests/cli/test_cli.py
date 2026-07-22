"""CliRunner tests for the ogentic-router CLI (OGE-585)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from ogentic_router.cli.main import cli


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


# ── version / help ───────────────────────────────────────────────────────────


def test_version(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert "ogentic-router" in result.output


def test_serve_help_mentions_mcp(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["serve", "--help"])
    assert result.exit_code == 0
    assert "--mcp" in result.output


def test_route_help_mentions_execute(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["route", "--help"])
    assert result.exit_code == 0
    assert "--execute" in result.output and "stdin" in result.output


# ── policies validate ────────────────────────────────────────────────────────


def test_policies_validate_ok(runner: CliRunner, policy_file: Path) -> None:
    result = runner.invoke(cli, ["policies", "validate", str(policy_file)])
    assert result.exit_code == 0
    assert "OK" in result.output


def test_policies_validate_bad_exits_2(runner: CliRunner, tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("version: 2\ndefault_backend: x\n", encoding="utf-8")
    result = runner.invoke(cli, ["policies", "validate", str(bad)])
    assert result.exit_code == 2
    assert "INVALID" in result.output


# ── policies show ────────────────────────────────────────────────────────────


def test_policies_show_lists_rules(runner: CliRunner, policy_file: Path) -> None:
    result = runner.invoke(cli, ["policies", "show", str(policy_file)])
    assert result.exit_code == 0
    assert "default_backend" in result.output
    assert "privilege-stays" in result.output  # rule ids appear (may be truncated)


# ── policies dry-run ─────────────────────────────────────────────────────────


def test_policies_dry_run_privileged_routes_local(runner: CliRunner, policy_file: Path) -> None:
    result = runner.invoke(
        cli, ["policies", "dry-run", str(policy_file), "--prompt", "attorney work product"]
    )
    assert result.exit_code == 0
    out = json.loads(result.output)
    assert out["backend_id"] == "ollama-local"
    assert out["rule_id"] == "privilege-stays-local"


def test_policies_dry_run_reads_stdin(runner: CliRunner, policy_file: Path) -> None:
    result = runner.invoke(
        cli, ["policies", "dry-run", str(policy_file)], input="what's the weather\n"
    )
    assert result.exit_code == 0
    assert json.loads(result.output)["backend_id"] == "openai-cloud"


def test_policies_dry_run_never_builds_an_adapter(
    runner: CliRunner, policy_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    called = {"n": 0}

    def _boom(**_kwargs: Any) -> Any:
        called["n"] += 1
        raise AssertionError("dry-run must not build an adapter")

    monkeypatch.setattr("ogentic_router.adapters.factory.build_adapter", _boom)
    result = runner.invoke(
        cli, ["policies", "dry-run", str(policy_file), "--prompt", "hello"]
    )
    assert result.exit_code == 0
    assert called["n"] == 0


# ── route ────────────────────────────────────────────────────────────────────


def test_route_prompt_prints_decision_json(runner: CliRunner, policy_file: Path) -> None:
    result = runner.invoke(
        cli, ["route", "--policy", str(policy_file), "--prompt", "privileged attorney memo"]
    )
    assert result.exit_code == 0
    out = json.loads(result.output)
    assert out["backend_id"] == "ollama-local"
    assert "output" not in out  # no --execute → decision only


def test_route_reads_stdin(runner: CliRunner, policy_file: Path) -> None:
    result = runner.invoke(
        cli, ["route", "--policy", str(policy_file)], input="ordinary question\n"
    )
    assert result.exit_code == 0
    assert json.loads(result.output)["backend_id"] == "openai-cloud"


def test_route_text_output(runner: CliRunner, policy_file: Path) -> None:
    result = runner.invoke(
        cli, ["route", "--policy", str(policy_file), "--prompt", "attorney", "--output", "text"]
    )
    assert result.exit_code == 0
    assert "backend:   ollama-local" in result.output


def test_route_no_prompt_no_stdin_exits_2(runner: CliRunner, policy_file: Path) -> None:
    result = runner.invoke(cli, ["route", "--policy", str(policy_file)], input="")
    assert result.exit_code == 2
    assert "no prompt" in result.output.lower()


def test_route_missing_config_and_policy_exits_2(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["route", "--prompt", "hi"])
    assert result.exit_code == 2
    assert "no policy or config" in result.output.lower()


def test_route_uses_router_config_env(
    runner: CliRunner, router_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ROUTER_CONFIG", str(router_config))
    result = runner.invoke(cli, ["route", "--prompt", "attorney memo"])
    assert result.exit_code == 0
    assert json.loads(result.output)["backend_id"] == "ollama-local"


def test_route_execute_dispatches_mocked_adapter(
    runner: CliRunner, router_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _FakeAdapter:
        backend_id = "ollama-local"
        is_local = True

        async def chat(self, messages: Any, **_kw: Any) -> Any:
            return {"choices": [{"message": {"content": "hello from the model"}}]}

    monkeypatch.setattr(
        "ogentic_router.adapters.factory.build_adapter", lambda **_kw: _FakeAdapter()
    )
    result = runner.invoke(
        cli,
        ["route", "--config", str(router_config), "--prompt", "attorney memo",
         "--execute", "--output", "text"],
    )
    assert result.exit_code == 0
    assert "hello from the model" in result.output


def test_route_execute_backend_not_in_config_exits_2(
    runner: CliRunner, router_config: Path
) -> None:
    # A low-sensitivity prompt routes to openai-cloud, which router_config
    # doesn't declare — --execute should refuse cleanly.
    result = runner.invoke(
        cli, ["route", "--config", str(router_config), "--prompt", "weather", "--execute"]
    )
    assert result.exit_code == 2
    assert "does not declare" in result.output
