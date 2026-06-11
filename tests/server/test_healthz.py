"""Tests for GET /healthz (OGE-583).

AC: liveness probe always returns 200 {"status": "ok"} regardless of
whether a config is loaded.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from ogentic_router.server.app import create_app


class TestHealthz:
    """/healthz endpoint tests."""

    def test_healthz_ok(self, test_client: TestClient) -> None:
        """AC: /healthz returns 200 with status=ok."""
        resp = test_client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_healthz_no_config(self) -> None:
        """AC: /healthz works even when no config/adapters are loaded."""
        app = create_app()  # no config, no adapters
        with TestClient(app) as client:
            resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_healthz_method_not_allowed(self, test_client: TestClient) -> None:
        """POST /healthz returns 405."""
        resp = test_client.post("/healthz")
        assert resp.status_code == 405
