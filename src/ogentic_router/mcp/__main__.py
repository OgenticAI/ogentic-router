"""Allow ``python -m ogentic_router.mcp`` to launch the stdio MCP server.

Reads the router config from ``ROUTER_CONFIG`` (or ``--config``), builds the
Router, and runs the MCP server over stdio. Raises ``RouterError`` with the
install hint if the ``[mcp]`` extra is missing.
"""

from __future__ import annotations

import os
import sys

from ..router import Router
from .server import build_server


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    config_path = os.environ.get("ROUTER_CONFIG")
    if "--config" in argv:
        config_path = argv[argv.index("--config") + 1]
    if not config_path:
        raise SystemExit(
            "no router config — set ROUTER_CONFIG or pass --config <path>."
        )
    router = Router.from_yaml(config_path)
    server = build_server(router)
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
