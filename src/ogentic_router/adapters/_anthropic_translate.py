"""Translate OpenAI-shaped messages into Anthropic-shaped Messages API input.

The single bit of shape-mapping that has to be right in v0.1: OpenAI inlines
``role: system`` into the messages list; Anthropic's Messages API takes
``system=`` as a *separate* top-level parameter. The router accepts the
OpenAI-shaped payload everywhere (so policies / Shield / the server are
provider-agnostic) and the adapter does the translation on the way out.

If the caller passes multiple ``role: system`` messages, they're joined with
``"\\n\\n"`` and the joined string becomes the Anthropic ``system=``. Tests
in ``tests/adapters/test_anthropic_adapter.py`` lock this contract.
"""

from __future__ import annotations

from typing import Any

_SYSTEM_JOIN = "\n\n"


def _stringify(content: Any) -> str:
    """Best-effort coerce a message content value to a string.

    OpenAI permits ``content`` to be either a string or a list of content
    parts. For the system-message extraction path we only need a single
    plain string. Non-string content is repr'd so the caller sees the shape
    and can flatten upstream if needed.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        # Flatten OpenAI content-part lists; only ``type: text`` parts carry
        # textual system content. Anything exotic (image, audio) in a system
        # message would be ill-formed for both providers.
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                text = part.get("text", "")
                if isinstance(text, str):
                    parts.append(text)
        return _SYSTEM_JOIN.join(parts)
    # Last resort: stringify so the caller at least sees something on the wire
    return str(content)


def extract_system_and_messages(
    messages: list[dict[str, Any]],
) -> tuple[str | None, list[dict[str, Any]]]:
    """Split an OpenAI-shaped messages list into (system, non-system).

    Returns a 2-tuple:

    - ``system``: the joined system prompt, or ``None`` if the input had no
      ``role: system`` entries. Multiple system messages are joined with
      ``"\\n\\n"`` in the order they appeared.
    - ``messages_without_system``: the original list with all ``role: system``
      entries removed, preserving order of the surviving messages.
    """
    system_parts: list[str] = []
    rest: list[dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role")
        if role == "system":
            system_parts.append(_stringify(msg.get("content", "")))
        else:
            rest.append(msg)

    if not system_parts:
        return None, rest
    return _SYSTEM_JOIN.join(system_parts), rest
