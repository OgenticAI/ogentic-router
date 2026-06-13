"""ogentic-router: Privacy-aware LLM routing.

Sensitive content stays local; redacted content may go to cloud.
Pairs with ogentic-shield (classification) and ogentic-audit (evidence).

v0.1 ships the Wave-2 baseline: Policy DSL + Router class + four
adapters (OpenAI, Anthropic, Ollama, llama.cpp). See CHANGELOG.md
and the project README for details.
"""

from __future__ import annotations

from ogentic_router.classification import ShieldClassification
from ogentic_router.errors import RouterError, ShieldUnavailableError
from ogentic_router.policy import Policy, PolicyError, RouteDecision, Transform
from ogentic_router.router import Router

__version__ = "0.1.0"

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

from ogentic_router import adapters  # noqa: E402,F401  (re-export submodule)
