"""Loopback-only endpoint enforcement for local adapters.

Verbatim port of ``ogentic-shield/src/ogentic_shield/layers/llm_client.py``
lines 40-58. Shield's audit log already documents Layer-3 as
"contractually localhost-only"; the Router's privacy story points at the
same enforcement, so the two implementations must not drift.

Accepted hosts are the IPv4 + IPv6 loopback set (any port). Anything else —
including private CIDRs like ``10.x``, ``192.168.x``, or internal DNS
(``ollama.internal``) — raises :class:`LocalhostOnlyError` at construction.

This module deliberately stays free of httpx / pydantic / anything beyond the
stdlib so a misconfigured constructor fails before the optional ``[local]``
extra is even imported.
"""

from __future__ import annotations

from urllib.parse import urlparse

from ogentic_router.adapters.base import AdapterError

#: Hosts accepted by :func:`_validate_localhost`. Bracketed IPv6 covers the
#: common ``http://[::1]:8080`` form even after ``urlparse.hostname`` strips
#: the brackets — we check both ``::1`` and ``[::1]`` to be defensive against
#: any URL parser variation.
LOOPBACK_HOSTS: frozenset[str] = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})


class LocalhostOnlyError(AdapterError):
    """Raised when a local-adapter endpoint resolves to anything other than loopback.

    Subclass of :class:`AdapterError` so callers can ``except AdapterError`` to
    catch every adapter failure mode (matches the OGE-581 error hierarchy).
    """


def _validate_localhost(endpoint: str, *, kind: str) -> None:
    """Validate that ``endpoint`` uses http(s) and a loopback host.

    Parameters
    ----------
    endpoint:
        Full URL including scheme and host (e.g. ``"http://localhost:11434"``).
    kind:
        Display name interpolated into the error message (``"Ollama"`` /
        ``"llama.cpp"``). Lets the two adapters share one validator while
        producing adapter-specific errors.

    Raises
    ------
    LocalhostOnlyError
        If the scheme is not ``http``/``https``, or if the host is not in
        :data:`LOOPBACK_HOSTS`. Two-tier message structure (one for scheme,
        one for host) mirrors Shield's original implementation.
    """
    parsed = urlparse(endpoint)
    if parsed.scheme not in {"http", "https"}:
        raise LocalhostOnlyError(
            f"{kind} endpoint must use http(s); got '{parsed.scheme or '(missing)'}' in '{endpoint}'."
        )
    host = (parsed.hostname or "").lower()
    if host not in LOOPBACK_HOSTS:
        raise LocalhostOnlyError(
            f"{kind} endpoint must be loopback (got host='{host}'). "
            f"Accepted hosts: localhost, 127.0.0.1, ::1, [::1]. "
            f"If you want a non-localhost LLM, use a cloud adapter — this isn't one."
        )
