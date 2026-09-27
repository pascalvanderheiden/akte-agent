"""Tests for the consistency-analysis Foundry request payload."""

from types import SimpleNamespace

import pytest

from app.routers import admin_analysis


class _Credential:
    async def get_token(self, _scope: str) -> SimpleNamespace:
        return SimpleNamespace(token=object())


class _Response:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"choices": [{"message": {"content": "{}"}}]}


class _Client:
    def __init__(self) -> None:
        self.payload: dict | None = None

    async def post(self, _url: str, *, json: dict, headers: dict) -> _Response:
        self.payload = json
        assert headers["Authorization"].startswith("Bearer ")
        return _Response()


class _Routing:
    def __init__(self, _settings: object) -> None:
        pass

    def auxiliary_chat_url(self, _task: object) -> str:
        return "https://example.invalid/chat/completions"


@pytest.mark.asyncio
async def test_consistency_analysis_uses_reasoning_model_default_temperature(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _Client()
    monkeypatch.setattr(admin_analysis, "ModelRouting", _Routing)
    monkeypatch.setattr(admin_analysis, "_get_credential", lambda: _Credential())
    monkeypatch.setattr(admin_analysis, "_get_http_client", lambda: client)

    assert await admin_analysis._call_llm("system", "user", json_mode=True) == "{}"
    assert client.payload is not None
    assert client.payload["max_completion_tokens"] == 4096
    assert client.payload["response_format"] == {"type": "json_object"}
    assert "temperature" not in client.payload
