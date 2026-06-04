"""ogentic-router: Privacy-aware LLM routing.

Sensitive content stays local; redacted content may go to cloud.
Pairs with ogentic-shield (classification) and ogentic-audit (evidence).

v0.1 is in flight — see the Linear project:
https://linear.app/ogenticai/project/ogentic-router-oss-46e612b52d27
"""

from __future__ import annotations

from ogentic_router.classification import ShieldClassification
from ogentic_router.errors import RouterError, ShieldUnavailableError
from ogentic_router.policy import Policy, PolicyError, RouteDecision, Transform
from ogentic_router.router import Router

__version__ = "0.0.1.dev0"

__all__ = [
    "Policy",
    "PolicyError",
    "RouteDecision",
    "Router",
    "RouterError",
    "ShieldClassification",
    "ShieldUnavailableError",
    "Transform",
    "__version__",
]
