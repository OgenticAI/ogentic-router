"""Tests for AnthropicAdapter — mocked at the HTTP layer via pytest-httpx."""

from __future__ import annotations

import json as _json
import sys
from typing import Any

import pytest
from pytest_httpx import HTTPXMock

from ogentic_router.adapters import AdapterConfigError, AdapterImportError, AnthropicAdapter
from ogentic_router.adapters._anthropic_translate import extract_system_and_messages

_ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"


def _message_payload(text: str = "Hello back.") -> dict[str, Any]:
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-4-5",
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 5, "output_tokens": 3},
    }


# ---------- pure-function translation tests ----------


def test_translate_no_system_message() -> None:
    system, rest = extract_system_and_messages(
        [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
    )
    assert system is None
    assert rest == [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]


def test_translate_single_system_message() -> None:
    system, rest = extract_system_and_messages(
        [
            {"role": "system", "content": "Be careful."},
            {"role": "user", "content": "Summarise."},
        ]
    )
    assert system == "Be careful."
    assert rest == [{"role": "user", "content": "Summarise."}]


def test_translate_multiple_system_messages_joined() -> None:
    system, rest = extract_system_and_messages(
        [
            {"role": "system", "content": "First instruction."},
            {"role": "user", "content": "Hi."},
            {"role": "system", "content": "Second instruction."},
        ]
    )
    assert system == "First instruction.\n\nSecond instruction."
    assert rest == [{"role": "user", "content": "Hi."}]


def test_translate_handles_content_part_list() -> None:
    """OpenAI permits content as a list of parts; we flatten text parts."""
    system, _ = extract_system_and_messages(
        [
            {
                "role": "system",
                "content": [
                    {"type": "text", "text": "Part A"},
                    {"type": "text", "text": "Part B"},
                ],
            },
            {"role": "user", "content": "go"},
        ]
    )
    assert system == "Part A\n\nPart B"


# ---------- adapter-level tests ----------


def test_backend_id_and_is_local_defaults() -> None:
    a = AnthropicAdapter(api_key="sk-ant-test")
    assert a.backend_id == "anthropic-cloud"
    assert a.is_local is False


def test_backend_id_overridable() -> None:
    a = AnthropicAdapter(api_key="sk-ant-test", backend_id="anthropic-proxy")
    assert a.backend_id == "anthropic-proxy"


def test_allowlist_rejects_bad_base_url() -> None:
    with pytest.raises(AdapterConfigError) as exc:
        AnthropicAdapter(api_key="sk-ant-test", base_url="https://evil.invalid")
    assert "ALLOWED_ANTHROPIC_HOSTS" in str(exc.value)


async def test_system_message_extracted_on_the_wire(httpx_mock: HTTPXMock) -> None:
    """The system message ends up in ``system=``, not in ``messages=``."""
    httpx_mock.add_response(
        url=_ANTHROPIC_MESSAGES_URL,
        method="POST",
        json=_message_payload(),
    )

    a = AnthropicAdapter(api_key="sk-ant-test")
    await a.chat(
        messages=[
            {"role": "system", "content": "X"},
            {"role": "user", "content": "Y"},
        ],
        max_tokens=64,
    )

    request = httpx_mock.get_request()
    assert request is not None
    body = _json.loads(request.read())
    assert body["system"] == "X"
    assert body["messages"] == [{"role": "user", "content": "Y"}]


async def test_multiple_system_messages_joined_on_wire(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=_ANTHROPIC_MESSAGES_URL,
        method="POST",
        json=_message_payload(),
    )

    a = AnthropicAdapter(api_key="sk-ant-test")
    await a.chat(
        messages=[
            {"role": "system", "content": "First."},
            {"role": "system", "content": "Second."},
            {"role": "user", "content": "Go."},
        ],
        max_tokens=64,
    )

    request = httpx_mock.get_request()
    assert request is not None
    body = _json.loads(request.read())
    assert body["system"] == "First.\n\nSecond."
    assert body["messages"] == [{"role": "user", "content": "Go."}]


async def test_chat_returns_provider_native(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=_ANTHROPIC_MESSAGES_URL,
        method="POST",
        json=_message_payload("Roger."),
    )

    a = AnthropicAdapter(api_key="sk-ant-test")
    response = await a.chat(
        messages=[{"role": "user", "content": "Hi."}],
        max_tokens=64,
    )

    # Provider-native shape: a Message with .content[0].text
    assert response.content[0].text == "Roger."
    assert response.model == "claude-sonnet-4-5"


async def test_chat_omits_system_when_no_system_message(httpx_mock: HTTPXMock) -> None:
    """If the caller passes no system message, ``system`` is not sent."""
    httpx_mock.add_response(
        url=_ANTHROPIC_MESSAGES_URL,
        method="POST",
        json=_message_payload(),
    )

    a = AnthropicAdapter(api_key="sk-ant-test")
    await a.chat(messages=[{"role": "user", "content": "Hi."}], max_tokens=16)

    request = httpx_mock.get_request()
    assert request is not None
    body = _json.loads(request.read())
    assert "system" not in body


async def test_stream_yields_deltas(httpx_mock: HTTPXMock) -> None:
    """``stream=True`` returns an async iterator of provider-native events."""
    sse_body = (
        b'event: message_start\n'
        b'data: {"type":"message_start","message":{"id":"msg_test","type":"message",'
        b'"role":"assistant","model":"claude-sonnet-4-5","content":[],'
        b'"stop_reason":null,"stop_sequence":null,'
        b'"usage":{"input_tokens":5,"output_tokens":0}}}\n\n'
        b'event: content_block_start\n'
        b'data: {"type":"content_block_start","index":0,'
        b'"content_block":{"type":"text","text":""}}\n\n'
        b'event: content_block_delta\n'
        b'data: {"type":"content_block_delta","index":0,'
        b'"delta":{"type":"text_delta","text":"Hello"}}\n\n'
        b'event: content_block_stop\n'
        b'data: {"type":"content_block_stop","index":0}\n\n'
        b'event: message_delta\n'
        b'data: {"type":"message_delta",'
        b'"delta":{"stop_reason":"end_turn","stop_sequence":null},'
        b'"usage":{"output_tokens":1}}\n\n'
        b'event: message_stop\n'
        b'data: {"type":"message_stop"}\n\n'
    )
    httpx_mock.add_response(
        url=_ANTHROPIC_MESSAGES_URL,
        method="POST",
        content=sse_body,
        headers={"content-type": "text/event-stream"},
    )

    a = AnthropicAdapter(api_key="sk-ant-test")
    stream = await a.chat(messages=[{"role": "user", "content": "hi"}], max_tokens=32, stream=True)

    events = []
    async for ev in stream:
        events.append(ev)
    assert len(events) >= 1


def test_missing_extra_raises_with_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(sys.modules):
        if name == "anthropic" or name.startswith("anthropic."):
            monkeypatch.delitem(sys.modules, name, raising=False)

    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__  # type: ignore[index]

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "anthropic" or name.startswith("anthropic."):
            raise ImportError(f"No module named {name!r}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)

    with pytest.raises(AdapterImportError) as exc:
        AnthropicAdapter(api_key="sk-ant-test")
    assert "pip install 'ogentic-router[cloud]'" in str(exc.value)
