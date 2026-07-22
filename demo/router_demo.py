"""Core logic for the ogentic-router demo (OGE-1578).

Streamlit-free so it can be unit-tested and reused. Builds a Router from the
demo config, classifies a prompt with Shield, and returns the routing *decision*
— which backend the content is allowed to go to, and why.

The demo shows the **decision, not a completion**: no LLM is called, nothing
leaves the machine. That is the point — the privacy-relevant choice is made and
explainable before a single byte is dispatched.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ogentic_router import Router

DEMO_DIR = Path(__file__).parent
CONFIG_PATH = DEMO_DIR / "router.yaml"

# Per-backend display metadata, keyed by the backend id used in policy.yaml.
BACKENDS: dict[str, dict[str, str]] = {
    "llamacpp-local": {"kind": "llama.cpp", "location": "on-device", "icon": "🔒"},
    "ollama-local": {"kind": "Ollama", "location": "on-device", "icon": "🔒"},
    "openai-cloud": {"kind": "OpenAI", "location": "cloud", "icon": "☁️"},
    "anthropic-cloud": {"kind": "Anthropic", "location": "cloud", "icon": "☁️"},
}

# Sample prompts, each chosen to land on a different rule. (label, prompt)
SAMPLES: list[tuple[str, str]] = [
    ("Attorney–client privilege → llama.cpp (local)",
     "Privileged and confidential attorney work product: our litigation strategy "
     "against Acme, prepared by counsel in anticipation of trial."),
    ("Patient record / PHI → Ollama (local)",
     "Patient John A. Doe, diagnosed with Type 2 diabetes; prescribed metformin — "
     "note the change in this session's clinical record."),
    ("Insider / MNPI → Ollama (local)",
     "Material non-public information, internal use only, do not distribute: our "
     "acquisition of Beta Corp closes Friday at a $2.4 billion valuation; do not "
     "trade before the public announcement."),
    ("Personal reminder → Anthropic, redacted (cloud)",
     "Draft a friendly reminder to Sarah Miller at sarah.miller@example.com and "
     "call her at 415-555-0142 about the team offsite next month."),
    ("General question → OpenAI (cloud)",
     "What's a good three-day itinerary for visiting Lisbon in the spring?"),
]


@dataclass(frozen=True)
class DemoResult:
    """Everything the UI needs to render one routed prompt."""

    prompt: str
    score: int
    groups: list[str]
    top_category: str | None
    entity_count: int
    prompt_hash: str
    backend_id: str
    backend_kind: str
    backend_location: str  # "on-device" | "cloud" | "unknown"
    backend_icon: str
    is_local: bool | None
    rule_id: str | None
    transform: str | None
    reasoning: str

    @property
    def stayed_local(self) -> bool:
        return self.is_local is True

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "groups": self.groups,
            "top_category": self.top_category,
            "backend_id": self.backend_id,
            "backend_kind": self.backend_kind,
            "backend_location": self.backend_location,
            "is_local": self.is_local,
            "rule_id": self.rule_id,
            "transform": self.transform,
            "reasoning": self.reasoning,
            "prompt_hash": self.prompt_hash,
        }


def build_router() -> Router:
    """Build the demo Router (loads Shield once; cache this in the UI)."""
    return Router.from_yaml(CONFIG_PATH)


def route_prompt(router: Router, prompt: str) -> DemoResult:
    """Classify + route ``prompt`` and package the result for display.

    Budget enforcement is disabled here (``budget_ceiling=None``) so the demo
    always shows a routing decision rather than a cost refusal — the demo is
    about *where* content goes, not cost.
    """
    classification = router.classify(prompt)
    decision = router.route(prompt, budget_ceiling=None)

    meta = BACKENDS.get(decision.backend_id, {})
    is_local = _is_local(router, decision.backend_id)
    location = meta.get("location") or (
        "on-device" if is_local else "cloud" if is_local is False else "unknown"
    )
    return DemoResult(
        prompt=prompt,
        score=classification.score,
        groups=sorted(classification.category_groups_found),
        top_category=classification.top_category,
        entity_count=classification.entity_count,
        prompt_hash=classification.text_hash,
        backend_id=decision.backend_id,
        backend_kind=meta.get("kind", decision.backend_id),
        backend_location=location,
        backend_icon=meta.get("icon", "•"),
        is_local=is_local,
        rule_id=decision.rule_id,
        transform=decision.transform.value if decision.transform else None,
        reasoning=decision.reasoning,
    )


def _is_local(router: Router, backend_id: str) -> bool | None:
    """Locality from the router's declared backends (authoritative)."""
    for b in router.backends:
        if b.get("backend_id") == backend_id:
            return bool(b.get("is_local"))
    return None
