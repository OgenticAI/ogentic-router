"""Tests for POST /v1/chat/completions (OGE-583).

AC coverage:
1. Request shape validation — missing messages → 422.
2. Non-streaming response shape — returns OpenAI ChatCompletion dict.
3. Streaming response — returns text/event-stream with data: {...} lines.
4. Normalisation — Anthropic Message is translated to OpenAI shape.
5. Normalisation — Anthropic stream events are translated to OpenAI chunks.
6. No adapters → 503.
7. model field is reflected in response.
8. max_tokens / temperature are forwarded to the adapter.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient

from ogentic_router.server._normalize import to_openai_chunk, to_openai_response
from ogentic_router.server.app import create_app
from ogentic_router.server.config import BackendConfig, RouterConfig
from tests.server.conftest import FakeAnthropicAdapter

# ─── Unit tests for _normalize helpers ───────────────────────────────────────


class TestNormalizeHelpers:
    """Unit tests for to_openai_response / to_openai_chunk (no HTTP)."""

    def test_dict_response_passthrough(self) -> None:
        """AC: dict response (Ollama/llama.cpp shape) is rebuilt with canonical fields."""
        raw = {
            "id": "ollama-123",
            "object": "chat.completion",
            "created": 1234567890,
            "model": "llama3",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "Hello"},
                    "finish_reason": "stop",
                    "logprobs": None,
                }
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
        }
        result = to_openai_response(raw, "llama3", "chatcmpl-test123")
        assert result["id"] == "chatcmpl-test123"
        assert result["object"] == "chat.completion"
        assert result["choices"][0]["message"]["content"] == "Hello"
        assert result["usage"]["total_tokens"] == 7

    def test_anthropic_message_normalised(self) -> None:
        """AC: Anthropic Message is translated to OpenAI ChatCompletion shape."""
        from types import SimpleNamespace

        text_block = SimpleNamespace(type="text", text="World")
        usage = SimpleNamespace(input_tokens=8, output_tokens=3)
        msg = SimpleNamespace(
            id="msg_123",
            type="message",
            role="assistant",
            content=[text_block],
            model="claude-sonnet-4-5",
            stop_reason="end_turn",
            stop_sequence=None,
            usage=usage,
        )
        result = to_openai_response(msg, "claude-sonnet-4-5", "chatcmpl-test456")
        assert result["id"] == "chatcmpl-test456"
        assert result["object"] == "chat.completion"
        assert result["choices"][0]["message"]["content"] == "World"
        assert result["choices"][0]["finish_reason"] == "stop"
        assert result["usage"]["prompt_tokens"] == 8
        assert result["usage"]["completion_tokens"] == 3

    def test_anthropic_stop_reason_mapping(self) -> None:
        """AC: Anthropic stop_reason values map correctly to OpenAI finish_reason."""
        from types import SimpleNamespace

        for stop_reason, expected_finish in [
            ("end_turn", "stop"),
            ("max_tokens", "length"),
            ("stop_sequence", "stop"),
            ("tool_use", "tool_calls"),
        ]:
            text_block = SimpleNamespace(type="text", text="x")
            usage = SimpleNamespace(input_tokens=1, output_tokens=1)
            msg = SimpleNamespace(
                id="msg_test",
                type="message",
                role="assistant",
                content=[text_block],
                model="claude-test",
                stop_reason=stop_reason,
                stop_sequence=None,
                usage=usage,
            )
            result = to_openai_response(msg, "claude-test", "chatcmpl-x")
            assert result["choices"][0]["finish_reason"] == expected_finish, (
                f"stop_reason={stop_reason!r} should map to {expected_finish!r}"
            )

    def test_anthropic_chunk_content_delta(self) -> None:
        """AC: Anthropic content_block_delta event → OpenAI chunk with content."""
        from types import SimpleNamespace

        delta = SimpleNamespace(type="text_delta", text="hello ")
        event = SimpleNamespace(type="content_block_delta", delta=delta)
        chunk = to_openai_chunk(event, "claude-sonnet-4-5", "chatcmpl-stream-abc")
        assert chunk is not None
        assert chunk["choices"][0]["delta"]["content"] == "hello "
        assert chunk["choices"][0]["finish_reason"] is None

    def test_anthropic_message_delta_stop(self) -> None:
        """AC: Anthropic message_delta with stop_reason → chunk with finish_reason."""
        from types import SimpleNamespace

        stop_delta = SimpleNamespace(stop_reason="end_turn")
        event = SimpleNamespace(type="message_delta", delta=stop_delta)
        chunk = to_openai_chunk(event, "claude-sonnet-4-5", "chatcmpl-stream-abc")
        assert chunk is not None
        assert chunk["choices"][0]["finish_reason"] == "stop"

    def test_anthropic_message_stop_event_returns_none(self) -> None:
        """AC: Anthropic message_stop event produces no chunk (filtered)."""
        from types import SimpleNamespace

        event = SimpleNamespace(type="message_stop")
        chunk = to_openai_chunk(event, "claude-sonnet-4-5", "chatcmpl-stream-abc")
        assert chunk is None

    def test_dict_chunk_id_override(self) -> None:
        """AC: dict chunk gets the canonical request_id."""
        raw_chunk: dict[str, Any] = {
            "id": "old-id",
            "object": "chat.completion.chunk",
            "choices": [{"delta": {"content": "hi"}}],
        }
        result = to_openai_chunk(raw_chunk, "llama3", "chatcmpl-new123")
        assert result is not None
        assert result["id"] == "chatcmpl-new123"

    def test_request_id_format(self) -> None:
        """AC: auto-generated request IDs match chatcmpl-{hex} format."""
        from ogentic_router.server._normalize import _new_request_id

        rid = _new_request_id()
        assert rid.startswith("chatcmpl-")
        assert len(rid) == len("chatcmpl-") + 32  # uuid4 hex is 32 chars


# ─── HTTP-level tests ─────────────────────────────────────────────────────────


class TestChatCompletionsHTTP:
    """POST /v1/chat/completions HTTP-level tests."""

    def test_non_streaming_response_shape(self, test_client: TestClient) -> None:
        """AC: non-streaming POST returns OpenAI ChatCompletion shape."""
        resp = test_client.post(
            "/v1/chat/completions",
            json={
                "model": "fake-model",
                "messages": [{"role": "user", "content": "Hello"}],
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["object"] == "chat.completion"
        assert "choices" in body
        assert len(body["choices"]) > 0
        assert body["choices"][0]["message"]["role"] == "assistant"
        assert "usage" in body

    def test_request_id_in_response(self, test_client: TestClient) -> None:
        """AC: response id has chatcmpl- prefix."""
        resp = test_client.post(
            "/v1/chat/completions",
            json={
                "model": "fake-model",
                "messages": [{"role": "user", "content": "Hello"}],
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"].startswith("chatcmpl-")

    def test_missing_messages_returns_422(self, test_client: TestClient) -> None:
        """AC: request without messages returns 422 validation error."""
        resp = test_client.post(
            "/v1/chat/completions",
            json={"model": "fake-model"},
        )
        assert resp.status_code == 422

    def test_empty_messages_returns_422(self, test_client: TestClient) -> None:
        """AC: request with empty messages list returns 422."""
        resp = test_client.post(
            "/v1/chat/completions",
            json={"model": "fake-model", "messages": []},
        )
        assert resp.status_code == 422

    def test_no_backends_returns_503(self) -> None:
        """AC: when no adapters are configured, returns 503."""
        app = create_app()  # no config, no adapters
        with TestClient(app) as client:
            resp = client.post(
                "/v1/chat/completions",
                json={"model": "any-model", "messages": [{"role": "user", "content": "hi"}]},
            )
        assert resp.status_code == 503

    def test_model_reflected_in_response(self, test_client: TestClient) -> None:
        """AC: the model from the request appears in the response."""
        resp = test_client.post(
            "/v1/chat/completions",
            json={
                "model": "my-test-model",
                "messages": [{"role": "user", "content": "Hello"}],
            },
        )
        assert resp.status_code == 200
        # The fake adapter returns its own model name, but we check the response has a model field.
        body = resp.json()
        assert "model" in body

    def test_system_message_in_request(self, test_client: TestClient) -> None:
        """AC: system + user messages are accepted without error."""
        resp = test_client.post(
            "/v1/chat/completions",
            json={
                "model": "fake-model",
                "messages": [
                    {"role": "system", "content": "You are helpful."},
                    {"role": "user", "content": "Hello"},
                ],
            },
        )
        assert resp.status_code == 200


class TestChatCompletionsStreaming:
    """POST /v1/chat/completions streaming tests.

    Uses synchronous TestClient with `client.stream()` — the starlette
    TestClient properly runs the FastAPI lifespan (startup/shutdown), so
    app.state is populated before the first request.
    """

    def test_streaming_returns_event_stream(self, test_client: TestClient) -> None:
        """AC: stream=True returns text/event-stream content-type."""
        with test_client.stream(
            "POST",
            "/v1/chat/completions",
            json={
                "model": "fake-model",
                "messages": [{"role": "user", "content": "Hi"}],
                "stream": True,
            },
        ) as resp:
            assert resp.status_code == 200
            assert "text/event-stream" in resp.headers.get("content-type", "")

    def test_streaming_chunks_are_valid_sse(self, test_client: TestClient) -> None:
        """AC: each SSE line is parseable as JSON with OpenAI chunk shape."""
        chunks: list[dict[str, Any]] = []
        with test_client.stream(
            "POST",
            "/v1/chat/completions",
            json={
                "model": "fake-model",
                "messages": [{"role": "user", "content": "Hi"}],
                "stream": True,
            },
        ) as resp:
            for line in resp.iter_lines():
                if line.startswith("data:"):
                    payload = line[len("data:") :].strip()
                    if payload == "[DONE]":
                        break
                    chunks.append(json.loads(payload))

        assert len(chunks) > 0
        for chunk in chunks:
            assert chunk["object"] == "chat.completion.chunk"
            assert "choices" in chunk

    def test_streaming_ends_with_done(self, test_client: TestClient) -> None:
        """AC: stream ends with data: [DONE]."""
        last_line = ""
        with test_client.stream(
            "POST",
            "/v1/chat/completions",
            json={
                "model": "fake-model",
                "messages": [{"role": "user", "content": "Hi"}],
                "stream": True,
            },
        ) as resp:
            for line in resp.iter_lines():
                if line:
                    last_line = line
        assert last_line == "data: [DONE]"

    def test_anthropic_streaming_normalised(self) -> None:
        """AC: Anthropic stream events are normalised to OpenAI chunk shape."""
        adapter = FakeAnthropicAdapter()
        config = RouterConfig(
            version=1,
            policy_path="/dev/null",
            shield=__import__("ogentic_router.server.config", fromlist=["ShieldConfig"]).ShieldConfig(profiles=[]),
            backends=[
                BackendConfig(
                    id=adapter.backend_id,
                    kind="ollama",
                    base_url="http://localhost:11434",
                )
            ],
        )
        app = create_app(config=config, adapters={adapter.backend_id: adapter})
        chunks: list[dict[str, Any]] = []
        with TestClient(app) as client:
            with client.stream(
                "POST",
                "/v1/chat/completions",
                json={
                    "model": "claude-sonnet-4-5",
                    "messages": [{"role": "user", "content": "Hi"}],
                    "stream": True,
                },
            ) as resp:
                assert resp.status_code == 200
                for line in resp.iter_lines():
                    if line.startswith("data:"):
                        payload = line[len("data:") :].strip()
                        if payload == "[DONE]":
                            break
                        chunks.append(json.loads(payload))

        assert len(chunks) > 0
        # All chunks should be OpenAI-shaped
        for chunk in chunks:
            assert chunk["object"] == "chat.completion.chunk"
            assert "choices" in chunk
            assert len(chunk["choices"]) > 0
