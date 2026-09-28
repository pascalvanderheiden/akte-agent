"""Tests for the hosted-agent streaming handler."""

import asyncio
import importlib.util
import json
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models import ContentEvent


class _FakeInvocationAgentServerHost:
    def invoke_handler(self, func):
        return func

    def run(self):
        return None


@pytest.fixture
def hosted_main(monkeypatch):
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

    module_path = Path(__file__).resolve().parents[2] / "hosted-agent" / "main.py"
    spec = importlib.util.spec_from_file_location("hosted_agent_main_under_test", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


async def _collect_stream(stream):
    chunks = []
    async for chunk in stream:
        chunks.append(chunk.decode())
    return chunks


def _json_data_events(chunks):
    events = []
    for chunk in chunks:
        if not chunk.startswith("data: "):
            continue
        events.append(json.loads(chunk.removeprefix("data: ").strip()))
    return events


@pytest.mark.asyncio
async def test_stream_response_reuses_completed_duration_for_done_event(hosted_main):
    span = MagicMock()

    class FakeAgent:
        def set_conversation_use_case(self, *_args):
            return None

        def set_conversation_mcp_tokens(self, *_args):
            return None

        async def run(self, **kwargs):
            kwargs["invocation_telemetry"].attach_span(span)
            yield ContentEvent(content="hello")

        def get_run_stats(self, _conversation_id):
            return {
                "prompt_tokens": 1,
                "completion_tokens": 2,
                "reasoning_tokens": 0,
                "total_tokens": 3,
                "time_to_first_token_ms": 4,
                "model_latency_ms": 5,
            }

    hosted_main._copilot_agent = FakeAgent()
    hosted_main._cosmos_service = AsyncMock()

    with patch.object(hosted_main.time, "monotonic", side_effect=[1.0, 2.0, 5.0, 10.0]):
        chunks = await _collect_stream(
            hosted_main._stream_response(
                "synthetic-invocation",
                "synthetic-conversation",
                "hello",
                "default",
            )
        )

    done_events = [event["data"] for event in _json_data_events(chunks) if event["event"] == "done"]
    assert done_events[0]["totalDurationMs"] == 9000
    span.set_attribute.assert_any_call("kratos.handler_duration_ms", 9000)
    span.set_attribute.assert_any_call("kratos.request_stage.pre_handler_remainder_ms", 1000)
    span.set_attribute.assert_any_call("kratos.request_stage.in_handler_duration_ms", 3000)
    span.set_attribute.assert_any_call("kratos.request_stage.post_handler_remainder_ms", 5000)
    span.end.assert_called_once()


@pytest.mark.asyncio
async def test_stream_response_ends_deferred_span_on_agent_exception(hosted_main):
    span = MagicMock()

    class FakeAgent:
        def set_conversation_use_case(self, *_args):
            return None

        def set_conversation_mcp_tokens(self, *_args):
            return None

        async def run(self, **kwargs):
            kwargs["invocation_telemetry"].attach_span(span)
            raise RuntimeError("synthetic failure")
            yield

        def get_run_stats(self, _conversation_id):
            raise AssertionError("run stats should not be read after failure")

    hosted_main._copilot_agent = FakeAgent()
    hosted_main._cosmos_service = AsyncMock()

    with patch.object(hosted_main.time, "monotonic", side_effect=[1.0, 2.0, 3.0]):
        chunks = await _collect_stream(
            hosted_main._stream_response(
                "synthetic-invocation",
                "synthetic-conversation",
                "hello",
                "default",
            )
        )

    error_events = [event for event in _json_data_events(chunks) if event["event"] == "error"]
    assert error_events[0]["data"]["code"] == "AGENT_ERROR"
    span.set_attribute.assert_any_call("kratos.handler_duration_ms", 2000)
    span.end.assert_called_once()


@pytest.mark.asyncio
async def test_stream_response_ends_deferred_span_on_cancellation(hosted_main):
    span = MagicMock()

    class FakeAgent:
        def set_conversation_use_case(self, *_args):
            return None

        def set_conversation_mcp_tokens(self, *_args):
            return None

        async def run(self, **kwargs):
            kwargs["invocation_telemetry"].attach_span(span)
            raise asyncio.CancelledError
            yield

    hosted_main._copilot_agent = FakeAgent()
    hosted_main._cosmos_service = AsyncMock()

    with (
        patch.object(hosted_main.time, "monotonic", side_effect=[1.0, 2.0, 3.0]),
        pytest.raises(asyncio.CancelledError),
    ):
        await _collect_stream(
            hosted_main._stream_response(
                "synthetic-invocation",
                "synthetic-conversation",
                "hello",
                "default",
            )
        )

    span.set_attribute.assert_any_call("kratos.handler_duration_ms", 2000)
    span.end.assert_called_once()
