"""Shared fixtures for the server test suite (OGE-583).

Fixture modes:

1. ``test_client`` — synchronous ``TestClient`` (starlette) with ASGI transport.
   This is the primary test fixture. The starlette TestClient runs the FastAPI
   lifespan (startup/shutdown events) inside ``__enter__`` / ``__exit__``, so
   ``app.state`` is fully populated before any request is made.

2. ``async_client`` — ``httpx.AsyncClient`` with ASGI transport.
   Kept for any tests that need async patterns. NOTE: httpx ASGITransport does
   NOT run the FastAPI lifespan; tests using this fixture must inject adapters
   directly and not rely on ``app.state`` being populated by lifespan.

Both fixtures inject a pre-built adapter map so no real API keys are needed.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from ogentic_router.server.app import create_app
from ogentic_router.server.config import BackendConfig, RouterConfig, ShieldConfig

# ─── Fake adapter ────────────────────────────────────────────────────────────


class FakeAdapter:
    """Minimal adapter stub that satisfies the Adapter Protocol.

    Returns canned responses so tests run without real API keys / network
    calls. Pass ``stream=True`` to ``chat()`` to get a two-chunk async
    iterator.
    """

    backend_id: str = "fake-backend"
    is_local: bool = True

    def __init__(
        self,
        backend_id: str = "fake-backend",
        *,
        response_text: str = "Hello from fake adapter",
        model: str = "fake-model",
    ) -> None:
        self.backend_id = backend_id
        self._response_text = response_text
        self._model = model

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        stream: bool = False,
    ) -> Any | AsyncIterator[Any]:
        resolved_model = model or self._model
        if stream:
            return self._stream_chunks(resolved_model)
        return self._blocking_response(resolved_model)

    def _blocking_response(self, model: str) -> dict[str, Any]:
        """Return an OpenAI-shaped dict response."""
        return {
            "id": "chatcmpl-fake",
            "object": "chat.completion",
            "created": 1234567890,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": self._response_text},
                    "finish_reason": "stop",
                    "logprobs": None,
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }

    async def _stream_chunks(self, model: str) -> AsyncIterator[dict[str, Any]]:
        """Yield two OpenAI-shaped chunks then stop."""
        words = self._response_text.split()
        for word in words[:2]:
            yield {
                "id": "chatcmpl-fake",
                "object": "chat.completion.chunk",
                "created": 1234567890,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant", "content": word + " "},
                        "finish_reason": None,
                        "logprobs": None,
                    }
                ],
            }
        yield {
            "id": "chatcmpl-fake",
            "object": "chat.completion.chunk",
            "created": 1234567890,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "delta": {},
                    "finish_reason": "stop",
                    "logprobs": None,
                }
            ],
        }


class FakeAnthropicAdapter(FakeAdapter):
    """Fake adapter that returns Anthropic-shaped Message objects for normalisation tests."""

    backend_id: str = "anthropic-fake"

    def _blocking_response(self, model: str) -> Any:  # type: ignore[override]
        """Return an Anthropic-shaped Message SimpleNamespace."""
        text_block = SimpleNamespace(type="text", text=self._response_text)
        usage = SimpleNamespace(input_tokens=10, output_tokens=5)
        return SimpleNamespace(
            id="msg_fake",
            type="message",
            role="assistant",
            content=[text_block],
            model=model,
            stop_reason="end_turn",
            stop_sequence=None,
            usage=usage,
        )

    async def _stream_chunks(self, model: str) -> AsyncIterator[Any]:  # type: ignore[override]
        """Yield Anthropic-shaped stream events."""
        # content_block_delta events
        for word in self._response_text.split()[:2]:
            delta = SimpleNamespace(type="text_delta", text=word + " ")
            yield SimpleNamespace(type="content_block_delta", delta=delta)
        # message_delta with stop_reason
        stop_delta = SimpleNamespace(stop_reason="end_turn")
        yield SimpleNamespace(type="message_delta", delta=stop_delta)
        # message_stop (should be filtered out)
        yield SimpleNamespace(type="message_stop")


# ─── RouterConfig helpers ─────────────────────────────────────────────────────


def _minimal_router_config() -> RouterConfig:
    """Build a minimal RouterConfig with no backends (test mode)."""
    return RouterConfig(
        version=1,
        policy_path="/dev/null",  # won't be read — policy=None in lifespan
        shield=ShieldConfig(profiles=[]),
        audit={},
        backends=[],
    )


def _router_config_with_backend(backend_id: str = "fake-backend") -> RouterConfig:
    """Build a RouterConfig referencing one fake backend."""
    return RouterConfig(
        version=1,
        policy_path="/dev/null",
        shield=ShieldConfig(profiles=[]),
        audit={},
        backends=[
            BackendConfig(
                id=backend_id,
                kind="ollama",  # doesn't matter — adapter is injected
                base_url="http://localhost:11434",
            )
        ],
    )


# ─── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def fake_adapter() -> FakeAdapter:
    """A pre-built FakeAdapter instance."""
    return FakeAdapter()


@pytest.fixture
def fake_anthropic_adapter() -> FakeAnthropicAdapter:
    """A FakeAnthropicAdapter that returns Anthropic-shaped objects."""
    return FakeAnthropicAdapter()


@pytest.fixture
def test_client(fake_adapter: FakeAdapter) -> TestClient:
    """Synchronous TestClient with a fake adapter injected.

    Uses starlette's TestClient which properly runs the FastAPI lifespan
    (startup/shutdown) so app.state is populated before any request.
    """
    config = _router_config_with_backend(fake_adapter.backend_id)
    app = create_app(
        config=config,
        adapters={"fake-backend": fake_adapter},
    )
    with TestClient(app, raise_server_exceptions=True) as client:
        yield client  # type: ignore[misc]


@pytest_asyncio.fixture
async def async_client(fake_adapter: FakeAdapter) -> AsyncIterator[httpx.AsyncClient]:
    """Async httpx.AsyncClient with ASGI transport.

    NOTE: httpx ASGITransport does NOT run the FastAPI lifespan. This fixture
    is kept for forward-compat but most tests should use ``test_client``.
    Tests using this fixture inject adapters directly via create_app().
    """
    config = _router_config_with_backend(fake_adapter.backend_id)
    app = create_app(
        config=config,
        adapters={"fake-backend": fake_adapter},
    )
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest_asyncio.fixture
async def anthropic_async_client(fake_anthropic_adapter: FakeAnthropicAdapter) -> AsyncIterator[httpx.AsyncClient]:
    """Async httpx.AsyncClient backed by the Anthropic fake adapter."""
    config = _router_config_with_backend(fake_anthropic_adapter.backend_id)
    app = create_app(
        config=config,
        adapters={"anthropic-fake": fake_anthropic_adapter},
    )
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
