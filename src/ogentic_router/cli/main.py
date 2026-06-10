"""Click CLI entry point — ogentic-router command group (OGE-583).

Subcommands:

- ``serve``   — start the OpenAI-shaped FastAPI server (OGE-583)

Future subcommands (stubs / TODO markers):

- ``--mcp``   — MCP tool surface flag on ``serve`` (TODO: OGE-586 builder slots this in)
- ``route``   — CLI routing (OGE-585)
- ``policies`` — policy management (OGE-585)
"""

from __future__ import annotations

import sys

import click

from ogentic_router import __version__


@click.group()
@click.version_option(version=__version__, prog_name="ogentic-router")
def cli() -> None:
    """ogentic-router: Privacy-aware LLM routing.

    Routes prompts through a sensitivity-aware policy pipeline — sensitive
    content stays local, redacted content may go to cloud.
    """


@cli.command("serve")
@click.option(
    "--config",
    "config_path",
    envvar="ROUTER_CONFIG",
    default=None,
    metavar="PATH",
    help="Path to router.yaml config file. Defaults to $ROUTER_CONFIG.",
)
@click.option(
    "--host",
    default="127.0.0.1",
    show_default=True,
    help=(
        "Host to bind the server to. "
        "WARNING: 0.0.0.0 binds on all interfaces — only use behind a trusted reverse proxy."
    ),
)
@click.option(
    "--port",
    default=8080,
    show_default=True,
    type=int,
    help="Port to listen on.",
)
@click.option(
    "--reload",
    is_flag=True,
    default=False,
    help="Enable auto-reload on code changes (development only).",
)
# TODO(OGE-586): --mcp flag — MCP tool surface for router.classify_route etc.
# When OGE-586 lands, add:
#   @click.option("--mcp", is_flag=True, default=False,
#                 help="Enable the MCP tool surface alongside the HTTP server.")
# and wire it through to the MCP server startup logic.
def serve(
    config_path: str | None,
    host: str,
    port: int,
    reload: bool,
) -> None:
    """Start the OpenAI-shaped router HTTP server.

    The server exposes:

    \b
      GET  /healthz                 — liveness probe
      GET  /v1/models               — list configured backends/models
      POST /v1/chat/completions     — OpenAI-compatible chat completions
      GET  /v1/policy               — inspect the loaded routing policy
      GET  /v1/decision/{id}        — decision lookup (v0.2, pending ogentic-audit)

    Example:

    \b
      ogentic-router serve --config router.yaml --port 8080
    """
    # Warn loudly when binding to all interfaces.
    if host == "0.0.0.0":
        click.echo(
            "WARNING: Binding to 0.0.0.0 exposes the server on all network interfaces. "
            "Ensure you are behind a trusted reverse proxy in production.",
            err=True,
        )

    # Propagate config path via environment so the FastAPI lifespan picks it up.
    import os  # noqa: PLC0415

    if config_path:
        os.environ["ROUTER_CONFIG"] = config_path

    # Import uvicorn lazily — it lives in the [server] extra.
    try:
        import uvicorn  # noqa: PLC0415
    except ImportError:
        click.echo(
            "ERROR: 'uvicorn' is not installed. "
            "Install the [server] extra with: pip install 'ogentic-router[server]'",
            err=True,
        )
        sys.exit(1)

    # Import create_app lazily — fastapi also lives in [server].
    try:
        from ogentic_router.server import create_app  # noqa: PLC0415
    except ImportError:
        click.echo(
            "ERROR: 'fastapi' is not installed. "
            "Install the [server] extra with: pip install 'ogentic-router[server]'",
            err=True,
        )
        sys.exit(1)

    click.echo(f"Starting ogentic-router server on {host}:{port}")
    if config_path:
        click.echo(f"Config: {config_path}")

    app = create_app()
    uvicorn.run(app, host=host, port=port, reload=reload)


if __name__ == "__main__":
    cli()
