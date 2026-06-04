"""Tests for the shared SSE delta parser.

The parser is the single source of truth for streaming chat completions
across both local adapters. These tests pin the parser's behaviour
directly (without going through an adapter) so a parsing regression
shows up as an isolated failure here rather than as confusing
adapter-level test breakage.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import pytest

from ogentic_router.adapters._sse import aiter_sse_deltas


class _FakeResponse:
    """Minimal stand-in for ``httpx.Response`` exposing ``aiter_lines``.

    The parser only calls ``aiter_lines``; constructing a real
    ``httpx.Response`` for these unit tests would be overkill (and would
    drag the test into httpx_mock territory unnecessarily).
    """

    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    async def aiter_lines(self) -> AsyncIterator[str]:
        for line in self._lines:
            yield line


async def _collect(it: AsyncIterator[dict[str, object]]) -> list[dict[str, object]]:
    """Helper — consume an async iterator into a list for assertions."""
    return [x async for x in it]


async def test_parses_single_delta() -> None:
    response = _FakeResponse(['data: {"choices":[{"delta":{"content":"Hi"}}]}'])
    deltas = await _collect(aiter_sse_deltas(response))  # type: ignore[arg-type]
    assert deltas == [{"choices": [{"delta": {"content": "Hi"}}]}]


async def test_parses_multiple_deltas_in_order() -> None:
    response = _FakeResponse(
        [
            'data: {"choices":[{"delta":{"content":"Hel"}}]}',
            "",
            'data: {"choices":[{"delta":{"content":"lo"}}]}',
            "",
            "data: [DONE]",
        ]
    )
    deltas = await _collect(aiter_sse_deltas(response))  # type: ignore[arg-type]
    contents = [d["choices"][0]["delta"]["content"] for d in deltas]  # type: ignore[index]
    assert contents == ["Hel", "lo"]


async def test_done_sentinel_stops_iteration() -> None:
    """Anything after ``data: [DONE]`` is ignored — protects against trailing
    keep-alives polluting the stream."""
    response = _FakeResponse(
        [
            'data: {"v":1}',
            "data: [DONE]",
            'data: {"v":2}',
        ]
    )
    deltas = await _collect(aiter_sse_deltas(response))  # type: ignore[arg-type]
    assert deltas == [{"v": 1}]


async def test_skips_empty_and_comment_lines() -> None:
    response = _FakeResponse(
        [
            "",
            ": keep-alive comment",
            'data: {"v":1}',
            "",
            ": another",
            'data: {"v":2}',
        ]
    )
    deltas = await _collect(aiter_sse_deltas(response))  # type: ignore[arg-type]
    assert deltas == [{"v": 1}, {"v": 2}]


async def test_skips_non_data_fields() -> None:
    """``event:`` / ``id:`` lines are valid SSE but not what we care about."""
    response = _FakeResponse(
        [
            "event: message",
            "id: 42",
            'data: {"v":1}',
        ]
    )
    deltas = await _collect(aiter_sse_deltas(response))  # type: ignore[arg-type]
    assert deltas == [{"v": 1}]


async def test_malformed_json_raises() -> None:
    """Surface real parsing errors rather than silently dropping chunks."""
    response = _FakeResponse(["data: {not json"])
    with pytest.raises(json.JSONDecodeError):
        await _collect(aiter_sse_deltas(response))  # type: ignore[arg-type]
