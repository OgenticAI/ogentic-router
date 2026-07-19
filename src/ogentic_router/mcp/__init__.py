"""MCP subpackage — the router's classify-and-explain tool surface (OGE-586).

Importable without the ``[mcp]`` extra; :func:`build_server` raises
:class:`~ogentic_router.errors.RouterError` with the install hint only when
called without the SDK. See :mod:`ogentic_router.mcp.server`.
"""

from __future__ import annotations

from .server import build_server

__all__ = ["build_server"]
