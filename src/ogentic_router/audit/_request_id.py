"""HMAC ``request_id`` derivation for audit rows (OGE-584).

``request_id = HMAC-SHA256(salt, ts + prompt_hash)``. The salt comes from the
``OGENTIC_ROUTER_AUDIT_SALT`` environment variable so request ids are
reproducible across restarts within a deployment. If it isn't set, a random
per-process salt is generated once and a WARNING is logged — the demo path keeps
working, but ids won't be reproducible.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets

logger = logging.getLogger("ogentic_router.audit")

_SALT_ENV = "OGENTIC_ROUTER_AUDIT_SALT"


def resolve_salt() -> bytes:
    """Return the audit HMAC salt as bytes.

    Reads ``OGENTIC_ROUTER_AUDIT_SALT`` (UTF-8). If unset, generates a random
    32-byte salt for this process and logs a one-time WARNING — request ids
    won't be reproducible across restarts.
    """
    env = os.environ.get(_SALT_ENV)
    if env:
        return env.encode("utf-8")
    logger.warning(
        "audit salt not set — request_ids won't be reproducible across "
        "restarts. Set %s once per deployment for stable ids.",
        _SALT_ENV,
    )
    return secrets.token_bytes(32)


def compute_request_id(salt: bytes, ts: str, prompt_hash: str) -> str:
    """Return ``HMAC-SHA256(salt, ts + prompt_hash)`` as a hex digest.

    Deterministic in its inputs: identical ``(salt, ts, prompt_hash)`` always
    produce the same id, which is what the UAT determinism check relies on.
    """
    return hmac.new(salt, (ts + prompt_hash).encode("utf-8"), hashlib.sha256).hexdigest()


__all__ = ["compute_request_id", "resolve_salt"]
