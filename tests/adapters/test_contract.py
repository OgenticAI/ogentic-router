"""Contract test: both adapters consume the same OpenAI-shaped payload.

The router accepts OpenAI-shaped messages everywhere. The cloud adapters
must each consume that payload and produce a response from which the
caller can extract text content. The point of this test is to lock the
Anthropic translation in one place and assert end-to-end shape parity
from the caller's perspective.
"""

from __future__ import annotations

from typing import Any

from pytest_httpx import HTTPXMock

from ogentic_router.adapters import AnthropicAdapter, OpenAIAdapter

_SHARED_PAYLOAD = [
    {"role": "system", "content": "You are a careful assistant."},
    {"role": "user", "content": "Summarise the deck."},
]


def _openai_payload(text: str) -> dict[str, Any]:
    return {
        "id": "chatcmpl-c",
        "object": "chat.completion",
        "created": 1_700_000_000,
        "model": "gpt-4o-mini",
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
    }


def _anthropic_payload(text: str) -> dict[str, Any]:
    return {
        "id": "msg_c",
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-4-5",
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 5, "output_tokens": 3},
    }


def _extract_openai_text(resp: Any) -> str:
    text: str = resp.choices[0].message.content
    return text


def _extract_anthropic_text(resp: Any) -> str:
    text: str = resp.content[0].text
    return text


async def test_both_adapters_consume_same_payload(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="https://api.openai.com/v1/chat/completions",
        method="POST",
        json=_openai_payload("OK from OpenAI."),
    )
    httpx_mock.add_response(
        url="https://api.anthropic.com/v1/messages",
        method="POST",
        json=_anthropic_payload("OK from Anthropic."),
    )

    oa = OpenAIAdapter(api_key="sk-test")
    an = AnthropicAdapter(api_key="sk-ant-test")

    # Same input, two adapters, both produce extractable text.
    oa_resp = await oa.chat(messages=_SHARED_PAYLOAD, max_tokens=64)
    an_resp = await an.chat(messages=_SHARED_PAYLOAD, max_tokens=64)

    assert _extract_openai_text(oa_resp) == "OK from OpenAI."
    assert _extract_anthropic_text(an_resp) == "OK from Anthropic."

    # Both adapters report non-local.
    assert oa.is_local is False
    assert an.is_local is False
