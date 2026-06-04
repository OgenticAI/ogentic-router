"""Host allow-list enforcement for cloud adapters.

The flagship threat model the allow-list addresses: a deployer who types
``base_url="https://api.openai.com.evil.invalid"`` (or who got phished into
swapping the env var) silently exfiltrates every prompt to the attacker.
The library refuses to construct an adapter whose ``base_url`` host is not
on the allow-list, surfacing the typo at startup instead of at the audit
log review.

The allow-list defaults to the official provider host. Deployers who run
through a forward proxy or in-house gateway opt in via the env var per kind:

- ``OGENTIC_ROUTER_ALLOWED_OPENAI_HOSTS``
- ``OGENTIC_ROUTER_ALLOWED_ANTHROPIC_HOSTS``

Both are comma-separated host names (no scheme, no port). The env-var hosts
**extend** the defaults — they don't replace them — so the official endpoint
remains reachable even when a proxy is added.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

from ogentic_router.adapters.base import AdapterConfigError

ALLOWED_OPENAI_HOSTS: frozenset[str] = frozenset({"api.openai.com"})
ALLOWED_ANTHROPIC_HOSTS: frozenset[str] = frozenset({"api.anthropic.com"})

_ENV_VAR_BY_KIND: dict[str, str] = {
    "OPENAI": "OGENTIC_ROUTER_ALLOWED_OPENAI_HOSTS",
    "ANTHROPIC": "OGENTIC_ROUTER_ALLOWED_ANTHROPIC_HOSTS",
}


def _parse_env_hosts(env_var: str) -> frozenset[str]:
    """Parse a comma-separated env var value into a host set.

    Empty / unset env var yields an empty frozenset. Whitespace around hosts
    is trimmed; empty entries (from stray commas) are dropped.
    """
    raw = os.environ.get(env_var, "")
    if not raw:
        return frozenset()
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


def _effective_allowlist(defaults: frozenset[str], kind: str) -> frozenset[str]:
    """Combine the compiled-in defaults with the env-var extension."""
    env_var = _ENV_VAR_BY_KIND.get(kind)
    if env_var is None:
        return defaults
    return defaults | _parse_env_hosts(env_var)


def _validate_host(url: str | None, allowed: frozenset[str], kind: str) -> None:
    """Validate that ``url``'s host is on the allow-list.

    Parameters
    ----------
    url:
        The ``base_url`` the caller passed to the adapter constructor.
        When ``None``, the SDK uses its built-in default endpoint, which is
        always on the allow-list — short-circuit and accept.
    allowed:
        Compiled-in defaults (e.g. ``ALLOWED_OPENAI_HOSTS``).
    kind:
        ``"OPENAI"`` or ``"ANTHROPIC"`` — selects the env-var override key.

    Raises
    ------
    AdapterConfigError
        When the host is not on the allow-list. The message tells the deployer
        which env var to set if they really mean it.
    """
    if url is None:
        return

    parsed = urlparse(url)
    host = parsed.hostname
    if not host:
        raise AdapterConfigError(
            f"base_url {url!r} could not be parsed as a URL with a host. "
            f"Pass a full URL like 'https://api.{kind.lower()}.com'."
        )

    effective = _effective_allowlist(allowed, kind)
    if host not in effective:
        env_var = _ENV_VAR_BY_KIND.get(kind, "<unknown>")
        raise AdapterConfigError(
            f"base_url host {host!r} is not in ALLOWED_{kind}_HOSTS "
            f"(effective: {sorted(effective)!r}). Override with the "
            f"{env_var} env var (comma-separated) if you really mean it."
        )
