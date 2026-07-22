"""Fail-closed deny-cloud enforcement (OGE-1135).

The core privacy guarantee: content Shield flags as PRIVILEGE / PHI / MNPI can
never resolve to a cloud backend — even if a policy is misconfigured or its rules
are mis-ordered. Enforcement is ON by default; the router raises
``CloudRouteDeniedError`` before any dispatch.

Shield profiles map to groups: legal → PRIVILEGE, healthcare → PHI,
finance → MNPI. There's a test per profile.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from ogentic_router import CloudRouteDeniedError, Policy, Router
from ogentic_router.audit import RouteDecisionAudit

CANONICAL_POLICY = Path(__file__).parent.parent / "examples" / "policy.yaml"


def _stub_shield(*, score: int, groups: list[str] | None = None) -> Any:
    def analyze(_text: str) -> Any:
        return SimpleNamespace(
            score=score,
            category_groups_found=set(groups or []),
            entities=[],
            top_category=None,
            text_hash="sha256:deadbeefdeadbeef",
            entity_count=0,
            profile_ids=["shield-legal"],
        )

    return SimpleNamespace(analyze=analyze)


CLOUD_BACKENDS = [{"backend_id": "openai-cloud", "is_local": False, "default_model": "gpt-4o-mini"}]
LOCAL_BACKENDS = [{"backend_id": "ollama-local", "is_local": True, "default_model": "llama3.2:3b"}]


def _leak_policy(group: str) -> Policy:
    """A deliberately misconfigured policy that routes ``group`` to cloud."""
    return Policy.from_dict(
        {
            "version": 1,
            "default_backend": "openai-cloud",
            "rules": [{"id": "leak", "when": {"groups_include": [group]}, "route": "openai-cloud"}],
        }
    )


# ── Defaults ─────────────────────────────────────────────────────────────────


def test_deny_cloud_default_groups() -> None:
    policy = Policy.from_yaml(CANONICAL_POLICY)
    assert policy.denied_groups() == frozenset({"PRIVILEGE", "PHI", "MNPI"})


def test_canonical_policy_routes_regulated_local_no_denial() -> None:
    """The shipped policy already keeps regulated content local — guard never fires."""
    router = Router(
        Policy.from_yaml(CANONICAL_POLICY),
        shield=_stub_shield(score=85, groups=["PRIVILEGE"]),
        backends=LOCAL_BACKENDS,
    )
    decision = router.route("privileged attorney memo", budget_ceiling=None)
    assert decision.backend_id == "ollama-local"


# ── Per-profile fail-closed (legal / healthcare / finance) ───────────────────


@pytest.mark.parametrize(
    ("profile", "group"),
    [("legal", "PRIVILEGE"), ("healthcare", "PHI"), ("finance", "MNPI")],
)
def test_misconfigured_cloud_route_is_denied_per_profile(profile: str, group: str) -> None:
    router = Router(
        _leak_policy(group),
        shield=_stub_shield(score=60, groups=[group]),
        backends=CLOUD_BACKENDS,
    )
    with pytest.raises(CloudRouteDeniedError) as exc:
        router.route(f"{profile} content", budget_ceiling=None)
    assert exc.value.groups == [group]
    assert exc.value.backend_id == "openai-cloud"


def test_default_backend_cloud_leak_is_denied() -> None:
    """A regulated prompt that falls through to a cloud default_backend is denied."""
    policy = Policy.from_dict({"version": 1, "default_backend": "openai-cloud"})  # no rules
    router = Router(policy, shield=_stub_shield(score=90, groups=["MNPI"]), backends=CLOUD_BACKENDS)
    with pytest.raises(CloudRouteDeniedError):
        router.route("material non-public info", budget_ceiling=None)


def test_misordered_rules_still_fail_closed() -> None:
    """Even if a broad cloud rule precedes the local rule, the guard catches it."""
    policy = Policy.from_dict(
        {
            "version": 1,
            "default_backend": "ollama-local",
            "rules": [
                {"id": "everything-cloud", "when": {"sensitivity_score_gte": 0}, "route": "openai-cloud"},
                {"id": "privilege-local", "when": {"groups_include": ["PRIVILEGE"]}, "route": "ollama-local"},
            ],
        }
    )
    router = Router(policy, shield=_stub_shield(score=95, groups=["PRIVILEGE"]), backends=CLOUD_BACKENDS)
    with pytest.raises(CloudRouteDeniedError):
        router.route("privileged", budget_ceiling=None)


def test_redacted_cloud_route_is_still_denied() -> None:
    """deny_cloud beats redaction — regulated content can't go cloud even redacted."""
    policy = Policy.from_dict(
        {
            "version": 1,
            "default_backend": "ollama-local",
            "rules": [
                {"id": "phi-redact-cloud", "when": {"groups_include": ["PHI"]},
                 "route": "openai-cloud", "transform": "shield_redact"},
            ],
        }
    )
    router = Router(policy, shield=_stub_shield(score=50, groups=["PHI"]), backends=CLOUD_BACKENDS)
    with pytest.raises(CloudRouteDeniedError):
        router.route("patient record", budget_ceiling=None)


