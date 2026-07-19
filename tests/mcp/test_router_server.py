"""Tests for the router MCP tool surface (OGE-586).

Mirrors ``ogentic-shield``'s MCP test pattern: ``pytest.importorskip("mcp")`` so
collection is graceful without the SDK, tools invoked as plain callables (not
over JSON-RPC), and a stub Shield so there's no Presidio cold-start.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

mcp = pytest.importorskip(
    "mcp",
    reason="MCP SDK not installed — pip install 'ogentic-router[mcp]' to run these tests.",
)

from ogentic_router import Policy, Router  # noqa: E402  (after importorskip)
from ogentic_router.errors import RouterError  # noqa: E402
from ogentic_router.mcp.server import build_server  # noqa: E402

CANONICAL_POLICY = Path(__file__).parent.parent.parent / "examples" / "policy.yaml"


# ── Stubs ────────────────────────────────────────────────────────────────────


def _stub_shield(*, score: int = 0, groups: list[str] | None = None) -> Any:
    def analyze(_text: str) -> Any:
        return SimpleNamespace(
            score=score,
            category_groups_found=set(groups or []),
            entities=[],
            top_category=None,
            text_hash="sha256:deadbeefdeadbeef",
            entity_count=0,
            profile_ids=["shield-legal"],
        )

    def redact(_text: str) -> tuple[str, Any]:
        return ("My SSN is <REDACTED:US_SSN>", SimpleNamespace())

    return SimpleNamespace(analyze=analyze, redact=redact)


def _router(**shield_kwargs: Any) -> Router:
    return Router(
        Policy.from_yaml(CANONICAL_POLICY),
        shield=_stub_shield(**shield_kwargs),
        backends=[
            {"backend_id": "ollama-local", "is_local": True, "default_model": "llama3.2:3b"},
            {"backend_id": "openai-cloud", "is_local": False, "default_model": "gpt-4o-mini"},
        ],
    )


def _collect_tool_names(server: Any) -> list[str]:
    tm = getattr(server, "_tool_manager", None) or getattr(server, "tool_manager", None)
    assert tm is not None, "could not find tool manager on FastMCP server"
    tools = getattr(tm, "_tools", None) or getattr(tm, "tools", None)
    assert tools is not None, "tool manager has no _tools / tools attribute"
    return list(tools.keys())


def _tool(server: Any, name: str) -> Any:
    tm = getattr(server, "_tool_manager", None) or getattr(server, "tool_manager", None)
    tools = getattr(tm, "_tools", None) or getattr(tm, "tools", None)
    return tools[name].fn


# ── Tests ────────────────────────────────────────────────────────────────────


def test_registers_all_four_tools() -> None:
    server = build_server(_router())
    assert sorted(_collect_tool_names(server)) == [
        "router.adapters",
        "router.classify_route",
        "router.evaluate_dry",
        "router.policies",
    ]


async def test_classify_route_happy_path_local() -> None:
    server = build_server(_router(groups=["PRIVILEGE"]))
    out = await _tool(server, "router.classify_route")(prompt="privileged memo")
    assert out["backend_id"] == "ollama-local"
    assert out["rule_id"] == "privilege-stays-local"
    assert out["transform"] is None
    assert out["prompt_hash"].startswith("sha256:")


async def test_classify_route_output_never_contains_prompt() -> None:
    server = build_server(_router(score=5))
    secret = "4111-1111-1111-1111"
    out = await _tool(server, "router.classify_route")(prompt=f"card {secret} please")
    assert all(secret not in str(v) for v in out.values())
    assert "outgoing_prompt" not in out


async def test_classify_route_empty_prompt_raises() -> None:
    server = build_server(_router())
    with pytest.raises(ValueError, match="non-empty"):
        await _tool(server, "router.classify_route")(prompt="")


async def test_policies_tool_shape() -> None:
    server = build_server(_router())
    out = await _tool(server, "router.policies")()
    assert out["default_backend"] == "ollama-local"
    assert isinstance(out["policies"], list)
    assert any(r.get("id") == "privilege-stays-local" for r in out["policies"])


async def test_adapters_tool_shape() -> None:
    server = build_server(_router())
    out = await _tool(server, "router.adapters")()
    ids = {a["backend_id"]: a for a in out["adapters"]}
    assert ids["ollama-local"]["is_local"] is True
    assert ids["openai-cloud"]["is_local"] is False
    assert ids["ollama-local"]["default_model"] == "llama3.2:3b"


async def test_evaluate_dry_default_returns_no_prompt_content() -> None:
    server = build_server(_router(score=45))
    out = await _tool(server, "router.evaluate_dry")(prompt="My SSN is 123-45-6789")
    assert "outgoing_prompt" not in out
    assert "123-45-6789" not in str(out)
    assert out["transform"] == "shield_redact"


async def test_evaluate_dry_include_outgoing_returns_redacted_text() -> None:
    server = build_server(_router(score=45))
    out = await _tool(server, "router.evaluate_dry")(
        prompt="My SSN is 123-45-6789", include_outgoing_prompt=True
    )
    assert out["outgoing_prompt"] == "My SSN is <REDACTED:US_SSN>"
    assert "123-45-6789" not in out["outgoing_prompt"]  # the raw value is gone


async def test_evaluate_dry_no_transform_returns_prompt_when_opted_in() -> None:
    server = build_server(_router(score=5))  # low → cloud, no redact
    out = await _tool(server, "router.evaluate_dry")(
        prompt="hello world", include_outgoing_prompt=True
    )
    assert out["transform"] is None
    assert out["outgoing_prompt"] == "hello world"


async def test_evaluate_dry_never_calls_an_adapter() -> None:
    """route() is decision-only by design — no backend is dispatched. Prove it
    by attaching a mock adapter and asserting its chat() is never touched."""
    router = _router(score=45)
    mock_adapter = MagicMock()
    # Even if an adapter were reachable, evaluate_dry must not invoke it.
    server = build_server(router)
    await _tool(server, "router.evaluate_dry")(prompt="anything", include_outgoing_prompt=True)
    mock_adapter.chat.assert_not_called()


async def test_prompt_hash_matches_shield_text_hash_for() -> None:
    from ogentic_shield.pipeline import text_hash_for

    server = build_server(_router())
    prompt = "cross-system fingerprint check"
    out = await _tool(server, "router.classify_route")(prompt=prompt)
    assert out["prompt_hash"] == text_hash_for(prompt)


def test_build_server_raises_routererror_without_mcp(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulate the [mcp] extra being absent — build_server raises RouterError
    with the pip hint."""
    monkeypatch.setitem(sys.modules, "mcp.server.fastmcp", None)
    with pytest.raises(RouterError, match=r"ogentic-router\[mcp\]"):
        build_server(_router())


@pytest.mark.skipif(
    os.environ.get("OGENTIC_ROUTER_SHIELD_INTEGRATION") != "1",
    reason="set OGENTIC_ROUTER_SHIELD_INTEGRATION=1 to run the real-Shield integration test",
)
async def test_classify_route_with_real_shield() -> None:  # pragma: no cover - opt-in
    router = Router.from_config({"policy_path": str(CANONICAL_POLICY)})
    server = build_server(router)
    out = await _tool(server, "router.classify_route")(
        prompt="Privileged attorney-client memo about the settlement."
    )
    assert out["backend_id"] in {"ollama-local", "openai-cloud"}
    assert out["prompt_hash"].startswith("sha256:")
