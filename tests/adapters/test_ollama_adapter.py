"""Tests for ``OllamaAdapter`` — mocked via pytest-httpx.

These tests cover the AC matrix from the spec:
  * defaults / ``is_local`` / ``backend_id``
  * loopback enforcement (delegates to ``_validate_localhost``)
  * non-streaming returns provider-shaped dict
  * streaming yields delta dicts from SSE
  * 120s default timeout is large enough for cold model load
  * no model lifecycle calls (``/api/pull``, ``/api/tags``, etc.)
  * context manager closes the underlying httpx client
  * missing ``[local]`` extra raises ``AdapterImportError`` with a hint
  * opt-in integration test against a live Ollama
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest
from pytest_httpx import HTTPXMock

from ogentic_router.adapters import (
    AdapterImportError,
    LocalhostOnlyError,
    OllamaAdapter,
)

_OLLAMA_CHAT_URL = "http://localhost:11434/v1/chat/completions"
_FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> dict[str, Any]:
    with (_FIXTURE_DIR / name).open() as fh:
        data: dict[str, Any] = json.load(fh)
        return data


# ---------------------------------------------------------------------------
# Defaults / contract
# ---------------------------------------------------------------------------


def test_defaults_match_spec() -> None:
    """Canonical defaults from the spec — used by policy.yaml templates."""
    a = OllamaAdapter()
    assert a.is_local is True
    assert a.backend_id == "ollama-local"
    assert a.endpoint == "http://localhost:11434"
    assert a._default_model == "llama3.2:3b"  # type: ignore[attr-defined]


def test_backend_id_overridable() -> None:
    a = OllamaAdapter(backend_id="ollama-secondary")
    assert a.backend_id == "ollama-secondary"


def test_endpoint_trailing_slash_stripped() -> None:
    """Trailing slash on endpoint normalized so URL composition stays clean."""
    a = OllamaAdapter(endpoint="http://localhost:11434/")
    assert a.endpoint == "http://localhost:11434"


# ---------------------------------------------------------------------------
# Loopback enforcement
# ---------------------------------------------------------------------------


def test_non_loopback_endpoint_raises() -> None:
    with pytest.raises(LocalhostOnlyError):
        OllamaAdapter(endpoint="http://my-ollama.internal:11434")


def test_https_external_raises() -> None:
    """A common typo — pointing at an OpenAI URL — must fail LOUDLY."""
    with pytest.raises(LocalhostOnlyError):
        OllamaAdapter(endpoint="https://api.openai.com")


def test_ipv6_loopback_accepted() -> None:
    """IPv6 loopback (bracketed form) is in the allow-list."""
    a = OllamaAdapter(endpoint="http://[::1]:11434")
    assert a.endpoint == "http://[::1]:11434"


# ---------------------------------------------------------------------------
# Non-streaming chat
# ---------------------------------------------------------------------------


async def test_non_streaming_returns_provider_dict(httpx_mock: HTTPXMock) -> None:
    """Response is the OpenAI-shaped dict, pass-through (no normalization)."""
    httpx_mock.add_response(
        url=_OLLAMA_CHAT_URL,
        method="POST",
        json=_load_fixture("ollama_response.json"),
    )

    async with OllamaAdapter() as adapter:
        response = await adapter.chat(
            messages=[{"role": "user", "content": "Hi."}],
            max_tokens=50,
        )

    assert response["choices"][0]["message"]["content"] == "Hello back from Ollama."
    assert response["model"] == "llama3.2:3b"


async def test_request_payload_uses_default_model(httpx_mock: HTTPXMock) -> None:
    """When ``model=`` omitted, the constructor default goes on the wire."""
    httpx_mock.add_response(
        url=_OLLAMA_CHAT_URL,
        method="POST",
        json=_load_fixture("ollama_response.json"),
    )

    async with OllamaAdapter() as adapter:
        await adapter.chat(messages=[{"role": "user", "content": "Hi."}])

    request = httpx_mock.get_request()
    assert request is not None
    body = json.loads(request.read())
    assert body["model"] == "llama3.2:3b"
    assert body["stream"] is False


async def test_chat_uses_override_model(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=_OLLAMA_CHAT_URL,
        method="POST",
        json=_load_fixture("ollama_response.json"),
    )

    async with OllamaAdapter() as adapter:
        await adapter.chat(
            messages=[{"role": "user", "content": "Hi."}],
            model="qwen2.5:3b",
            temperature=0.2,
        )

    request = httpx_mock.get_request()
    assert request is not None
    body = json.loads(request.read())
    assert body["model"] == "qwen2.5:3b"
    assert body["temperature"] == 0.2


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------


async def test_stream_yields_deltas(httpx_mock: HTTPXMock) -> None:
    """``stream=True`` yields parsed delta dicts from the SSE stream."""
    sse_body = (
        b'data: {"choices":[{"index":0,"delta":{"role":"assistant","content":"Hel"}}]}\n\n'
        b'data: {"choices":[{"index":0,"delta":{"content":"lo"}}]}\n\n'
        b"data: [DONE]\n\n"
    )
    httpx_mock.add_response(
        url=_OLLAMA_CHAT_URL,
        method="POST",
        content=sse_body,
        headers={"content-type": "text/event-stream"},
    )

    async with OllamaAdapter() as adapter:
        stream = await adapter.chat(
            messages=[{"role": "user", "content": "Hi."}],
            stream=True,
        )
        deltas = [d async for d in stream]  # type: ignore[union-attr]

    assert len(deltas) == 2
    content = "".join(d["choices"][0]["delta"].get("content", "") for d in deltas)
    assert content == "Hello"


# ---------------------------------------------------------------------------
# Timeout
# ---------------------------------------------------------------------------


def test_default_timeout_is_120_seconds() -> None:
    """Cold model load can exceed 30s; default must be 120s per spec."""
    a = OllamaAdapter()
    # Accessing the private attr is the cheapest way to assert this without
    # waiting 120s in a test. The value is part of the contract.
    assert a._default_timeout_s == 120.0  # type: ignore[attr-defined]


def test_custom_timeout_respected() -> None:
    a = OllamaAdapter(timeout_s=300.0)
    assert a._default_timeout_s == 300.0  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# No model lifecycle calls
# ---------------------------------------------------------------------------


async def test_no_model_lifecycle_calls(httpx_mock: HTTPXMock) -> None:
    """Adapter must NOT call ``/api/pull``, ``/api/tags``, ``/api/show`` etc.

    Sotto Desktop owns model lifecycle. If we accidentally start pinging
    ``/api/tags`` before every chat, we leak the user's request cadence
    via extra HTTP calls and risk auto-pulling models without consent.
    """
    httpx_mock.add_response(
        url=_OLLAMA_CHAT_URL,
        method="POST",
        json=_load_fixture("ollama_response.json"),
    )

    async with OllamaAdapter() as adapter:
        await adapter.chat(messages=[{"role": "user", "content": "Hi."}])

    requests = httpx_mock.get_requests()
    # Exactly one request, and only to the chat endpoint.
    assert len(requests) == 1
    forbidden_paths = ("/api/pull", "/api/tags", "/api/show", "/api/delete", "/api/copy")
    for req in requests:
        assert not any(p in str(req.url) for p in forbidden_paths), (
            f"Forbidden model-lifecycle endpoint hit: {req.url}"
        )


def test_source_contains_no_lifecycle_endpoints() -> None:
    """Belt-and-suspenders: parse the adapter source and ensure none of the
    forbidden model-lifecycle endpoints appear in *executable* string
    literals. Docstrings naturally mention ``/api/pull`` to explain what
    the adapter does NOT do, so we identify and skip docstring nodes by
    object identity before scanning."""
    import ast

    src_path = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "ogentic_router"
        / "adapters"
        / "ollama_adapter.py"
    )
    tree = ast.parse(src_path.read_text())

    # Collect the AST nodes that *are* docstrings so we can exclude them
    # from the scan by object identity.
    docstring_nodes: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstring_nodes.add(id(body[0].value))

    forbidden = ("/api/pull", "/api/tags", "/api/show", "/api/delete", "/api/copy")
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        if id(node) in docstring_nodes:
            continue
        for bad in forbidden:
            assert bad not in node.value, (
                f"OllamaAdapter must not reference {bad} in executable code "
                f"(found in literal: {node.value!r})"
            )


# ---------------------------------------------------------------------------
# Context manager / cleanup
# ---------------------------------------------------------------------------


async def test_context_manager_closes_client() -> None:
    """``async with`` calls ``aclose`` on the underlying ``httpx.AsyncClient``."""
    adapter = OllamaAdapter()
    assert adapter._client.is_closed is False  # type: ignore[attr-defined]
    async with adapter:
        pass
    assert adapter._client.is_closed is True  # type: ignore[attr-defined]


async def test_explicit_aclose_works() -> None:
    """Callers that don't use ``async with`` can still close cleanly."""
    adapter = OllamaAdapter()
    await adapter.aclose()
    assert adapter._client.is_closed is True  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Missing extra
