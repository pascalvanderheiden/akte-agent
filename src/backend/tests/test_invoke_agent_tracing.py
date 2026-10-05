"""Tracing contracts for hosted-agent invocation phases and proxy propagation."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from opentelemetry import baggage, propagate, trace
from opentelemetry.baggage.propagation import W3CBaggagePropagator
from opentelemetry.context import Context, attach, detach
from opentelemetry.propagators.composite import CompositePropagator
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from app.config import Settings
from app.services.copilot_agent import CopilotAgent
from app.services.foundry_agent_proxy import FoundryAgentProxy


class _Response:
    status = 200
    headers = {}

    def __init__(self, stream=None):
        self.content = self
        self.stream = stream

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def iter_any(self):
        if self.stream is not None:
            async for chunk in self.stream:
                yield chunk


@pytest.mark.asyncio
async def test_proxy_propagates_w3c_trace_context_to_upstream_without_baggage(monkeypatch):
    captured = {}
    global_propagator = CompositePropagator([TraceContextTextMapPropagator(), W3CBaggagePropagator()])
    monkeypatch.setattr(propagate, "get_global_textmap", lambda: global_propagator)
    inbound_context = global_propagator.extract(
        {
            "traceparent": "00-1234567890abcdef1234567890abcdef-1234567890abcdef-01",
            "tracestate": "synthetic=correlation",
            "baggage": "synthetic_user_data=must-not-cross-boundary",
        },
        context=Context(),
    )

    def post(_endpoint, *, headers, **_kwargs):
        captured.update(headers)
        return _Response()

    settings = Settings(local_mode=True, foundry_agent_invocations_endpoint="http://fake/invocations")
    proxy = FoundryAgentProxy(settings)
    proxy._http_session = SimpleNamespace(post=post)
    proxy._get_token = AsyncMock(return_value=None)
    proxy.claim_warm_session = AsyncMock(return_value=None)
    provider = TracerProvider()
    tracer = provider.get_tracer(__name__)

    context_token = attach(inbound_context)
    try:
        with (
            tracer.start_as_current_span("POST /api/agent/chat", kind=trace.SpanKind.SERVER) as request_span,
            tracer.start_as_current_span("request.admission") as admission_span,
            tracer.start_as_current_span("hosted_agent.invoke", kind=trace.SpanKind.CLIENT) as invoke_span,
        ):
            assert baggage.get_baggage("synthetic_user_data") == "must-not-cross-boundary"
            async for _event in proxy.invoke("hello", "synthetic-conversation"):
                pass
    finally:
        detach(context_token)

    traceparent = captured["traceparent"]
    assert captured["tracestate"] == "synthetic=correlation"
    assert "baggage" not in captured
    _, trace_id, parent_span_id, _ = traceparent.split("-")
    assert admission_span.parent.span_id == request_span.get_span_context().span_id
    assert invoke_span.parent.span_id == admission_span.get_span_context().span_id
    assert int(trace_id, 16) == request_span.get_span_context().trace_id
    assert int(parent_span_id, 16) == invoke_span.get_span_context().span_id
    provider.shutdown()


@pytest.mark.asyncio
async def test_proxy_to_hosted_agent_phase_hierarchy_and_duration(monkeypatch):
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

    async def hosted_handler(headers, payload):
        remote_context = TraceContextTextMapPropagator().extract(headers, context=Context())
        with tracer.start_as_current_span("POST /invocations", kind=trace.SpanKind.SERVER, context=remote_context):
            async for _event in agent.run(payload["input"], payload["conversationId"], use_case=payload["useCase"]):
                yield b""

    def post(_endpoint, *, headers, json, **_kwargs):
        return _Response(hosted_handler(headers, json))

    proxy = FoundryAgentProxy(Settings(local_mode=True, foundry_agent_invocations_endpoint="http://fake/invocations"))
    proxy._http_session = SimpleNamespace(post=post)
    proxy._get_token = AsyncMock(return_value=None)
    proxy.claim_warm_session = AsyncMock(return_value=None)
    with (
        tracer.start_as_current_span("POST /api/agent/chat", kind=trace.SpanKind.SERVER) as request_span,
        tracer.start_as_current_span("request.admission") as admission_span,
        tracer.start_as_current_span("hosted_agent.invoke", kind=trace.SpanKind.CLIENT) as proxy_span,
    ):
        async for _event in proxy.invoke("hello", "synthetic-conversation", use_case="akte-agent"):
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
    assert admission_span.parent.span_id == request_span.get_span_context().span_id
    assert proxy_span.parent.span_id == admission_span.get_span_context().span_id
    assert server_span.parent.is_remote
    assert server_span.parent.span_id == proxy_span.get_span_context().span_id
    assert invoke_span.parent.span_id == server_span.context.span_id
    assert all(span.context.trace_id == server_span.context.trace_id for span in spans)

    expected_parents = {
        "request.admission": server_span.context.span_id,
        "session_skill.prepare": invoke_span.context.span_id,
        "gen_ai.chat": invoke_span.context.span_id,
        "execute_tool web_search": invoke_span.context.span_id,
        "response.stream_finalize": invoke_span.context.span_id,
    }
    for phase_name, parent_span_id in expected_parents.items():
        phase_spans = [
            span
            for span in spans
            if span.name == phase_name and span.context.span_id != admission_span.get_span_context().span_id
        ]
        assert phase_spans
        assert all(span.parent and span.parent.span_id == parent_span_id for span in phase_spans)

    invoke_children = [span for span in spans if span.parent and span.parent.span_id == invoke_span.context.span_id]
    parent_duration_ns = invoke_span.end_time - invoke_span.start_time
    child_duration_ns = sum(span.end_time - span.start_time for span in invoke_children)
    unattributed_remainder_ns = parent_duration_ns - child_duration_ns
    assert unattributed_remainder_ns >= 0
    assert child_duration_ns + unattributed_remainder_ns == parent_duration_ns

    provider.shutdown()


@pytest.mark.asyncio
async def test_active_model_span_ends_with_error_when_run_is_cancelled(monkeypatch):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer(__name__)
    monkeypatch.setattr("app.services.copilot_agent.tracer", tracer)

    callback = None
    model_started = asyncio.Event()

    def on(handler):
        nonlocal callback
        callback = handler

    async def send(_message, **_kwargs):
        callback(SimpleNamespace(type=SimpleNamespace(value="assistant.turn_start"), data=SimpleNamespace()))
        model_started.set()

    session = SimpleNamespace(on=on, send=send)
    agent = CopilotAgent(Settings(foundry_endpoint="https://test.services.ai.azure.com"))
    monkeypatch.setattr(agent, "_get_or_create_session", AsyncMock(return_value=session))

    run = agent.run("hello", "cancelled-conversation", use_case="akte-agent")
    task = asyncio.create_task(anext(run))
    await model_started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    spans = exporter.get_finished_spans()
    model_span = next(span for span in spans if span.name == "gen_ai.chat")
    assert model_span.status.status_code == trace.StatusCode.ERROR
    assert model_span.attributes["error.type"] == "run_terminated"
    provider.shutdown()
