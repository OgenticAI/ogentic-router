"""Response normalisation — native provider shapes → OpenAI wire format (OGE-583).

The server accepts any Adapter (OpenAI, Anthropic, Ollama, llama.cpp) and
returns OpenAI-shaped JSON to the caller. Normalisation happens HERE, not in
the adapters — adapters remain pass-through and the server is the translation
boundary.

Two public functions:

- :func:`to_openai_response` — for non-streaming responses.
- :func:`to_openai_chunk`    — for individual chunks in a streaming response.

Both are pure functions (no I/O), which makes them cheap to unit-test in
isolation.

Design constraints:
- ``openai.types.chat.ChatCompletion`` passes through unchanged (already the
  right shape) — we just call ``.model_dump()`` and adjust the id field.
- ``anthropic.types.Message`` is translated to the OpenAI shape.
- Any other type (Ollama/llama.cpp return OpenAI-shaped dicts) is handled by
  coercion.
- Request-ID format: ``chatcmpl-{uuid4().hex}`` — matches OpenAI wire format.
"""

from __future__ import annotations

import time
import uuid
from typing import Any


def _new_request_id() -> str:
    """Generate a new ``chatcmpl-<hex>`` request ID."""
    return f"chatcmpl-{uuid.uuid4().hex}"


def _translate_anthropic_message(msg: Any, model: str, request_id: str) -> dict[str, Any]:
    """Translate an ``anthropic.types.Message`` to an OpenAI ChatCompletion dict.

    Anthropic ``Message`` shape (relevant fields)::

        id: str
        type: "message"
        role: "assistant"
        content: list[ContentBlock]   # TextBlock | ToolUseBlock
        model: str
        stop_reason: "end_turn" | "max_tokens" | "stop_sequence" | "tool_use"
        stop_sequence: str | None
        usage: Usage  # input_tokens, output_tokens

    OpenAI ``ChatCompletion`` shape::

        id: str
        object: "chat.completion"
        created: int
        model: str
        choices: list[Choice]
        usage: CompletionUsage
    """
    # Extract text from Anthropic content blocks.
    text_parts: list[str] = []
    raw_content = getattr(msg, "content", None) or []
    if isinstance(raw_content, list):
        for block in raw_content:
            block_type = getattr(block, "type", None)
            if block_type == "text":
                text = getattr(block, "text", "")
                if isinstance(text, str):
                    text_parts.append(text)
    elif isinstance(raw_content, str):
        text_parts.append(raw_content)

    content = "".join(text_parts)

    # Map Anthropic stop_reason → OpenAI finish_reason.
    stop_reason = getattr(msg, "stop_reason", None)
    finish_reason_map: dict[str | None, str] = {
        "end_turn": "stop",
        "max_tokens": "length",
        "stop_sequence": "stop",
        "tool_use": "tool_calls",
        None: "stop",
    }
    finish_reason = finish_reason_map.get(stop_reason, "stop")

    # Usage translation.
    usage_obj = getattr(msg, "usage", None)
    prompt_tokens = int(getattr(usage_obj, "input_tokens", 0)) if usage_obj is not None else 0
    completion_tokens = int(getattr(usage_obj, "output_tokens", 0)) if usage_obj is not None else 0

    return {
        "id": request_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": content,
                },
                "finish_reason": finish_reason,
                "logprobs": None,
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


