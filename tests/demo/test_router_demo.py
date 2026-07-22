"""Tests for the demo core logic (OGE-1578).

Streamlit is not imported here — the core (`demo/router_demo.py`) is deliberately
UI-free so it can be tested with a stubbed Shield (no Presidio cold-start). The
`app.py` Streamlit layer is a thin renderer over this.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import yaml

from demo import router_demo
from ogentic_router import Policy, Router


def _stub_shield(*, score: int, groups: list[str] | None = None, entities: int = 0) -> Any:
    def analyze(_text: str) -> Any:
        return SimpleNamespace(
            score=score,
            category_groups_found=set(groups or []),
            entities=[SimpleNamespace(category="X") for _ in range(entities)],
            top_category=(groups or [None])[0],
            text_hash="sha256:deadbeefdeadbeef",
            entity_count=entities,
            profile_ids=["shield-legal"],
        )

    return SimpleNamespace(analyze=analyze)


def _router_with(shield: Any) -> Router:
    """Build a demo Router with the real demo config but an injected Shield."""
    cfg = yaml.safe_load(router_demo.CONFIG_PATH.read_text())
    policy = Policy.from_yaml(router_demo.DEMO_DIR / cfg["policy_path"])
    backends = [
        {"backend_id": b["id"], "is_local": b["kind"] in {"ollama", "llamacpp"},
         "default_model": b.get("default_model")}
        for b in cfg["backends"]
    ]
    return Router(policy, shield=shield, backends=backends)


# ── Static assets are coherent ───────────────────────────────────────────────


def test_demo_config_and_policy_load() -> None:
    # The demo router.yaml + policy.yaml must parse and validate.
    cfg = yaml.safe_load(router_demo.CONFIG_PATH.read_text())
    Policy.from_yaml(router_demo.DEMO_DIR / cfg["policy_path"])  # raises on invalid


def test_backends_metadata_matches_config() -> None:
    cfg = yaml.safe_load(router_demo.CONFIG_PATH.read_text())
    config_ids = {b["id"] for b in cfg["backends"]}
    assert set(router_demo.BACKENDS) == config_ids  # every backend has display metadata


def test_all_four_adapter_kinds_present() -> None:
    cfg = yaml.safe_load(router_demo.CONFIG_PATH.read_text())
    kinds = {b["kind"] for b in cfg["backends"]}
    assert kinds == {"openai", "anthropic", "ollama", "llamacpp"}


def test_five_samples() -> None:
    assert len(router_demo.SAMPLES) == 5


# ── Routing outcomes (with stubbed Shield) ───────────────────────────────────


def test_privilege_routes_local_llamacpp() -> None:
    res = router_demo.route_prompt(_router_with(_stub_shield(score=90, groups=["PRIVILEGE"])), "x")
    assert res.backend_id == "llamacpp-local"
    assert res.stayed_local is True
    assert res.backend_location == "on-device"


def test_phi_routes_local_ollama() -> None:
    res = router_demo.route_prompt(_router_with(_stub_shield(score=80, groups=["PHI"])), "x")
    assert res.backend_id == "ollama-local"
    assert res.stayed_local is True


def test_mnpi_routes_local() -> None:
    res = router_demo.route_prompt(_router_with(_stub_shield(score=95, groups=["MNPI"])), "x")
    assert res.is_local is True


def test_medium_pii_redacts_to_cloud() -> None:
    res = router_demo.route_prompt(_router_with(_stub_shield(score=28, groups=["PII"])), "x")
    assert res.backend_id == "anthropic-cloud"
    assert res.stayed_local is False
    assert res.transform == "shield_redact"


def test_low_sensitivity_goes_cloud_clear() -> None:
    res = router_demo.route_prompt(_router_with(_stub_shield(score=0)), "x")
    assert res.backend_id == "openai-cloud"
    assert res.stayed_local is False
    assert res.transform is None


# ── Result shape / privacy ───────────────────────────────────────────────────


def test_result_is_shape_only() -> None:
    secret = "4111-1111-1111-1111"
    res = router_demo.route_prompt(_router_with(_stub_shield(score=0)), f"card {secret}")
    blob = str(res.to_dict())
    assert secret not in blob
    assert res.prompt_hash.startswith("sha256:")


def test_result_carries_reasoning_and_score() -> None:
    res = router_demo.route_prompt(_router_with(_stub_shield(score=90, groups=["PRIVILEGE"])), "x")
    assert res.score == 90
    assert res.groups == ["PRIVILEGE"]
    assert "privilege-to-llamacpp" in (res.rule_id or "")
    assert res.reasoning
