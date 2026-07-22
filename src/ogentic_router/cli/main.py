"""Click CLI entry point — the ``ogentic-router`` command group (OGE-585).

Subcommands:

- ``serve``    — boot the OpenAI-shaped HTTP server, or the MCP surface with ``--mcp``
- ``route``    — route one prompt through the full pipeline; print the decision
                 (and, with ``--execute``, the model output)
- ``policies`` — ``validate`` / ``show`` / ``dry-run`` a policy file

Config resolution: a subcommand that needs a router takes ``--config`` (a full
``router.yaml``) or ``--policy`` (a bare policy file). When neither is given, the
config path falls back to the ``ROUTER_CONFIG`` environment variable (or
``OGENTIC_ROUTER_CONFIG``).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import TYPE_CHECKING, Any

import click

from ogentic_router import __version__

if TYPE_CHECKING:  # pragma: no cover - hints only
    from ogentic_router import RouteDecision, Router

# Env vars consulted (in order) for a default --config path.
_CONFIG_ENV_VARS = ("ROUTER_CONFIG", "OGENTIC_ROUTER_CONFIG")


def _config_from_env() -> str | None:
    for var in _CONFIG_ENV_VARS:
        val = os.environ.get(var)
        if val:
            return val
    return None


def _load_router(config_path: str | None, policy_path: str | None) -> Router:
    """Build a Router from a full config, a bare policy, or the env default.

    ``--config`` (or ``$ROUTER_CONFIG``) loads the full ``router.yaml`` — policy,
    Shield, backends, audit. ``--policy`` loads just the routing rules (no
    backends; enough for a decision, not a dispatch). Exits non-zero with a clean
    message on any load error rather than dumping a traceback.
    """
    from ogentic_router import Policy, Router  # noqa: PLC0415
    from ogentic_router.errors import RouterError  # noqa: PLC0415

    resolved_config = config_path or (None if policy_path else _config_from_env())
    try:
        if resolved_config:
            return Router.from_yaml(resolved_config)
        if policy_path:
            return Router(Policy.from_yaml(policy_path))
    except (RouterError, OSError) as exc:
        click.echo(f"ERROR: {exc}", err=True)
        sys.exit(2)
    click.echo(
        "ERROR: no policy or config. Pass --config <router.yaml> or "
        "--policy <policy.yaml>, or set $ROUTER_CONFIG.",
        err=True,
    )
    sys.exit(2)


def _decision_dict(decision: RouteDecision) -> dict[str, Any]:
    return {
        "backend_id": decision.backend_id,
        "rule_id": decision.rule_id,
        "transform": decision.transform.value if decision.transform else None,
        "reasoning": decision.reasoning,
    }


def _read_prompt(prompt: str | None) -> str:
    """Return ``prompt`` if given, else read stdin. Exit 2 if both are empty."""
    if prompt is not None:
        text = prompt
    elif not sys.stdin.isatty():
        text = sys.stdin.read()
    else:
        text = ""
    text = text.strip()
    if not text:
        click.echo(
            "ERROR: no prompt. Pass --prompt \"...\" or pipe text on stdin.", err=True
        )
        sys.exit(2)
    return text


@click.group()
@click.version_option(version=__version__, prog_name="ogentic-router")
def cli() -> None:
    """ogentic-router: privacy-aware LLM routing.

    Routes prompts through a sensitivity-aware policy — sensitive content stays
    local, redacted content may go to cloud.
    """


# ─── serve ───────────────────────────────────────────────────────────────────


@cli.command("serve")
@click.option(
    "--config",
    "config_path",
    envvar="ROUTER_CONFIG",
    default=None,
    metavar="PATH",
    help="Path to router.yaml. Defaults to $ROUTER_CONFIG.",
)
@click.option("--host", default="127.0.0.1", show_default=True, help="Host to bind.")
@click.option("--port", default=8080, show_default=True, type=int, help="Port to listen on.")
@click.option("--reload", is_flag=True, default=False, help="Auto-reload (dev only).")
@click.option(
    "--mcp",
    is_flag=True,
    default=False,
    help="Boot the stdio MCP tool surface instead of the HTTP server (OGE-586).",
)
def serve(config_path: str | None, host: str, port: int, reload: bool, mcp: bool) -> None:
    """Start the router server — HTTP by default, or the MCP surface with --mcp.

    \b
    Examples:
      ogentic-router serve --config router.yaml --port 8080
      ogentic-router serve --mcp --config router.yaml
    """
    if mcp:
        _serve_mcp(config_path)
        return

    if host == "0.0.0.0":  # noqa: S104 - warning, not a bind default
        click.echo(
            "WARNING: binding 0.0.0.0 exposes the server on all interfaces. "
            "Stay behind a trusted reverse proxy in production.",
            err=True,
        )

    if config_path:
        os.environ["ROUTER_CONFIG"] = config_path

    try:
        import uvicorn  # noqa: PLC0415

        from ogentic_router.server import create_app  # noqa: PLC0415
    except ImportError:
        click.echo(
            "ERROR: the HTTP server needs the [server] extra. "
            "Install with: pip install 'ogentic-router[server]'",
            err=True,
        )
        sys.exit(1)

    click.echo(f"Starting ogentic-router server on {host}:{port}")
    if config_path:
        click.echo(f"Config: {config_path}")
    uvicorn.run(create_app(), host=host, port=port, reload=reload)


def _serve_mcp(config_path: str | None) -> None:
    """Build the Router from config and run the stdio MCP server."""
    resolved = config_path or _config_from_env()
    if not resolved:
        click.echo(
            "ERROR: --mcp needs a config. Pass --config <router.yaml> or set "
            "$ROUTER_CONFIG.",
            err=True,
        )
        sys.exit(1)
    from ogentic_router.router import Router  # noqa: PLC0415

    try:
        from ogentic_router.mcp import build_server  # noqa: PLC0415
    except ImportError:  # pragma: no cover - build_server raises the hint itself
        click.echo(
            "ERROR: the MCP surface needs the [mcp] extra. "
            "Install with: pip install 'ogentic-router[mcp]'",
            err=True,
        )
        sys.exit(1)

    try:
        server = build_server(Router.from_yaml(resolved))
    except Exception as exc:  # noqa: BLE001 - surface a clean CLI error
        click.echo(f"ERROR: {exc}", err=True)
        sys.exit(1)

    click.echo(f"Starting ogentic-router MCP server (stdio). Config: {resolved}", err=True)
    server.run(transport="stdio")


# ─── route ───────────────────────────────────────────────────────────────────


@cli.command("route")
@click.option("--config", "config_path", default=None, metavar="PATH",
              help="Path to router.yaml (enables --execute). Defaults to $ROUTER_CONFIG.")
@click.option("--policy", "policy_path", default=None, metavar="PATH",
              help="Path to a bare policy.yaml (decision only; no backends).")
@click.option("--prompt", "prompt", default=None, metavar="TEXT",
              help="Prompt to route. If omitted, read from stdin.")
@click.option("--model", default=None, metavar="MODEL",
              help="Model id for cost estimation / dispatch (e.g. 'gpt-4o-mini').")
@click.option("--budget-ceiling", "budget_ceiling", default=None, type=float, metavar="USD",
              help="Override the policy budget for this call. Omit to use the policy's "
                   "budget block (ON by default).")
@click.option("--execute", is_flag=True, default=False,
              help="Also dispatch to the chosen backend and print the model output "
                   "(needs a --config with backends).")
@click.option("--output", "output", type=click.Choice(["json", "text"]), default="json",
              show_default=True, help="Output format.")
def route(
    config_path: str | None,
    policy_path: str | None,
    prompt: str | None,
    model: str | None,
    budget_ceiling: float | None,
    execute: bool,
    output: str,
) -> None:
    """Route a single prompt through the full pipeline and print the decision.

    Runs Shield classification + the policy, printing the RouteDecision. With
    --execute (and a --config that declares backends), it also dispatches to the
    chosen backend and includes the model output. Budget enforcement is ON by
    default (OGE-1120) — a runaway prompt is refused before any network call.

    \b
    Examples:
      ogentic-router route --config router.yaml --prompt "attorney work product"
      echo "what's the weather" | ogentic-router route --policy policy.yaml
      ogentic-router route --config router.yaml --prompt "hi" --execute
    """
    from ogentic_router import BudgetCeilingExceeded, ShieldUnavailableError  # noqa: PLC0415

    router = _load_router(config_path, policy_path)
    text = _read_prompt(prompt)

    route_kwargs: dict[str, Any] = {"model": model}
    if budget_ceiling is not None:
        route_kwargs["budget_ceiling"] = budget_ceiling

    try:
        decision = router.route(text, **route_kwargs)
    except BudgetCeilingExceeded as exc:
        click.echo(f"BudgetCeilingExceeded: {exc}", err=True)
        sys.exit(1)
    except ShieldUnavailableError as exc:
        click.echo(f"ERROR: {exc}", err=True)
        sys.exit(2)

    result: dict[str, Any] = _decision_dict(decision)

    if execute:
        result["output"] = _dispatch(config_path, decision, text, model)

    if output == "json":
        click.echo(json.dumps(result, indent=2))
    else:
        click.echo(f"backend:   {result['backend_id']}")
        click.echo(f"rule:      {result['rule_id'] or '(default_backend)'}")
        click.echo(f"transform: {result['transform'] or '(none)'}")
        click.echo(f"reason:    {result['reasoning']}")
        if "output" in result:
            click.echo(f"output:    {result['output']}")


def _dispatch(
    config_path: str | None, decision: RouteDecision, prompt: str, model: str | None
) -> str:
    """Build the chosen backend's adapter and return its completion text."""
    resolved = config_path or _config_from_env()
    if not resolved:
        click.echo(
            "ERROR: --execute needs a --config with backends (or $ROUTER_CONFIG).",
            err=True,
        )
        sys.exit(2)
    from ogentic_router.adapters.factory import build_adapter  # noqa: PLC0415
    from ogentic_router.server.config import load_router_config, resolve_api_key  # noqa: PLC0415

    cfg = load_router_config(resolved)
    backend = next((b for b in cfg.backends if b.id == decision.backend_id), None)
    if backend is None:
        click.echo(
            f"ERROR: policy chose backend {decision.backend_id!r}, which the config "
            "does not declare.",
            err=True,
        )
        sys.exit(2)

    adapter = build_adapter(
        kind=backend.kind,
        backend_id=backend.id,
        base_url=backend.base_url,
        default_model=backend.default_model,
        api_key=resolve_api_key(backend),
    )

    async def _run() -> str:
        resp: Any = await adapter.chat([{"role": "user", "content": prompt}], model=model)
        # Adapters pass provider-native responses through; pull the text out
        # of the common OpenAI/Anthropic shapes, else stringify.
        try:
            return str(resp["choices"][0]["message"]["content"])
        except (KeyError, TypeError, IndexError):
            try:
                return str(resp.choices[0].message.content)
            except Exception:  # noqa: BLE001
                return str(resp)

    try:
        return asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001 - clean CLI error on dispatch failure
        click.echo(f"ERROR: dispatch to {decision.backend_id} failed: {exc}", err=True)
        sys.exit(1)


