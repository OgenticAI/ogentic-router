"""ogentic-router server — OpenAI-shaped FastAPI application (OGE-583).

Re-exports :func:`create_app` as the top-level entry point so callers can
do::

    from ogentic_router.server import create_app
    app = create_app()

The server module is gated behind the ``[server]`` extra
(``fastapi`` + ``uvicorn``). Importing this module without the extra
raises :class:`~ogentic_router.errors.ServerImportError` with the canonical
install hint.
"""

from __future__ import annotations


def __getattr__(name: str) -> object:
    if name == "create_app":
        try:
            from ogentic_router.server.app import create_app  # noqa: PLC0415

            return create_app
        except ImportError as exc:
            from ogentic_router.errors import ServerImportError  # noqa: PLC0415

            raise ServerImportError(
                "ogentic-router server requires the 'fastapi' and 'uvicorn' packages. "
                "Install with: pip install 'ogentic-router[server]'"
            ) from exc
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


try:
    from ogentic_router.server.app import create_app  # noqa: F401

    __all__ = ["create_app"]
except ImportError:
    __all__ = []
