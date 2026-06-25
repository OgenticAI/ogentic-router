"""Pre-flight cost estimation for LLM calls (OGE-1061).

Provides :func:`estimate_cost` — a prompt-only, pre-flight USD cost estimate
used by :meth:`~ogentic_router.Router.route` to enforce ``budget_ceiling``
before any network call leaves the device.

Design notes:

* **Input-only estimate.** We have not yet sent the call, so output tokens are
  unknown. The estimate uses prompt-token count only — this is deliberately
  conservative and clearly documented as such.
* **Token approximation.** Character count ÷ 4 is the widely-used English
  average (GPT-3 paper, tiktoken rough baseline). Exact tokenisation would
  require model-specific tokenisers as a dependency — not worth the weight for
  a fail-fast gate.
* **Built-in pricing table.** A module-level dict keyed by lowercase canonical
  model name. Prefix matching handles version suffixes
  (``"claude-3-opus-20240229"`` matches ``"claude-3-opus"``). Unknown models
  fall back to a conservative $10 / M tokens rather than silently passing.
* **No external config files.** Prices are baked in so the check is always
  available without any setup. Users who need bespoke prices can override
  ``estimate_cost`` (it is a plain function, not a method) or pass
  ``budget_ceiling=None`` to opt out.
"""

from __future__ import annotations

# USD per 1 million input tokens, keyed by lowercase canonical model name.
# Covers the models most likely to trigger a fat-finger budget issue.
_INPUT_PRICE_PER_M: dict[str, float] = {
    # ── Anthropic Claude ──────────────────────────────────────────────────
    "claude-opus-4": 15.0,
    "claude-opus-4-5": 15.0,
    "opus-4": 15.0,
    "claude-3-opus": 15.0,
    "claude-sonnet-4": 3.0,
    "claude-sonnet-4-6": 3.0,
    "claude-3-5-sonnet": 3.0,
    "claude-3-sonnet": 3.0,
    "claude-haiku-4-5": 0.8,
    "claude-haiku-4": 0.8,
    "claude-3-haiku": 0.25,
    "claude-3-5-haiku": 0.8,
    # ── OpenAI GPT / O-series ────────────────────────────────────────────
    "gpt-4o": 2.5,
    "gpt-4-turbo": 10.0,
    "gpt-4": 30.0,
    "gpt-3.5-turbo": 0.5,
    "o1": 15.0,
    "o1-mini": 3.0,
    "o3": 10.0,
    "o3-mini": 1.1,
    "o4-mini": 1.1,
    # ── Google Gemini ────────────────────────────────────────────────────
    "gemini-2.0-flash": 0.1,
    "gemini-2.5-pro": 1.25,
    "gemini-1.5-pro": 1.25,
    "gemini-1.5-flash": 0.075,
    # ── Meta Llama (hosted) ───────────────────────────────────────────────
    "llama-3.1-405b": 5.0,
    "llama-3.1-70b": 0.9,
    "llama-3.1-8b": 0.2,
}

# Fallback for any model not in the table — conservative so the ceiling
# check errs on the side of refusal rather than silently passing.
_DEFAULT_PRICE_PER_M: float = 10.0


def _price_per_m(model: str) -> float:
    """Return USD / 1 M input tokens for *model*.

    Tries exact match (case-insensitive) first, then longest-prefix match
    (handles version suffixes like ``"claude-3-opus-20240229"``), then
    falls back to :data:`_DEFAULT_PRICE_PER_M`.
    """
    key = model.lower().strip()
    if key in _INPUT_PRICE_PER_M:
        return _INPUT_PRICE_PER_M[key]
    matches = [(k, v) for k, v in _INPUT_PRICE_PER_M.items() if key.startswith(k)]
    if matches:
        return max(matches, key=lambda kv: len(kv[0]))[1]
    return _DEFAULT_PRICE_PER_M


def estimate_cost(model: str, prompt: str) -> float:
    """Estimate the USD cost of sending *prompt* to *model*.

    Uses a character-based token approximation (approximately 4 characters per
    token for English text) and a per-model input-token price from the
    :data:`_INPUT_PRICE_PER_M` table.  Output tokens are not counted because
    the call has not been sent yet — this is a **prompt-only, pre-flight
    estimate** intended for fail-fast enforcement via :func:`budget_ceiling`.

    Args:
        model: Model identifier, e.g. ``"gpt-4-turbo"`` or ``"opus-4"``.
            Case-insensitive. Version suffixes are handled by prefix matching.
        prompt: The full prompt text to be sent.

    Returns:
        Estimated USD cost as a :class:`float`.  Always ``>= 0``.
    """
    token_count = max(1.0, len(prompt) / 4.0)
    price_per_m = _price_per_m(model)
    return (token_count / 1_000_000.0) * price_per_m


__all__ = ["estimate_cost"]