# ---------------------------------------------------------------------------


def test_missing_extra_raises_with_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    """If ``httpx`` isn't importable, construction raises ``AdapterImportError``
    with the right ``pip install`` hint."""
    for name in list(sys.modules):
        if name == "httpx" or name.startswith("httpx."):
            monkeypatch.delitem(sys.modules, name, raising=False)

    real_import = (
        __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__  # type: ignore[index]
    )

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "httpx" or name.startswith("httpx."):
            raise ImportError(f"No module named {name!r}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)

    with pytest.raises(AdapterImportError) as exc:
        OllamaAdapter()
    assert "pip install 'ogentic-router[local]'" in str(exc.value)


# ---------------------------------------------------------------------------
# Opt-in integration test
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("OGENTIC_ROUTER_OLLAMA_INTEGRATION") != "1",
    reason="set OGENTIC_ROUTER_OLLAMA_INTEGRATION=1 to run against a live Ollama",
)
async def test_real_ollama_integration() -> None:  # pragma: no cover — opt-in
    """Round-trip against a live Ollama at ``http://localhost:11434``.

    Skipped unless the env var is set so CI doesn't depend on a running
    backend. Useful locally for spotting wire-shape drift.
    """
    async with OllamaAdapter() as adapter:
        response = await asyncio.wait_for(
            adapter.chat(
                messages=[{"role": "user", "content": "Reply with the single word: pong"}],
                max_tokens=10,
            ),
            timeout=120,
        )
    assert isinstance(response, dict)
    assert "choices" in response
