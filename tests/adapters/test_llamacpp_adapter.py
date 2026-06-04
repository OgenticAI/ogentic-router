"""Tests for ``LlamaCppAdapter`` — mocked via pytest-httpx.

Covers the AC matrix specific to llama.cpp:
  * defaults: port 8080, ``default_model=None``, ``backend_id="llamacpp-local"``
  * ``is_local = True``
  * loopback enforcement (delegates to ``_validate_localhost``)
  * non-streaming returns provider-shaped dict
  * **Defensive parsing** — response missing ``usage`` block, missing ``id``,
    unusual ``finish_reason`` values must NOT crash the adapter
  * streaming yields delta dicts from SSE
  * context manager closes the underlying httpx client
  * model is only included in request payload when caller provides one
    (because ``./server`` pins the model at launch)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pytest_httpx import HTTPXMock

from ogentic_router.adapters import LlamaCppAdapter, LocalhostOnlyError

_LLAMACPP_CHAT_URL = "http://localhost:8080/v1/chat/completions"
_FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> dict[str, Any]:
    with (_FIXTURE_DIR / name).open() as fh:
        data: dict[str, Any] = json.load(fh)
        return data


# ---------------------------------------------------------------------------
# Defaults / contract
# ---------------------------------------------------------------------------


def test_defaults_match_spec() -> None:
    """Canonical defaults from the spec."""
    a = LlamaCppAdapter()
    assert a.is_local is True
    assert a.backend_id == "llamacpp-local"
    assert a.endpoint == "http://localhost:8080"
    assert a._default_model is None  # type: ignore[attr-defined]


def test_backend_id_overridable() -> None:
    a = LlamaCppAdapter(backend_id="llamacpp-secondary")
    assert a.backend_id == "llamacpp-secondary"


# ---------------------------------------------------------------------------
# Loopback enforcement
# ---------------------------------------------------------------------------


def test_non_loopback_endpoint_raises() -> None:
    with pytest.raises(LocalhostOnlyError) as exc:
        LlamaCppAdapter(endpoint="http://192.168.1.50:8080")
    # Adapter-specific message — surfaces "llama.cpp", not "Ollama".
    assert "llama.cpp" in str(exc.value)


def test_ipv6_loopback_accepted() -> None:
    a = LlamaCppAdapter(endpoint="http://[::1]:8080")
    assert a.endpoint == "http://[::1]:8080"


# ---------------------------------------------------------------------------
# Non-streaming chat — defensive parsing
# ---------------------------------------------------------------------------


async def test_response_without_usage_block(httpx_mock: HTTPXMock) -> None:
    """``./server`` builds may omit ``usage``; adapter must NOT crash."""
    httpx_mock.add_response(
        url=_LLAMACPP_CHAT_URL,
        method="POST",
        json=_load_fixture("llamacpp_response_missing_usage.json"),
    )

    async with LlamaCppAdapter() as adapter:
        response = await adapter.chat(
            messages=[{"role": "user", "content": "Hi."}],
            max_tokens=50,
        )

    assert response["choices"][0]["message"]["content"] == "Hello back from llama.cpp."
    # The adapter pass-throughs the dict; absence of usage is the caller's problem.
    assert "usage" not in response


@pytest.mark.parametrize("finish_reason", ["stop", "end_turn", "length"])
async def test_finish_reason_variants(httpx_mock: HTTPXMock, finish_reason: str) -> None:
    """llama.cpp varies ``finish_reason`` across builds. Pass-through dict."""
    payload = {
        "object": "chat.completion",
        "created": 1717500001,
        "model": "qwen2.5-3b-instruct.gguf",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "ok"},
                "finish_reason": finish_reason,
            }
        ],
    }
    httpx_mock.add_response(url=_LLAMACPP_CHAT_URL, method="POST", json=payload)

    async with LlamaCppAdapter() as adapter:
        response = await adapter.chat(messages=[{"role": "user", "content": "Hi."}])

    assert response["choices"][0]["finish_reason"] == finish_reason


async def test_response_missing_id_field(httpx_mock: HTTPXMock) -> None:
    """Some ``./server`` builds omit the top-level ``id``. Don't crash."""
    payload = {
        "object": "chat.completion",
        "created": 1717500001,
        "model": "qwen2.5-3b-instruct.gguf",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "ok"},
                "finish_reason": "stop",
            }
        ],
    }
    httpx_mock.add_response(url=_LLAMACPP_CHAT_URL, method="POST", json=payload)

    async with LlamaCppAdapter() as adapter:
        response = await adapter.chat(messages=[{"role": "user", "content": "Hi."}])

    assert "id" not in response
    assert response["choices"][0]["message"]["content"] == "ok"


# ---------------------------------------------------------------------------
# Payload shape
# ---------------------------------------------------------------------------


async def test_payload_omits_model_when_default_none(httpx_mock: HTTPXMock) -> None:
    """``./server`` pins the model at launch; we don't put ``"model": null``
    on the wire because that confuses some builds."""
    httpx_mock.add_response(
        url=_LLAMACPP_CHAT_URL,
        method="POST",
        json=_load_fixture("llamacpp_response_missing_usage.json"),
    )

    async with LlamaCppAdapter() as adapter:
        await adapter.chat(messages=[{"role": "user", "content": "Hi."}])

    request = httpx_mock.get_request()
    assert request is not None
    body = json.loads(request.read())
    assert "model" not in body
    assert body["stream"] is False


async def test_payload_includes_model_when_caller_provides(httpx_mock: HTTPXMock) -> None:
    """If caller passes ``model=``, we forward it (newer ``./server`` builds
    may honour it for model-switching scenarios)."""
    httpx_mock.add_response(
        url=_LLAMACPP_CHAT_URL,
        method="POST",
        json=_load_fixture("llamacpp_response_missing_usage.json"),
    )

    async with LlamaCppAdapter() as adapter:
        await adapter.chat(
            messages=[{"role": "user", "content": "Hi."}],
            model="qwen2.5-3b-q4.gguf",
        )

    request = httpx_mock.get_request()
    assert request is not None
    body = json.loads(request.read())
    assert body["model"] == "qwen2.5-3b-q4.gguf"


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------


async def test_stream_yields_deltas(httpx_mock: HTTPXMock) -> None:
    """SSE streaming works the same as Ollama (shared parser)."""
    sse_body = (
        b'data: {"choices":[{"index":0,"delta":{"content":"He"}}]}\n\n'
        b'data: {"choices":[{"index":0,"delta":{"content":"llo"}}]}\n\n'
        b"data: [DONE]\n\n"
    )
    httpx_mock.add_response(
        url=_LLAMACPP_CHAT_URL,
        method="POST",
        content=sse_body,
        headers={"content-type": "text/event-stream"},
    )

    async with LlamaCppAdapter() as adapter:
        stream = await adapter.chat(
            messages=[{"role": "user", "content": "Hi."}],
            stream=True,
        )
        deltas = [d async for d in stream]  # type: ignore[union-attr]

    assert len(deltas) == 2
    content = "".join(d["choices"][0]["delta"].get("content", "") for d in deltas)
    assert content == "Hello"


# ---------------------------------------------------------------------------
# Context manager
# ---------------------------------------------------------------------------


async def test_context_manager_closes_client() -> None:
    adapter = LlamaCppAdapter()
    assert adapter._client.is_closed is False  # type: ignore[attr-defined]
    async with adapter:
        pass
    assert adapter._client.is_closed is True  # type: ignore[attr-defined]
