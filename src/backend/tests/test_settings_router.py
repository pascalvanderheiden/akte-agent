"""GET/POST /api/settings — no leftover error code on success (issue #71)."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings
from app.routers import settings as settings_router


def make_client(settings: Settings, monkeypatch) -> TestClient:
    monkeypatch.setattr(settings_router, "get_settings", lambda: settings)
    app = FastAPI()
    app.include_router(settings_router.router, prefix="/api/settings")
    app.state.cosmos_service = None
    return TestClient(app)


def test_get_settings_has_no_error_code(monkeypatch):
    settings = Settings(foundry_endpoint="https://example.foundry", foundry_model_deployment="gpt-x")
    client = make_client(settings, monkeypatch)

    response = client.get("/api/settings")

    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is True
    assert body["foundryEndpoint"] == "https://example.foundry"
    assert body["foundryModelDeployment"] == "gpt-x"
    assert "code" not in body


def test_post_settings_has_no_error_code(monkeypatch):
    settings = Settings()
    client = make_client(settings, monkeypatch)

    response = client.post(
        "/api/settings",
        json={"foundryEndpoint": "https://example.foundry", "foundryModelDeployment": "gpt-x"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["foundryEndpoint"] == "https://example.foundry"
    assert body["foundryModelDeployment"] == "gpt-x"
    assert "code" not in body
