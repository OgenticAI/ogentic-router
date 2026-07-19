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
@click.option(
    "--mcp",
    is_flag=True,
    default=False,
    help="Boot the stdio MCP tool surface instead of the HTTP server (OGE-586).",
)
def serve(
    config_path: str | None,
    host: str,
    port: int,
    reload: bool,
    mcp: bool,
) -> None:
    """Start the router server — HTTP by default, or the MCP tool surface with --mcp.

    The HTTP server exposes:

    \b
      GET  /healthz                 — liveness probe
      GET  /v1/models               — list configured backends/models
      POST /v1/chat/completions     — OpenAI-compatible chat completions
      GET  /v1/policy               — inspect the loaded routing policy
      GET  /v1/decision/{id}        — decision lookup (v0.2, pending ogentic-audit)

    With --mcp, a stdio MCP server is booted instead (for Claude Desktop / Goose /
    Cursor / Sotto), exposing router.classify_route + three introspection tools.
    Same config file (router.yaml); the transport diverges at boot.

    Example:

    \b
      ogentic-router serve --config router.yaml --port 8080
      ogentic-router serve --mcp --config router.yaml
    """
    if mcp:
        _serve_mcp(config_path)
        return

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


def _serve_mcp(config_path: str | None) -> None:
    """Build the Router from config and run the stdio MCP server."""
    if not config_path:
        click.echo(
            "ERROR: --mcp needs a config. Pass --config <router.yaml> or set "
            "$ROUTER_CONFIG.",
            err=True,
        )
        sys.exit(1)
    from ogentic_router.router import Router  # noqa: PLC0415

    try:
        from ogentic_router.mcp import build_server  # noqa: PLC0415
    except ImportError:  # pragma: no cover - build_server itself raises the hint
        click.echo(
            "ERROR: the MCP tool surface needs the [mcp] extra. "
            "Install with: pip install 'ogentic-router[mcp]'",
            err=True,
        )
        sys.exit(1)

    try:
        router = Router.from_yaml(config_path)
        server = build_server(router)
    except Exception as exc:  # noqa: BLE001 - surface a clean CLI error
        click.echo(f"ERROR: {exc}", err=True)
        sys.exit(1)

    click.echo(f"Starting ogentic-router MCP server (stdio). Config: {config_path}", err=True)
    server.run(transport="stdio")


@cli.command("route")
@click.option(
    "--model",
    required=True,
    metavar="MODEL",
    help="Model identifier to route to (e.g. 'gpt-4-turbo', 'opus-4').",
)
@click.option(
    "--prompt",
    "prompt_text",
    required=True,
    metavar="TEXT",
    help="Prompt text to route.",
)
@click.option(
    "--budget-ceiling",
    "budget_ceiling",
    default=None,
    type=float,
    metavar="USD",
    help=(
        "Maximum estimated USD cost for this call. "
        "Exits non-zero with an error if the estimate exceeds the ceiling. "
        "0.0 refuses all calls (dry-run mode). Omit to disable enforcement."
    ),
)
def route(
    model: str,
    prompt_text: str,
    budget_ceiling: float | None,
) -> None:
    """Route a prompt through the cost-aware budget enforcer.

    Checks the estimated cost of sending PROMPT to MODEL before any network
    call is made. If --budget-ceiling is set and the estimate exceeds it,
    the command exits non-zero with a BudgetCeilingExceeded error on stderr.

    Example:

    \b
      ogentic-router route --model opus-4 --prompt 'hello' --budget-ceiling 0.001
    """
    from ogentic_router.cost import estimate_cost  # noqa: PLC0415
    from ogentic_router.errors import BudgetCeilingExceeded  # noqa: PLC0415

    if budget_ceiling is not None:
        cost = estimate_cost(model, prompt_text)
        if cost > budget_ceiling:
            exc = BudgetCeilingExceeded(
                estimated_cost=cost,
                ceiling=budget_ceiling,
                model=model,
            )
            click.echo(str(exc), err=True)
            sys.exit(1)

    click.echo(f"routed: model={model} budget_ceiling={budget_ceiling}")


if __name__ == "__main__":
    cli()
