"""Tracing contracts for hosted-agent invocation phases and proxy propagation."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.config import Settings
from app.services.copilot_agent import CopilotAgent
from app.services.foundry_agent_proxy import FoundryAgentProxy


class _Response:
    status = 200
    headers = {}

    def __init__(self):
        self.content = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def iter_any(self):
        if False:
            yield b""


@pytest.mark.asyncio
async def test_proxy_propagates_w3c_trace_context_to_upstream():
    captured = {}

    def post(_endpoint, *, headers, **_kwargs):
        captured.update(headers)
        return _Response()

    settings = Settings(local_mode=True, foundry_agent_invocations_endpoint="http://fake/invocations")
    proxy = FoundryAgentProxy(settings)
    proxy._http_session = SimpleNamespace(post=post)
    proxy._get_token = AsyncMock(return_value=None)
    proxy.claim_warm_session = AsyncMock(return_value=None)
    tracer = TracerProvider().get_tracer(__name__)

    with tracer.start_as_current_span("POST /api/agent/chat") as parent:
        expected_trace_id = parent.get_span_context().trace_id
        async for _event in proxy.invoke("hello", "synthetic-conversation"):
            pass

    traceparent = captured["traceparent"]
    assert int(traceparent.split("-")[1], 16) == expected_trace_id


@pytest.mark.asyncio
async def test_invoke_agent_phase_spans_share_parent_and_reconcile_duration(monkeypatch):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer(__name__)
    monkeypatch.setattr("app.services.copilot_agent.tracer", tracer)

    callback = None

    def on(handler):
        nonlocal callback
        callback = handler

    def emit(event_type, **data):
        callback(SimpleNamespace(type=SimpleNamespace(value=event_type), data=SimpleNamespace(**data)))

    async def send(_message, **_kwargs):
        emit("assistant.turn_start")
        emit("assistant.message_delta", delta_content="Synthetic answer")
        emit("tool.execution_start", tool_name="web_search", arguments='{"query":"synthetic"}')
        emit("tool.execution_complete", tool_name="web_search", output="Synthetic result", success=True, duration_ms=1)
        emit("assistant.turn_start")
        emit("assistant.message", content="Synthetic answer")
        emit("session.idle")

    session = SimpleNamespace(on=on, send=send)
    agent = CopilotAgent(
        Settings(
            foundry_endpoint="https://test.services.ai.azure.com",
            foundry_model_deployment="gpt-52",
            model_deployment_deep_reasoning="gpt-6-sol",
            model_deployment_fast="gpt-6-luna",
        )
    )
    monkeypatch.setattr(agent, "_get_or_create_session", AsyncMock(return_value=session))

    with tracer.start_as_current_span("POST /invocations", kind=trace.SpanKind.SERVER):
        async for _event in agent.run("hello", "synthetic-conversation", use_case="akte-agent"):
            pass

    spans = exporter.get_finished_spans()
    server_span = next(span for span in spans if span.name == "POST /invocations")
    invoke_span = next(span for span in spans if span.name == "invoke_agent kratos-agent")
    phases = {
        "request.admission",
        "session_skill.prepare",
        "gen_ai.chat",
        "execute_tool web_search",
        "response.stream_finalize",
    }
    assert phases <= {span.name for span in spans}
    assert invoke_span.parent.span_id == server_span.context.span_id
    assert all(span.context.trace_id == server_span.context.trace_id for span in spans)

    invoke_children = [span for span in spans if span.parent and span.parent.span_id == invoke_span.context.span_id]
    parent_duration_ns = invoke_span.end_time - invoke_span.start_time
    child_duration_ns = sum(span.end_time - span.start_time for span in invoke_children)
    unattributed_remainder_ns = parent_duration_ns - child_duration_ns
    assert unattributed_remainder_ns >= 0
    assert child_duration_ns + unattributed_remainder_ns == parent_duration_ns

    provider.shutdown()
