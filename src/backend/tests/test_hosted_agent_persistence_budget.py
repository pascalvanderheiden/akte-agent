"""Cosmos persistence budget, proven at the hosted-agent invocation entry point.

These tests drive ``handle_invoke`` end to end with only the Cosmos containers
faked, and assert on what a caller or operator can observe: the streamed
response, how long the invocation was held waiting on Cosmos, which messages
were persisted and which signature was logged. They do not inspect retry or
timeout internals.

The budget under test is the real ADR 0003 value from ``app.latency_budget``.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import sys
import time
import types
from pathlib import Path

import pytest
from azure.cosmos.exceptions import CosmosHttpResponseError, CosmosResourceNotFoundError
from starlette.requests import Request

from app.config import Settings
from app.latency_budget import COSMOS_PERSISTENCE_BUDGET_MS, COSMOS_PERSISTENCE_BUDGET_S, INTERACTIVE_P50_BUDGET_S
from app.models import ContentEvent, MessageRole
from app.routers import agent as agent_router
from app.services.cosmos_service import (
    NETWORK_DENIAL_SIGNATURE,
    OPERATION_TIMEOUT_SIGNATURE,
    RBAC_DENIAL_SIGNATURE,
    UNCLASSIFIED_DENIAL_SIGNATURE,
    CosmosService,
)
from app.services.skill_registry import SkillRegistry

ADR = Path(__file__).resolve().parents[3] / "docs" / "adr" / "0003-interactive-request-latency-budget.md"

FIREWALL_DENIAL = (
    "Request originated from client IP 10.0.1.4 through public internet. "
    "This is blocked by your Cosmos DB account firewall settings."
)

# Scheduling slack on top of the budget; a regression to one budget per message
# or to the SDK retry ladder overshoots this by far more.
_TOLERANCE_S = 0.15


class _FakeInvocationAgentServerHost:
    def invoke_handler(self, func):
        return func

    def run(self):
        return None


@pytest.fixture
def hosted(monkeypatch):
    invocations = types.ModuleType("azure.ai.agentserver.invocations")
    invocations.InvocationAgentServerHost = _FakeInvocationAgentServerHost
    try:
        import azure.ai as azure_ai
    except ImportError:
        azure_ai = types.ModuleType("azure.ai")
        azure_ai.__path__ = []
        monkeypatch.setitem(sys.modules, "azure.ai", azure_ai)
    agentserver = types.ModuleType("azure.ai.agentserver")
    agentserver.__path__ = []
    agentserver.invocations = invocations
    monkeypatch.setattr(azure_ai, "agentserver", agentserver, raising=False)
    monkeypatch.setitem(sys.modules, "azure.ai.agentserver", agentserver)
    monkeypatch.setitem(sys.modules, "azure.ai.agentserver.invocations", invocations)
    monkeypatch.setattr(sys, "path", sys.path.copy())

    module_path = Path(__file__).resolve().parents[2] / "hosted-agent" / "main.py"
    spec = importlib.util.spec_from_file_location("hosted_agent_persistence_budget", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    module._copilot_agent = _FakeAgent()
    module._registries["akte-agent"] = SkillRegistry(use_case="akte-agent", system_prompt="")
    module._collect_generated_files = lambda _response: []
    return module


class _FakeAgent:
    def __init__(self) -> None:
        self.run_kwargs: dict = {}

    def set_conversation_use_case(self, *_args):
        return None

    def set_conversation_mcp_tokens(self, *_args):
        return None

    async def run(self, **_kwargs):
        self.run_kwargs = _kwargs
        yield ContentEvent(content="Synthetic reply")

    def get_run_stats(self, _conversation_id):
        return {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "reasoning_tokens": 0,
            "total_tokens": 0,
            "time_to_first_token_ms": 0,
            "model_latency_ms": 0,
        }


class _CosmosClock:
    """Wall time the invocation spent waiting on any Cosmos call."""

    def __init__(self) -> None:
        self.held_s = 0.0

    async def hold(self, call):
        started = time.monotonic()
        try:
            return await call()
        finally:
            self.held_s += time.monotonic() - started


class _NetworkDeniedContainer:
    """Every call is refused by the account firewall after ``latency_s``.

    A non-zero latency stands in for the SDK retry ladder that preceded the
    403 in the incident.
    """

    def __init__(self, clock: _CosmosClock, latency_s: float) -> None:
        self._clock = clock
        self._latency_s = latency_s

    async def _deny(self):
        await asyncio.sleep(self._latency_s)
        error = CosmosHttpResponseError(status_code=403, message=FIREWALL_DENIAL)
        error.sub_status = 5301
        raise error

    async def read_item(self, *_args, **_kwargs):
        return await self._clock.hold(self._deny)

    async def upsert_item(self, *_args, **_kwargs):
        return await self._clock.hold(self._deny)


class _HealthyContainer:
    def __init__(self, clock: _CosmosClock) -> None:
        self._clock = clock
        self.items: dict[str, dict] = {}

    async def read_item(self, item, partition_key):
        async def _read():
            if item not in self.items:
                raise CosmosResourceNotFoundError(status_code=404, message="not found")
            return self.items[item]

        return await self._clock.hold(_read)

    async def upsert_item(self, body):
        async def _upsert():
            self.items[body["id"]] = body
            return body

        return await self._clock.hold(_upsert)


def _cosmos(conversations, messages) -> CosmosService:
    service = CosmosService(Settings(cosmos_db_endpoint="https://example.documents.azure.com:443/"))
    service._conversations_container = conversations
    service._messages_container = messages
    return service


async def _invoke(hosted, payload: dict) -> tuple[int, list[dict]]:
    body = json.dumps(payload).encode()

    async def receive():
        return {"type": "http.request", "body": body}

    request = Request({"type": "http", "method": "POST", "path": "/", "headers": []}, receive)
    request.state.invocation_id = "synthetic-invocation"
    response = await hosted.handle_invoke(request)
    events = []
    async for chunk in response.body_iterator:
        text = chunk.decode() if isinstance(chunk, bytes) else chunk
        if text.startswith("data: "):
            events.append(json.loads(text.removeprefix("data: ").strip()))
        elif text.startswith("event: done"):
            events.append({"event": "invocation_done"})
    return response.status_code, events


@pytest.mark.parametrize("denial_latency_s", [0.0, COSMOS_PERSISTENCE_BUDGET_S * 2 / 3])
async def test_network_denied_invocation_succeeds_within_one_persistence_budget(
    hosted, caplog: pytest.LogCaptureFixture, denial_latency_s: float
) -> None:
    clock = _CosmosClock()
    hosted._cosmos_service = _cosmos(
        _NetworkDeniedContainer(clock, denial_latency_s),
        _NetworkDeniedContainer(clock, denial_latency_s),
    )

    with caplog.at_level(logging.WARNING):
        status, events = await _invoke(
            hosted,
            {"message": "Hello", "conversationId": "synthetic-conversation", "useCase": "akte-agent"},
        )

    assert status == 200
    assert [e["data"]["content"] for e in events if e["event"] == "content"] == ["Synthetic reply"]
    assert any(e["event"] == "done" for e in events)
    assert not [e for e in events if e["event"] == "error"]
    assert events[-1] == {"event": "invocation_done"}

    # One budget for the whole invocation: the preflight read, the user message
    # and the assistant message together, not one budget per message.
    assert clock.held_s <= COSMOS_PERSISTENCE_BUDGET_S + _TOLERANCE_S

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert NETWORK_DENIAL_SIGNATURE in logged
    assert RBAC_DENIAL_SIGNATURE not in logged
    assert UNCLASSIFIED_DENIAL_SIGNATURE not in logged
    assert "Failed to persist user message to Cosmos (non-fatal)" in logged
    assert "Failed to persist assistant message to Cosmos (non-fatal)" in logged


async def test_healthy_cosmos_persists_both_messages_within_the_budget(
    hosted, caplog: pytest.LogCaptureFixture
) -> None:
    clock = _CosmosClock()
    messages = _HealthyContainer(clock)
    hosted._cosmos_service = _cosmos(_HealthyContainer(clock), messages)

    with caplog.at_level(logging.WARNING):
        status, events = await _invoke(
            hosted,
            {"message": "Hello", "conversationId": "synthetic-conversation", "useCase": "akte-agent"},
        )

    assert status == 200
    assert not [e for e in events if e["event"] == "error"]
    persisted = sorted(messages.items.values(), key=lambda doc: doc["role"] != MessageRole.USER.value)
    assert [(doc["role"], doc["content"], doc["conversationId"]) for doc in persisted] == [
        (MessageRole.USER.value, "Hello", "synthetic-conversation"),
        (MessageRole.ASSISTANT.value, "Synthetic reply", "synthetic-conversation"),
    ]
    assert clock.held_s <= COSMOS_PERSISTENCE_BUDGET_S

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "Failed to persist" not in logged
    assert OPERATION_TIMEOUT_SIGNATURE not in logged
    assert NETWORK_DENIAL_SIGNATURE not in logged


async def test_degraded_backend_invocation_skips_hosted_agent_persistence(hosted):
    clock = _CosmosClock()
    messages = _HealthyContainer(clock)
    hosted._cosmos_service = _cosmos(_HealthyContainer(clock), messages)

    status, events = await _invoke(
        hosted,
        {
            "message": "Hello",
            "conversationId": "synthetic-conversation",
            "useCase": "akte-agent",
            "persistenceAllowed": False,
        },
    )

    assert status == 200
    assert any(event.get("event") == "done" for event in events)
    assert messages.items == {}
    assert hosted._copilot_agent.run_kwargs["persist_session_mapping"] is False


def test_backend_and_hosted_agent_share_one_persistence_budget_policy(hosted) -> None:
    assert hosted.cosmos_persistence_budget is agent_router.cosmos_persistence_budget
    with hosted.cosmos_persistence_budget() as budget:
        assert budget.remaining_s == COSMOS_PERSISTENCE_BUDGET_S
    # The persistence slice must leave an ordinary request inside its p50 budget.
    assert 0 < COSMOS_PERSISTENCE_BUDGET_S < INTERACTIVE_P50_BUDGET_S


def test_adr_records_the_persistence_budget_the_code_enforces() -> None:
    assert f"{COSMOS_PERSISTENCE_BUDGET_MS} ms" in ADR.read_text(encoding="utf-8")
