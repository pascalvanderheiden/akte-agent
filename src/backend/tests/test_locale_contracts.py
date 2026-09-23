"""Public API -> gateway -> hosted invocation -> reused SDK session contracts.

The SDK and follow-up model below are deterministic fixtures, NOT live evaluations.
No Azure services, credentials or model calls are used.
"""

import importlib.util
import json
import re
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.config import Settings
from app.models import MessageRole
from app.routers import agent, copilot_studio
from app.services.copilot_agent import CopilotAgent
from app.services.foundry_agent_proxy import FoundryAgentProxy


@pytest.fixture
def transport(monkeypatch):
    class Host:
        def invoke_handler(self, handler):
            return handler

    module = ModuleType("azure.ai.agentserver.invocations")
    module.InvocationAgentServerHost = Host
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setattr(sys, "path", sys.path.copy())
    spec = importlib.util.spec_from_file_location(
        "locale_hosted_fixture", Path(__file__).parents[2] / "hosted-agent" / "main.py"
    )
    hosted = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hosted)

    messages = []
    mappings = {}

    async def persist(message):
        messages.append(message)

    async def save_mapping(conversation, session):
        mappings[conversation] = session

    cosmos = SimpleNamespace(
        upsert_message=persist,
        get_session_mapping=AsyncMock(side_effect=lambda conversation: mappings.get(conversation)),
        upsert_session_mapping=save_mapping,
    )
    callback = None
    sent = []

    def on(handler):
        nonlocal callback
        callback = handler

    async def send(message, **kwargs):
        sent.append(message)
        match = re.match(r'<response_locale code="(en|nl)">', message)
        locale = match.group(1) if match else None
        # Deliberate fixture behavior, not an assertion that a real model obeys.
        reply = (
            "Synthetic English reply"
            if locale != "nl" or "in English" in message
            else "Synthetisch Nederlands antwoord"
        )
        callback(
            SimpleNamespace(
                type=SimpleNamespace(value="assistant.message_delta"),
                data=SimpleNamespace(delta_content=reply),
            )
        )
        callback(SimpleNamespace(type=SimpleNamespace(value="session.idle"), data=SimpleNamespace()))

    session = SimpleNamespace(session_id="synthetic-sdk-session", on=on, send=send)
    sdk = SimpleNamespace(create_session=AsyncMock(return_value=session))
    settings = Settings(local_mode="true", warm_pool_size=0)
    runtime = CopilotAgent(settings)
    runtime._client = sdk
    hosted._copilot_agent = runtime
    hosted._cosmos_service = cosmos
    hosted._ensure_registry = AsyncMock()
    hosted._collect_generated_files = lambda response: []
    captured = []
    strip_fields = [False]

    async def invoke(payload):
        body = json.dumps({"input": payload["input"]} if strip_fields[0] else payload).encode()

        async def receive():
            return {"type": "http.request", "body": body}

        request = Request({"type": "http", "method": "POST", "path": "/", "headers": []}, receive)
        request.state.invocation_id = "synthetic-invocation"
        return await hosted.handle_invoke(request)

    class GatewayResponse:
        status = 200
        headers = {"x-agent-session-id": "synthetic-gateway-session"}

        def __init__(self, payload):
            self.payload = payload
            self.content = self

        async def __aenter__(self):
            self.response = await invoke(self.payload)
            assert self.response.status_code == 200
            return self

        async def __aexit__(self, *args):
            return False

        async def iter_any(self):
            async for chunk in self.response.body_iterator:
                yield chunk

    def post(endpoint, *, json, **kwargs):
        captured.append((endpoint, json))
        return GatewayResponse(json)

    proxy = FoundryAgentProxy(settings)
    proxy._http_session = SimpleNamespace(post=post)
    proxy._get_token = AsyncMock(return_value=None)
    proxy.claim_warm_session = AsyncMock(return_value=None)
    follow_ups = AsyncMock(return_value=["Synthetic next question"])
    monkeypatch.setattr(agent, "generate_follow_ups", follow_ups)
    app = FastAPI()
    app.include_router(agent.router, prefix="/api/agent")
    app.include_router(copilot_studio.router, prefix="/api/copilot-studio")
    app.state.cosmos_service = cosmos
    app.state.foundry_proxy = proxy
    app.state.registries = {}
    return SimpleNamespace(
        client=TestClient(app),
        sdk=sdk,
        runtime=runtime,
        sent=sent,
        captured=captured,
        messages=messages,
        follow_ups=follow_ups,
        strip_fields=strip_fields,
        invoke=invoke,
    )


@pytest.mark.parametrize("strip_fields", [False, True])
def test_switch_language_on_reused_gateway_and_runtime_session(transport, strip_fields):
    transport.strip_fields[0] = strip_fields
    for locale, message, reply in [
        ("en", "Hello", "Synthetic English reply"),
        ("nl", "Ga verder", "Synthetisch Nederlands antwoord"),
        ("nl", "Write this output in English", "Synthetic English reply"),
    ]:
        response = transport.client.post(
            "/api/agent/chat",
            json={
                "conversationId": "synthetic-conversation",
                "message": message,
                "locale": locale,
            },
        )
        assert response.status_code == 200
        assert reply in response.text
        assert '"type": "done"' in response.text
        endpoint, payload = transport.captured[-1]
        assert payload["locale"] == locale
        assert payload["conversationId"] == "synthetic-conversation"
        assert transport.follow_ups.await_args.kwargs["locale"] == locale
        assert transport.follow_ups.await_args.args[:2] == (message, reply)
    assert transport.sdk.create_session.await_count == 1
    assert len(transport.runtime._sessions) == 1
    assert "agent_session_id=synthetic-gateway-session" in transport.captured[-1][0]
    assert {m.conversationId for m in transport.messages} == {"synthetic-conversation"}
    user_messages = [m.content for m in transport.messages if m.role == MessageRole.USER]
    assert set(user_messages) == {"Hello", "Ga verder", "Write this output in English"}
    assert all("<locale>" not in message and "<conversation_id>" not in message for message in transport.sent)


@pytest.mark.parametrize("route", ["/api/agent/chat", "/api/copilot-studio/chat"])
def test_legacy_request_and_invalid_explicit_locales(transport, route):
    request = {"conversationId": "legacy-conversation", "message": "Bonjour"}
    response = transport.client.post(route, json=request)
    assert response.status_code == 200
    assert "locale" not in transport.captured[-1][1]
    assert transport.sent[-1] == "Bonjour"
    for invalid in ["fr", "", ["nl"], 12]:
        response = transport.client.post(route, json={**request, "locale": invalid})
        assert response.status_code == 422
    assert len(transport.captured) == 1


def test_studio_language_and_session_reuse(transport):
    for locale in ["nl", "en"]:
        response = transport.client.post(
            "/api/copilot-studio/chat",
            json={
                "conversationId": "studio-conversation",
                "message": "Continue",
                "locale": locale,
            },
        )
        assert response.status_code == 200
        assert response.json()["conversationId"] == "studio-conversation"
        assert transport.captured[-1][1]["locale"] == locale
    assert transport.sdk.create_session.await_count == 1
    assert "agent_session_id=synthetic-gateway-session" in transport.captured[-1][0]


@pytest.mark.asyncio
async def test_hosted_invalid_locale_has_normal_validation_response(transport):
    for payload in [
        {"input": "Hello", "locale": "fr"},
        {"input": "<locale>fr</locale>\n\nHello"},
    ]:
        response = await transport.invoke(payload)
        assert response.status_code == 400
        assert json.loads(response.body)["error"] == "invalid_request"
    assert not transport.sent