# ─── policies ────────────────────────────────────────────────────────────────


@cli.group("policies")
def policies() -> None:
    """Inspect and validate policy files."""


@policies.command("validate")
@click.argument("path", metavar="POLICY")
def policies_validate(path: str) -> None:
    """Load POLICY and report whether it is valid. Exit 0 on success, 2 on error."""
    from ogentic_router import Policy, PolicyError  # noqa: PLC0415

    try:
        Policy.from_yaml(path)
    except PolicyError as exc:
        click.echo(f"INVALID: {exc}", err=True)
        sys.exit(2)
    click.echo(f"OK: {path} is a valid policy.")


@policies.command("show")
@click.argument("path", metavar="POLICY")
def policies_show(path: str) -> None:
    """Pretty-print POLICY's rules as a table."""
    from rich.console import Console  # noqa: PLC0415
    from rich.table import Table  # noqa: PLC0415

    from ogentic_router import Policy, PolicyError  # noqa: PLC0415

    try:
        policy = Policy.from_yaml(path)
    except PolicyError as exc:
        click.echo(f"INVALID: {exc}", err=True)
        sys.exit(2)

    spec = policy.to_dict()
    console = Console()
    console.print(f"[bold]default_backend:[/bold] {policy.default_backend}")
    table = Table(show_header=True, header_style="bold")
    table.add_column("#", justify="right")
    table.add_column("id")
    table.add_column("when")
    table.add_column("route")
    table.add_column("transform")
    for i, rule in enumerate(spec.get("rules", []), start=1):
        when = ", ".join(f"{k}={v}" for k, v in (rule.get("when") or {}).items()) or "(any)"
        table.add_row(str(i), rule.get("id", ""), when, rule.get("route", ""),
                      rule.get("transform") or "-")
    console.print(table)


