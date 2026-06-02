"""Smoke tests for the v0.0.1 scaffold.

Tightly scoped: prove the package imports, the version is exposed, and the
CLI registers cleanly. Real test surface lands with each v0.1 build ticket.
"""

from __future__ import annotations

from click.testing import CliRunner

import ogentic_router
from ogentic_router.cli.main import cli


def test_version_is_exposed() -> None:
    assert isinstance(ogentic_router.__version__, str)
    assert ogentic_router.__version__.startswith("0.0.1")


def test_cli_help_runs_clean() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "ogentic-router" in result.output.lower()


def test_cli_version_flag_runs_clean() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert ogentic_router.__version__ in result.output
