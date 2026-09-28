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


class _FailingClient:
    async def post(self, _url: str, *, json: dict, headers: dict) -> _Response:
        raise RuntimeError("boom")


def _use_span(monkeypatch: pytest.MonkeyPatch) -> str:
    """Pin a deterministic trace id so responses can be asserted on."""
    trace_id = 0x0123456789ABCDEF0123456789ABCDEF
    span = SimpleNamespace(get_span_context=lambda: SimpleNamespace(trace_id=trace_id))
    monkeypatch.setattr(admin_analysis.trace, "get_current_span", lambda: span)
    return f"{trace_id:032x}"


def _registry() -> SimpleNamespace:
    return SimpleNamespace(system_prompt="Be helpful.", skills={}, get_skill=lambda _name: None)


@pytest.mark.asyncio
async def test_analysis_success_carries_trace_id(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = _use_span(monkeypatch)
    monkeypatch.setattr(admin_analysis, "_get_registry", lambda _request, _use_case: _registry())

    async def _llm(*_args: object, **_kwargs: object) -> str:
        return '{"summary": "ok", "overallScore": 90, "issues": [], "strengths": []}'

    monkeypatch.setattr(admin_analysis, "_call_llm", _llm)

    result = await admin_analysis.analyze_consistency(SimpleNamespace(), None, "akte-agent")

    assert result.traceId == expected


@pytest.mark.asyncio
async def test_analysis_failure_carries_trace_id(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = _use_span(monkeypatch)
    monkeypatch.setattr(admin_analysis, "ModelRouting", _Routing)
    monkeypatch.setattr(admin_analysis, "_get_credential", lambda: _Credential())
    monkeypatch.setattr(admin_analysis, "_get_http_client", lambda: _FailingClient())

    with pytest.raises(admin_analysis.HTTPException) as exc_info:
        await admin_analysis._call_llm("system", "user")

    assert exc_info.value.detail["traceId"] == expected


@pytest.mark.asyncio
async def test_apply_fix_carries_trace_id(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = _use_span(monkeypatch)
    monkeypatch.setattr(admin_analysis, "_get_registry", lambda _request, _use_case: _registry())

    body = admin_analysis.ApplyFixRequest(category="unused", title="t", description="d", affectedSkills=["missing"])

    result = await admin_analysis.apply_fix(SimpleNamespace(), body, "akte-agent")

    assert result.success is False
    assert result.traceId == expected


def test_trace_id_omitted_without_span_context(monkeypatch: pytest.MonkeyPatch) -> None:
    span = SimpleNamespace(get_span_context=lambda: SimpleNamespace(trace_id=0))
    monkeypatch.setattr(admin_analysis.trace, "get_current_span", lambda: span)

    assert admin_analysis._trace_id() is None
    assert "traceId" not in admin_analysis._http_error(502, "nope").detail