# ── Not denied: non-regulated content, opt-out, local routing ────────────────


def test_non_regulated_content_routes_cloud_freely() -> None:
    policy = _leak_policy("PHI")  # cloud rule keyed on PHI
    router = Router(policy, shield=_stub_shield(score=5, groups=[]), backends=CLOUD_BACKENDS)
    # No PHI on this prompt → default_backend (openai-cloud) → allowed.
    assert router.route("weather?", budget_ceiling=None).backend_id == "openai-cloud"


def test_opt_out_enforce_false_allows_cloud() -> None:
    policy = Policy.from_dict(
        {
            "version": 1,
            "default_backend": "openai-cloud",
            "deny_cloud": {"enforce": False},
            "rules": [{"id": "leak", "when": {"groups_include": ["PHI"]}, "route": "openai-cloud"}],
        }
    )
    router = Router(policy, shield=_stub_shield(score=50, groups=["PHI"]), backends=CLOUD_BACKENDS)
    assert router.route("x", budget_ceiling=None).backend_id == "openai-cloud"


def test_narrowed_groups_drop_phi() -> None:
    """A deployment can route de-identified PHI to cloud by narrowing the set."""
    policy = Policy.from_dict(
        {
            "version": 1,
            "default_backend": "openai-cloud",
            "deny_cloud": {"groups": ["PRIVILEGE", "MNPI"]},  # PHI dropped
            "rules": [{"id": "phi-cloud", "when": {"groups_include": ["PHI"]}, "route": "openai-cloud"}],
        }
    )
    router = Router(policy, shield=_stub_shield(score=50, groups=["PHI"]), backends=CLOUD_BACKENDS)
    assert router.route("x", budget_ceiling=None).backend_id == "openai-cloud"
    # ...but PRIVILEGE is still denied.
    priv = Router(
        Policy.from_dict(
            {
                "version": 1,
                "default_backend": "openai-cloud",
                "deny_cloud": {"groups": ["PRIVILEGE", "MNPI"]},
                "rules": [{"id": "leak", "when": {"groups_include": ["PRIVILEGE"]}, "route": "openai-cloud"}],
            }
        ),
        shield=_stub_shield(score=80, groups=["PRIVILEGE"]),
        backends=CLOUD_BACKENDS,
    )
    with pytest.raises(CloudRouteDeniedError):
        priv.route("x", budget_ceiling=None)


# ── Locality nuance: no declared backends ────────────────────────────────────


def test_no_backends_known_cloud_name_is_denied() -> None:
    """Even without declared backends, an obviously-cloud backend name is denied."""
    router = Router(_leak_policy("PHI"), shield=_stub_shield(score=50, groups=["PHI"]))
    with pytest.raises(CloudRouteDeniedError):
        router.route("x", budget_ceiling=None)


def test_no_backends_unknown_name_is_allowed_with_warning(caplog: pytest.LogCaptureFixture) -> None:
    """With no declared backends and an unclassifiable name, allow but warn."""
    policy = Policy.from_dict(
        {
            "version": 1,
            "default_backend": "mystery-box",
            "rules": [{"id": "leak", "when": {"groups_include": ["PHI"]}, "route": "mystery-box"}],
        }
    )
    router = Router(policy, shield=_stub_shield(score=50, groups=["PHI"]))
    with caplog.at_level("WARNING"):
        decision = router.route("x", budget_ceiling=None)
    assert decision.backend_id == "mystery-box"
    assert any("cannot confirm backend" in r.message for r in caplog.records)


# ── Audit records the denial ─────────────────────────────────────────────────


def test_denial_emits_audit_row_naming_the_cloud_backend() -> None:
    rows: list[RouteDecisionAudit] = []
    sink = SimpleNamespace(emit=rows.append)
    router = Router(
        _leak_policy("PHI"),
        shield=_stub_shield(score=50, groups=["PHI"]),
        audit_sink=sink,
        backends=CLOUD_BACKENDS,
    )
    with pytest.raises(CloudRouteDeniedError):
        router.route("patient", budget_ceiling=None)
    assert len(rows) == 1
    assert rows[0].error == "CloudRouteDeniedError"
    assert rows[0].route_decision == "openai-cloud"  # the denied backend is recorded
    assert rows[0].backend_is_local is False


# ── Validation ───────────────────────────────────────────────────────────────


def test_unknown_deny_cloud_group_is_rejected() -> None:
    from ogentic_router import PolicyError

    with pytest.raises(PolicyError, match="Unknown category group"):
        Policy.from_dict({"version": 1, "default_backend": "x", "deny_cloud": {"groups": ["NOPE"]}})


def test_deny_cloud_round_trips_through_to_dict() -> None:
    policy = Policy.from_dict(
        {"version": 1, "default_backend": "x", "deny_cloud": {"enforce": False, "groups": ["PHI"]}}
    )
    again = Policy.from_dict(policy.to_dict())
    assert again.to_dict() == policy.to_dict()
    assert again.denied_groups() == frozenset()  # enforce=false → empty
