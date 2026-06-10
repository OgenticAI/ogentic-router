"""SSE wire-format serialiser for OpenAI-shaped streaming responses (OGE-583).

Produces the ``data: {...}\\n\\n`` framing that OpenAI clients expect.
The final sentinel ``data: [DONE]\\n\\n`` is emitted after the last chunk.

Usage::

    async def generate():
        for chunk in chunks:
            yield sse_data(chunk)
        yield sse_done()

    return StreamingResponse(generate(), media_type="text/event-stream")
"""

from __future__ import annotations

import json
from typing import Any


def sse_data(payload: dict[str, Any]) -> str:
    """Serialise a JSON payload to an SSE ``data:`` frame.

    Returns the string ``"data: <json>\\n\\n"`` — the trailing double newline
    is the SSE framing spec's event delimiter.
    """
    return f"data: {json.dumps(payload)}\n\n"


def sse_done() -> str:
    """Return the SSE stream terminator ``data: [DONE]\\n\\n``."""
    return "data: [DONE]\n\n"


__all__ = ["sse_data", "sse_done"]
