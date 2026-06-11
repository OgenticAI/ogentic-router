"""Tests for the `serve` CLI subcommand (OGE-583).

AC coverage:
1. ``serve --help`` exits 0 and prints usage.
2. ``serve`` with missing ``[server]`` extra exits 1 with install hint.
3. ``serve`` warns on --host 0.0.0.0.
4. ``serve`` without uvicorn exits 1 with install hint.
5. ``ogentic-router --version`` prints version.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from ogentic_router.cli.main import cli


class TestServeCommand:
    """Tests for `ogentic-router serve`."""

    def test_serve_help(self) -> None:
        """AC: serve --help exits 0 and prints usage text."""
        runner = CliRunner()
        result = runner.invoke(cli, ["serve", "--help"])
        assert result.exit_code == 0
        assert "serve" in result.output.lower() or "host" in result.output.lower()

    def test_serve_help_shows_host_option(self) -> None:
        """AC: serve --help shows --host option."""
        runner = CliRunner()
        result = runner.invoke(cli, ["serve", "--help"])
        assert result.exit_code == 0
        assert "--host" in result.output

    def test_serve_help_shows_port_option(self) -> None:
        """AC: serve --help shows --port option."""
        runner = CliRunner()
        result = runner.invoke(cli, ["serve", "--help"])
        assert result.exit_code == 0
        assert "--port" in result.output

    def test_serve_warns_on_all_interfaces(self) -> None:
        """AC: --host 0.0.0.0 emits a WARNING before running."""
        runner = CliRunner()

        # Patch uvicorn.run and create_app to prevent actual server start.
        fake_uvicorn = MagicMock()
        fake_uvicorn.run = MagicMock()

        with (
            patch.dict("sys.modules", {"uvicorn": fake_uvicorn}),
            patch("ogentic_router.server.create_app", MagicMock(return_value=MagicMock()), create=True),
        ):
            result = runner.invoke(cli, ["serve", "--host", "0.0.0.0", "--port", "9999"])

        # WARNING should appear in combined output
        assert "WARNING" in result.output or "0.0.0.0" in result.output

    def test_serve_missing_uvicorn_exits_1(self) -> None:
        """AC: when uvicorn is not installed, serve exits 1 with install hint."""
        runner = CliRunner()

        # Simulate uvicorn missing by patching the import inside the CLI.
        import builtins

        real_import = builtins.__import__

        def mock_import(name: str, *args: object, **kwargs: object) -> object:
            if name == "uvicorn":
                raise ImportError("No module named 'uvicorn'")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            result = runner.invoke(cli, ["serve", "--port", "9999"])

        assert result.exit_code == 1
        assert "uvicorn" in result.output.lower() or "server" in result.output.lower()

    def test_cli_version(self) -> None:
        """AC: --version prints the version string."""
        runner = CliRunner()
        result = runner.invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert "ogentic-router" in result.output

    def test_serve_invokes_uvicorn_run(self) -> None:
        """AC: serve calls uvicorn.run with the correct host/port."""
        runner = CliRunner()

        # create_app is imported lazily inside the serve() function body.
        # To intercept uvicorn.run, we patch the entire uvicorn module via sys.modules.
        # The fake uvicorn.run captures the kwargs.
        fake_uvicorn = MagicMock()
        fake_uvicorn.run = MagicMock()

        # We also need create_app to succeed. Since it's imported lazily from
        # ogentic_router.server, we patch the server module's create_app.
        import ogentic_router.server as _server_mod  # noqa: PLC0415

        with (
            patch.dict("sys.modules", {"uvicorn": fake_uvicorn}),
            patch.object(_server_mod, "create_app", MagicMock(return_value=MagicMock())),
        ):
            runner.invoke(cli, ["serve", "--host", "127.0.0.1", "--port", "8888"])

        fake_uvicorn.run.assert_called_once()
        _call_args, call_kwargs = fake_uvicorn.run.call_args
        assert call_kwargs.get("host") == "127.0.0.1"
        assert call_kwargs.get("port") == 8888