@policies.command("dry-run")
@click.argument("path", metavar="POLICY")
@click.option("--prompt", "prompt", default=None, metavar="TEXT",
              help="Prompt to evaluate. If omitted, read from stdin.")
@click.option("--output", "output", type=click.Choice(["json", "text"]), default="json",
              show_default=True, help="Output format.")
def policies_dry_run(path: str, prompt: str | None, output: str) -> None:
    """Show what POLICY would decide for a prompt — Shield + policy, no dispatch.

    Never calls a backend adapter; this is the safe "what would happen" inspector.
    Budget enforcement is disabled here so a dry-run never refuses.
    """
    from ogentic_router import Policy, Router, ShieldUnavailableError  # noqa: PLC0415

    text = _read_prompt(prompt)
    try:
        router = Router(Policy.from_yaml(path))
        # budget_ceiling=None disables enforcement for the inspection.
        decision = router.route(text, budget_ceiling=None)
    except ShieldUnavailableError as exc:
        click.echo(f"ERROR: {exc}", err=True)
        sys.exit(2)

    result = _decision_dict(decision)
    if output == "json":
        click.echo(json.dumps(result, indent=2))
    else:
        click.echo(f"backend:   {result['backend_id']}")
        click.echo(f"rule:      {result['rule_id'] or '(default_backend)'}")
        click.echo(f"transform: {result['transform'] or '(none)'}")
        click.echo(f"reason:    {result['reasoning']}")


if __name__ == "__main__":
    cli()
