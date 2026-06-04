"""Tests for OpenAIAdapter — mocked at the HTTP layer via pytest-httpx."""

from __future__ import annotations

import sys
from typing import Any

import pytest
from pytest_httpx import HTTPXMock

from ogentic_router.adapters import AdapterConfigError, AdapterImportError, OpenAIAdapter

_OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"


def _chat_completion_payload(text: str = "Hello back.") -> dict[str, Any]:
    """Canonical OpenAI chat-completion JSON the SDK can parse."""
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 1_700_000_000,
        "model": "gpt-4o-mini",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
    }


def test_backend_id_and_is_local_defaults() -> None:
    a = OpenAIAdapter(api_key="sk-test")
    assert a.backend_id == "openai-cloud"
    assert a.is_local is False


def test_backend_id_overridable() -> None:
    a = OpenAIAdapter(api_key="sk-test", backend_id="openai-proxy-eu")
    assert a.backend_id == "openai-proxy-eu"


def test_allowlist_rejects_bad_base_url() -> None:
    with pytest.raises(AdapterConfigError) as exc:
        OpenAIAdapter(api_key="sk-test", base_url="https://evil.invalid")
    assert "ALLOWED_OPENAI_HOSTS" in str(exc.value)


async def test_chat_returns_provider_native(httpx_mock: HTTPXMock) -> None:
    """``chat()`` returns the SDK's ChatCompletion shape with extractable text."""
    httpx_mock.add_response(
        url=_OPENAI_CHAT_URL,
        method="POST",
        json=_chat_completion_payload("Hello back."),
    )

    a = OpenAIAdapter(api_key="sk-test")
    response = await a.chat(
        messages=[
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hi."},
        ],
        max_tokens=50,
        temperature=0.0,
    )

    # Provider-native shape — no router normalization.
    assert response.choices[0].message.content == "Hello back."
    assert response.model == "gpt-4o-mini"


async def test_chat_forwards_messages_unchanged(httpx_mock: HTTPXMock) -> None:
    """The OpenAI adapter does NOT translate; messages list goes through verbatim."""
    httpx_mock.add_response(
        url=_OPENAI_CHAT_URL,
        method="POST",
        json=_chat_completion_payload(),
    )

    a = OpenAIAdapter(api_key="sk-test")
    messages = [
        {"role": "system", "content": "Sys 1"},
        {"role": "system", "content": "Sys 2"},
        {"role": "user", "content": "Hello."},
    ]
    await a.chat(messages=messages, max_tokens=10)

    request = httpx_mock.get_request()
    assert request is not None
    body = request.read()
    import json as _json

    parsed = _json.loads(body)
    assert parsed["messages"] == messages
    assert parsed["model"] == "gpt-4o-mini"
    assert parsed["max_tokens"] == 10


async def test_chat_uses_override_model(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=_OPENAI_CHAT_URL,
        method="POST",
        json=_chat_completion_payload(),
    )

    a = OpenAIAdapter(api_key="sk-test")
    await a.chat(messages=[{"role": "user", "content": "hi"}], model="gpt-4o")

    import json as _json

    request = httpx_mock.get_request()
    assert request is not None
    assert _json.loads(request.read())["model"] == "gpt-4o"


async def test_stream_yields_deltas(httpx_mock: HTTPXMock) -> None:
    """``stream=True`` returns an async iterator yielding ChatCompletionChunk events."""
    # SSE event stream — two delta chunks then [DONE].
    sse_body = (
        b'data: {"id":"chatcmpl-stream","object":"chat.completion.chunk",'
        b'"created":1700000000,"model":"gpt-4o-mini","choices":[{"index":0,'
        b'"delta":{"role":"assistant","content":"Hel"},"finish_reason":null}]}\n\n'
        b'data: {"id":"chatcmpl-stream","object":"chat.completion.chunk",'
        b'"created":1700000000,"model":"gpt-4o-mini","choices":[{"index":0,'
        b'"delta":{"content":"lo"},"finish_reason":null}]}\n\n'
        b"data: [DONE]\n\n"
    )
    httpx_mock.add_response(
        url=_OPENAI_CHAT_URL,
        method="POST",
        content=sse_body,
        headers={"content-type": "text/event-stream"},
    )

    a = OpenAIAdapter(api_key="sk-test")
    stream = await a.chat(messages=[{"role": "user", "content": "hi"}], stream=True)

    chunks = []
    async for chunk in stream:
        chunks.append(chunk)
    assert len(chunks) >= 1
    # Each chunk is a provider-native ChatCompletionChunk
    contents = [c.choices[0].delta.content for c in chunks if c.choices and c.choices[0].delta.content]
    assert "".join(contents) == "Hello"


def test_missing_extra_raises_with_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    """If ``openai`` isn't importable, construction raises AdapterImportError."""
    # Drop the cached openai modules and block re-import.
    for name in list(sys.modules):
        if name == "openai" or name.startswith("openai."):
            monkeypatch.delitem(sys.modules, name, raising=False)

    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__  # type: ignore[index]

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "openai" or name.startswith("openai."):
            raise ImportError(f"No module named {name!r}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)

    with pytest.raises(AdapterImportError) as exc:
        OpenAIAdapter(api_key="sk-test")
    assert "pip install 'ogentic-router[cloud]'" in str(exc.value)