def _translate_dict_response(resp: dict[str, Any], model: str, request_id: str) -> dict[str, Any]:
    """Translate an OpenAI-shaped dict (from Ollama/llama.cpp) to normalised shape.

    Ollama and llama.cpp both return OpenAI-compatible JSON. We rebuild the
    dict with a canonical ``id`` and guaranteed ``object`` field so the caller
    doesn't need to handle partial shapes.
    """
    choices = resp.get("choices", [])
    # Ensure choices is a list of dicts with the required structure.
    normalised_choices: list[dict[str, Any]] = []
    for i, choice in enumerate(choices):
        if isinstance(choice, dict):
            normalised_choices.append(
                {
                    "index": choice.get("index", i),
                    "message": choice.get("message", {"role": "assistant", "content": ""}),
                    "finish_reason": choice.get("finish_reason", "stop"),
                    "logprobs": choice.get("logprobs"),
                }
            )

    raw_usage = resp.get("usage") or {}
    prompt_tokens = int(raw_usage.get("prompt_tokens", 0))
    completion_tokens = int(raw_usage.get("completion_tokens", 0))

    return {
        "id": request_id,
        "object": "chat.completion",
        "created": resp.get("created", int(time.time())),
        "model": resp.get("model", model),
        "choices": normalised_choices,
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


def to_openai_response(resp: Any, model: str, request_id: str | None = None) -> dict[str, Any]:
    """Normalise a provider-native response to an OpenAI ChatCompletion dict.

    Parameters
    ----------
    resp:
        Provider-native response. Handled types:

        - ``openai.types.chat.ChatCompletion`` — pass-through (just
          ``.model_dump()`` + id override).
        - ``anthropic.types.Message`` — translated to OpenAI shape.
        - ``dict`` (Ollama/llama.cpp return OpenAI-shaped dicts) — canonical
          rebuild.

    model:
        The model name to embed in the response (used when the provider
        response doesn't carry a reliable model field).
    request_id:
        Optional pre-generated request ID. If ``None``, a new
        ``chatcmpl-<hex>`` ID is generated.

    Returns
    -------
    dict
        A JSON-serialisable OpenAI ChatCompletion dict.
    """
    rid = request_id or _new_request_id()

    # Dicts go through the dict path first.
    if isinstance(resp, dict):
        return _translate_dict_response(resp, model, rid)

    # OpenAI ChatCompletion — pass-through.
    # Detected by: has model_dump() AND has choices attribute AND does NOT have stop_reason.
    # (OpenAI ChatCompletion does not have stop_reason; Anthropic Message does.)
    if hasattr(resp, "model_dump") and hasattr(resp, "choices") and not hasattr(resp, "stop_reason"):
        dumped: dict[str, Any] = resp.model_dump()
        dumped["id"] = rid  # override with our canonical id
        return dumped

    # Anthropic Message — translate.
    # Detected by: has stop_reason AND has content (could be list of content blocks).
    if hasattr(resp, "stop_reason") and hasattr(resp, "content"):
        return _translate_anthropic_message(resp, model, rid)

    # Fallback: best-effort extraction for unknown shapes.
    # This should not happen in normal operation but prevents a hard 500.
    return {
        "id": rid,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": str(resp)},
                "finish_reason": "stop",
                "logprobs": None,
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def _translate_anthropic_chunk(event: Any, model: str, request_id: str) -> dict[str, Any] | None:
    """Translate a single Anthropic stream event to an OpenAI chunk dict.

    Anthropic streaming events (``RawStreamEvent``) shapes we care about:

    - ``RawContentBlockDeltaEvent`` (type=``"content_block_delta"``,
      delta.type=``"text_delta"``, delta.text=``<str>``)
    - ``RawMessageStartEvent``  (type=``"message_start"``)
    - ``RawMessageStopEvent``   (type=``"message_stop"``)
    - ``RawMessageDeltaEvent``  (type=``"message_delta"``,
      delta.stop_reason available)

    Returns ``None`` for events that should not emit a chunk (e.g.
    ping / content_block_start etc).
    """
    event_type = getattr(event, "type", None)

    if event_type == "content_block_delta":
        delta = getattr(event, "delta", None)
        if delta is None:
            return None
        delta_type = getattr(delta, "type", None)
        if delta_type == "text_delta":
            text = getattr(delta, "text", "")
            return {
                "id": request_id,
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant", "content": text},
                        "finish_reason": None,
                        "logprobs": None,
                    }
                ],
            }

    if event_type == "message_delta":
        delta = getattr(event, "delta", None)
        stop_reason = getattr(delta, "stop_reason", None) if delta is not None else None
        finish_reason_map: dict[str | None, str | None] = {
            "end_turn": "stop",
            "max_tokens": "length",
            "stop_sequence": "stop",
            "tool_use": "tool_calls",
            None: None,
        }
        finish_reason = finish_reason_map.get(stop_reason, "stop")
        if finish_reason is not None:
            return {
                "id": request_id,
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {},
                        "finish_reason": finish_reason,
                        "logprobs": None,
                    }
                ],
            }

    return None


def to_openai_chunk(event: Any, model: str, request_id: str) -> dict[str, Any] | None:
    """Normalise a provider-native stream event to an OpenAI chunk dict.

    Parameters
    ----------
    event:
        A single provider-native streaming event. Handled:

        - OpenAI ``ChatCompletionChunk`` — pass-through via ``model_dump()``.
        - Anthropic ``RawStreamEvent`` subclass — translated.
        - ``dict`` — treated as an already-OpenAI-shaped chunk.

    model:
        Model name to embed (used when the event lacks it).
    request_id:
        The request ID to embed in the chunk.

    Returns
    -------
    dict or None
        The OpenAI-shaped chunk dict, or ``None`` if this event should not
        produce a chunk (e.g. Anthropic bookkeeping events).
    """
    type_name = type(event).__name__

    # OpenAI ChatCompletionChunk — pass-through.
    if type_name == "ChatCompletionChunk" and hasattr(event, "model_dump"):
        dumped: dict[str, Any] = event.model_dump()
        dumped["id"] = request_id
        return dumped

    # Anthropic RawStreamEvent — translate.
    if hasattr(event, "type") and not isinstance(event, dict):
        return _translate_anthropic_chunk(event, model, request_id)

    # Dict — already OpenAI-shaped (Ollama/llama.cpp).
    if isinstance(event, dict):
        event["id"] = request_id
        return event

    return None


__all__ = ["to_openai_chunk", "to_openai_response"]
