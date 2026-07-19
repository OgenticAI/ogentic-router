"""MCP tool surface for the router — classify-and-explain, no LLM call (OGE-586).

Exposes the router as an MCP server with four tools:

* ``router.classify_route`` — given a prompt, which backend would handle it and why.
* ``router.policies`` — the loaded policy's structure.
* ``router.adapters`` — the registered backends and their locality.
* ``router.evaluate_dry`` — like ``classify_route``, plus the *would-be-sent*
  (post-redaction) prompt, opt-in. The adapter is never called.

Mirrors ``ogentic-shield``'s MCP pattern: :func:`build_server` lazy-imports
``FastMCP`` inside the function, so this module imports fine without the
``[mcp]`` extra and only fails when you actually build the server. Same
shape-only discipline — tool outputs carry a ``prompt_hash``, never the raw
prompt, unless a caller explicitly opts into ``include_outgoing_prompt``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ..errors import RouterError

if TYPE_CHECKING:  # pragma: no cover - hints only
    from ..router import Router

logger = logging.getLogger("ogentic_router.mcp")


def _text_hash(prompt: str) -> str:
    """Return ``sha256:<16 hex>`` via Shield's ``text_hash_for`` (cross-system
    fingerprint convention). Falls back to the identical local format if Shield
    isn't importable, so introspection still works without the ``[shield]`` extra."""
    try:
        from ogentic_shield.pipeline import text_hash_for  # noqa: PLC0415

        return str(text_hash_for(prompt))
    except ImportError:  # pragma: no cover - shield is normally present
        import hashlib

        return f"sha256:{hashlib.sha256(prompt.encode()).hexdigest()[:16]}"


def _decision_dict(router: Router, prompt: str) -> dict[str, Any]:
    """Route ``prompt`` and return the shape-only decision dict (no raw text)."""
    decision = router.route(prompt)
    return {
        "backend_id": decision.backend_id,
        "rule_id": decision.rule_id,
        "transform": decision.transform.value if decision.transform else None,
        "reasoning": decision.reasoning,
        "prompt_hash": _text_hash(prompt),
    }


def build_server(router: Router, *, name: str = "ogentic-router") -> Any:
    """Construct (but don't run) the FastMCP server for ``router``.

    Lazy-imports ``FastMCP`` so this module is importable without the ``[mcp]``
    extra; raises :class:`RouterError` with the install hint only when called.

    Args:
        router: A constructed :class:`~ogentic_router.Router` — its loaded
            policy, Shield, and declared backends back the four tools.
        name: MCP server name advertised to clients.

    Returns:
        A ``FastMCP`` instance with the four ``router.*`` tools registered.
    """
    try:
        from mcp.server.fastmcp import FastMCP  # noqa: PLC0415
    except ImportError as exc:
        raise RouterError(
            "the router MCP server requires the `mcp` package. Install with "
            "`pip install 'ogentic-router[mcp]'`."
        ) from exc

    server = FastMCP(name=name)

    @server.tool(name="router.classify_route")
    async def classify_route(prompt: str) -> dict[str, Any]:
        """Given a prompt, report which backend would handle it and why —
        without firing the LLM call.

        Returns ``backend_id``, ``rule_id``, ``transform``, ``reasoning``, and a
        shape-only ``prompt_hash``. Never returns the raw prompt.
        """
        if not prompt:
            raise ValueError("`prompt` must be a non-empty string")
        return _decision_dict(router, prompt)

    @server.tool(name="router.policies")
    async def policies() -> dict[str, Any]:
        """Return the loaded policy: its rules and ``default_backend``."""
        spec = router.policy.to_dict()
        return {"policies": spec.get("rules", []), "default_backend": router.policy.default_backend}

    @server.tool(name="router.adapters")
    async def adapters() -> dict[str, Any]:
        """Return the registered backends: ``backend_id``, ``is_local``, ``default_model``."""
        return {"adapters": [dict(b) for b in router.backends]}

    @server.tool(name="router.evaluate_dry")
    async def evaluate_dry(prompt: str, include_outgoing_prompt: bool = False) -> dict[str, Any]:
        """Like ``classify_route``, plus the would-be-sent prompt on opt-in.

        The adapter is **never** called. With ``include_outgoing_prompt=True``
        the response includes ``outgoing_prompt`` — the post-transform text that
        *would* leave the device (e.g. after ``shield_redact``). Off by default;
        using it logs a WARNING because it returns cleared prompt content.
        """
        if not prompt:
            raise ValueError("`prompt` must be a non-empty string")
        out = _decision_dict(router, prompt)
        if include_outgoing_prompt:
            logger.warning(
                "router.evaluate_dry called with include_outgoing_prompt=True — "
                "returning cleared prompt content to the caller."
            )
            out["outgoing_prompt"] = _outgoing_prompt(router, prompt, out["transform"])
        return out

    return server


def _outgoing_prompt(router: Router, prompt: str, transform: str | None) -> str:
    """The text that would actually be dispatched, after any transform.

    For ``shield_redact`` this is ``Shield.redact(prompt)[0]``; with no transform
    the prompt is sent unchanged.
    """
    if transform != "shield_redact":
        return prompt
    shield = router._ensure_shield()  # internal accessor, same package
    redact = getattr(shield, "redact", None)
    if not callable(redact):  # pragma: no cover - real Shield always has redact
        raise RouterError("the configured Shield does not support redaction.")
    redacted, _mapping = redact(prompt)
    return str(redacted)


__all__ = ["build_server"]
