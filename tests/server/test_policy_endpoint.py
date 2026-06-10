"""Tests for GET /v1/policy and GET /v1/decision/{id} (OGE-583).

AC:
- /v1/policy returns the loaded policy shape when a policy is loaded.
- /v1/policy returns 404 when no policy is loaded.
- /v1/decision/{id} always returns the "no audit in v0.1" message (no 500).
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from ogentic_router.server.app import create_app


class TestPolicyEndpoint:
    """GET /v1/policy tests."""

    def test_policy_no_config_returns_404(self, test_client: TestClient) -> None:
        """AC: /v1/policy returns 404 when no policy is loaded (config.policy_path=/dev/null)."""
        # The test_client fixture uses policy_path="/dev/null" which won't load.
        resp = test_client.get("/v1/policy")
        assert resp.status_code == 404

    def test_policy_no_config_app_returns_404(self) -> None:
        """AC: /v1/policy returns 404 when no config is loaded at all."""
        app = create_app()  # no config
        with TestClient(app) as client:
            resp = client.get("/v1/policy")
        assert resp.status_code == 404


class TestDecisionEndpoint:
    """GET /v1/decision/{id} tests."""

    def test_decision_returns_no_audit_message(self, test_client: TestClient) -> None:
        """AC: /v1/decision/{id} returns the v0.1 stub message, not 500."""
        resp = test_client.get("/v1/decision/chatcmpl-abc123")
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == "chatcmpl-abc123"
        assert "not_found" in body["status"] or "audit" in body["detail"].lower()

    def test_decision_arbitrary_id_no_error(self, test_client: TestClient) -> None:
        """AC: Any decision ID works — no KeyError / 500."""
        resp = test_client.get("/v1/decision/some-completely-random-id-xyz")
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == "some-completely-random-id-xyz"
