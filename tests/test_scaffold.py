"""Smoke tests for package import and CLI scaffold.

Tightly scoped: prove the package imports, the version is exposed, and the
CLI registers cleanly.
"""

from __future__ import annotations

from click.testing import CliRunner

import ogentic_router
from ogentic_router.cli.main import cli


def test_version_is_exposed() -> None:
    assert isinstance(ogentic_router.__version__, str)
    assert ogentic_router.__version__ == "0.1.0"


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
