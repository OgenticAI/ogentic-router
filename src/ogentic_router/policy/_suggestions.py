"""Internal helper — "did you mean ...?" suggestions for unknown category names.

Pure stdlib (`difflib`). Lives behind a leading underscore because it's an
implementation detail of the validation layer; the public API surface
just sees the resulting error message.
"""

from __future__ import annotations

from collections.abc import Iterable
from difflib import get_close_matches


def suggest(typo: str, valid: Iterable[str], *, cutoff: float = 0.7) -> str | None:
    """Return the closest valid name to ``typo``, or ``None`` if nothing is close.

    Uses :func:`difflib.get_close_matches` with a single-match cap so the error
    message can read naturally as *"did you mean 'PRIVILEGE'?"*. ``cutoff`` is
    intentionally conservative (0.7) — false-positive suggestions on totally
    unrelated input ("INVOICE" → "PRIVILEGE") would actively confuse the
    operator more than no suggestion at all.
    """
    matches = get_close_matches(typo, list(valid), n=1, cutoff=cutoff)
    return matches[0] if matches else None
