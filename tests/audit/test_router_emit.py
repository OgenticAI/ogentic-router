"""Router-level audit emission tests (OGE-584)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from ogentic_router import BudgetCeilingExceeded, Policy, Router
from ogentic_router.audit import LocalFileSink, NoopSink, RouteDecisionAudit
from ogentic_router.audit._request_id import compute_request_id, resolve_salt

CANONICAL_POLICY = Path(__file__).parent.parent.parent / "examples" / "policy.yaml"


def _stub_shield(*, score: int = 0, groups: list[str] | None = None) -> Any:
    def analyze(_text: str) -> Any:
        return SimpleNamespace(
            score=score,
            category_groups_found=set(groups or []),
            entities=[],
            top_category="LEGAL_PRIVILEGE" if groups else None,
            text_hash="sha256:deadbeefdeadbeef",
            entity_count=0,
            profile_ids=["shield-legal"],
        )

    return SimpleNamespace(analyze=analyze)


class _CapturingSink:
    def __init__(self) -> None:
        self.rows: list[RouteDecisionAudit] = []

    def emit(self, row: RouteDecisionAudit) -> None:
        self.rows.append(row)


def _router(sink: Any, shield: Any) -> Router:
    return Router(Policy.from_yaml(CANONICAL_POLICY), shield=shield, audit_sink=sink)


def test_default_sink_is_noop_and_writes_nothing(tmp_path: Path) -> None:
    router = Router(Policy.from_yaml(CANONICAL_POLICY), shield=_stub_shield())
    assert isinstance(router._audit_sink, NoopSink)
    router.route("hello")  # no file to write anywhere; must not raise


def test_one_row_per_route_success(tmp_path: Path) -> None:
    sink = _CapturingSink()
    router = _router(sink, _stub_shield(groups=["PRIVILEGE"]))
    router.route("privileged memo")
    assert len(sink.rows) == 1
    row = sink.rows[0]
    assert row.route_decision == "ollama-local"
    assert row.rule_id == "privilege-stays-local"
    assert row.groups_found == ["PRIVILEGE"]
    assert row.top_category == "LEGAL_PRIVILEGE"
    assert row.profile == "shield-legal"
    assert row.backend_is_local is True
    assert row.error is None
    assert row.latency_ms >= 0.0
    assert row.prompt_hash.startswith("sha256:")


def test_row_emitted_on_error_path_with_class_name_only() -> None:
    sink = _CapturingSink()

    def _boom(_text: str) -> Any:
        raise RuntimeError("secret detail")

    router = _router(sink, SimpleNamespace(analyze=_boom))
    with pytest.raises(RuntimeError):
        router.route("anything")
    assert len(sink.rows) == 1
    assert sink.rows[0].error == "RuntimeError"
    assert sink.rows[0].route_decision is None
    assert sink.rows[0].sensitivity_score is None


def test_row_emitted_on_budget_ceiling_refusal() -> None:
    sink = _CapturingSink()
    router = _router(sink, _stub_shield())
    with pytest.raises(BudgetCeilingExceeded):
        router.route("hello" * 50, model="gpt-4o-mini", budget_ceiling=0.0)
    assert len(sink.rows) == 1
    row = sink.rows[0]
    assert row.error == "BudgetCeilingExceeded"
    # Refused before classification — but still shape-only, with a fingerprint.
    assert row.prompt_hash.startswith("sha256:")
    assert row.sensitivity_score is None


def test_backend_is_local_cloud_route() -> None:
    sink = _CapturingSink()
    router = _router(sink, _stub_shield(score=5))
    router.route("what's the weather")
    assert sink.rows[0].route_decision == "openai-cloud"
    assert sink.rows[0].backend_is_local is False


def test_local_backends_override_makes_locality_exact() -> None:
    sink = _CapturingSink()
    # Declare that 'openai-cloud' is actually local (contrived) to prove the
    # explicit set wins over the naming heuristic.
    router = Router(
        Policy.from_yaml(CANONICAL_POLICY),
        shield=_stub_shield(score=5),
        audit_sink=sink,
        local_backends=frozenset({"openai-cloud"}),
    )
    router.route("hi")
    assert sink.rows[0].backend_is_local is True


def test_transform_recorded_on_redact_rule() -> None:
    sink = _CapturingSink()
    router = _router(sink, _stub_shield(score=45))
    router.route("mid-sensitivity text")
    assert sink.rows[0].route_decision == "openai-cloud"
    assert sink.rows[0].transform == "shield_redact"


def test_localfilesink_end_to_end_one_line(tmp_path: Path) -> None:
    log = tmp_path / "audit.jsonl"
    router = _router(LocalFileSink(log), _stub_shield(groups=["PHI"]))
    router.route("patient chart")
    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert set(row) == {
        "ts", "request_id", "prompt_hash", "sensitivity_score", "profile",
        "top_category", "groups_found", "route_decision", "rule_id",
        "transform", "backend_is_local", "latency_ms", "error",
    }


def test_from_config_builds_local_file_sink(tmp_path: Path) -> None:
    log = tmp_path / "audit.jsonl"
    policy = tmp_path / "policy.yaml"
    policy.write_text(CANONICAL_POLICY.read_text(), encoding="utf-8")
    router = Router.from_config(
        {"policy_path": str(policy), "audit": {"sink": "local_file", "path": str(log)}}
    )
    assert isinstance(router._audit_sink, LocalFileSink)


def test_hmac_request_id_is_deterministic_with_fixed_salt() -> None:
    salt = b"fixed-salt"
    a = compute_request_id(salt, "2026-06-04T17:00:00Z", "sha256:abc")
    b = compute_request_id(salt, "2026-06-04T17:00:00Z", "sha256:abc")
    assert a == b
    # Different inputs → different id.
    assert a != compute_request_id(salt, "2026-06-04T17:00:01Z", "sha256:abc")
    assert a != compute_request_id(b"other-salt", "2026-06-04T17:00:00Z", "sha256:abc")


def test_resolve_salt_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OGENTIC_ROUTER_AUDIT_SALT", "hunter2")
    assert resolve_salt() == b"hunter2"


def test_resolve_salt_warns_and_falls_back(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.delenv("OGENTIC_ROUTER_AUDIT_SALT", raising=False)
    with caplog.at_level("WARNING"):
        salt = resolve_salt()
    assert isinstance(salt, bytes) and len(salt) == 32
    assert any("audit salt not set" in r.message for r in caplog.records)
