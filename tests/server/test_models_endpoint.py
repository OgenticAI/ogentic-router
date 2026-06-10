"""Tests for GET /v1/models (OGE-583).

AC: list models returns OpenAI-shaped model objects derived from router.yaml.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from ogentic_router.server.app import create_app
from ogentic_router.server.config import BackendConfig, RouterConfig, ShieldConfig
from tests.server.conftest import FakeAdapter


class TestModelsEndpoint:
    """GET /v1/models tests."""

    def test_models_returns_list_shape(self, test_client: TestClient) -> None:
        """AC: /v1/models returns {"object": "list", "data": [...]}."""
        resp = test_client.get("/v1/models")
        assert resp.status_code == 200
        body = resp.json()
        assert body["object"] == "list"
        assert isinstance(body["data"], list)

    def test_models_includes_configured_backends(self) -> None:
        """AC: each backend in router.yaml appears as a model entry."""
        config = RouterConfig(
            version=1,
            policy_path="/dev/null",
            shield=ShieldConfig(profiles=[]),
            backends=[
                BackendConfig(id="openai-cloud", kind="openai", default_model="gpt-4o-mini"),
                BackendConfig(id="ollama-local", kind="ollama", default_model="llama3"),
            ],
        )
        app = create_app(
            config=config,
            adapters={
                "openai-cloud": FakeAdapter(backend_id="openai-cloud"),
                "ollama-local": FakeAdapter(backend_id="ollama-local"),
            },
        )
        with TestClient(app) as client:
            resp = client.get("/v1/models")
        assert resp.status_code == 200
        model_ids = [m["id"] for m in resp.json()["data"]]
        assert "gpt-4o-mini" in model_ids
        assert "llama3" in model_ids

    def test_models_empty_when_no_config(self) -> None:
        """AC: /v1/models returns empty list when no config is loaded."""
        app = create_app()  # no config, no adapters
        with TestClient(app) as client:
            resp = client.get("/v1/models")
        assert resp.status_code == 200
        body = resp.json()
        assert body["object"] == "list"
        assert body["data"] == []

    def test_models_shape_fields(self) -> None:
        """AC: each model entry has id, object, created, owned_by fields."""
        config = RouterConfig(
            version=1,
            policy_path="/dev/null",
            shield=ShieldConfig(profiles=[]),
            backends=[
                BackendConfig(id="openai-cloud", kind="openai", default_model="gpt-4o"),
            ],
        )
        app = create_app(
            config=config,
            adapters={"openai-cloud": FakeAdapter(backend_id="openai-cloud")},
        )
        with TestClient(app) as client:
            resp = client.get("/v1/models")
        model = resp.json()["data"][0]
        assert model["id"] == "gpt-4o"
        assert model["object"] == "model"
        assert isinstance(model["created"], int)
        assert "ogentic-router/openai" in model["owned_by"]
