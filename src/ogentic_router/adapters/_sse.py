"""Minimal SSE delta parser for streaming chat completions.

Both Ollama and llama.cpp expose an OpenAI-compatible
``POST /v1/chat/completions`` endpoint that emits ``text/event-stream``
responses when ``"stream": true`` is set. The wire format is the standard
``data: {...}\\n\\n`` framing with a sentinel ``data: [DONE]`` to mark the end
of the stream.

Single parser, two callers — kept here so both ``OllamaAdapter`` and
``LlamaCppAdapter`` reuse the same battle-tested logic. Lazy-imports
``httpx`` types only via :class:`typing.TYPE_CHECKING` so importing
``ogentic_router.adapters._sse`` is safe without the ``[local]`` extra.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import httpx


async def aiter_sse_deltas(response: httpx.Response) -> AsyncIterator[dict[str, Any]]:
    """Yield parsed ``data: {...}`` JSON events from an SSE response stream.

    Parameters
    ----------
    response:
        An ``httpx.Response`` opened with ``stream=True`` — the caller is
        responsible for the ``async with`` lifecycle (the parser only reads,
        it doesn't close).

    Yields
    ------
    dict
        Each JSON event from the ``data:`` lines. The terminator
        ``data: [DONE]`` is filtered out; empty lines and SSE comments
        (lines starting with ``:``) are skipped.

    Raises
    ------
    json.JSONDecodeError
        If a ``data:`` line contains malformed JSON. Surfacing this rather
        than silently dropping the chunk lets the caller see real upstream
        bugs — partial buffering / chunked SSE framing is httpx's job.
    """
    async for line in response.aiter_lines():
        if not line or line.startswith(":"):
            # Empty keep-alive line or SSE comment — skip.
            continue
        if not line.startswith("data:"):
            # Other SSE fields (``event:``, ``id:`` etc.) — we only care
            # about ``data:`` payloads for OpenAI-compatible streams.
            continue
        payload = line[len("data:") :].strip()
        if payload == "[DONE]":
            return
        if not payload:
            continue
        yield json.loads(payload)
