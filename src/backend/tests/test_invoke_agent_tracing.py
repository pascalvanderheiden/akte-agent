"""Tracing contracts for hosted-agent invocation phases and proxy propagation."""

import asyncio
import os
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from opentelemetry import baggage, propagate, trace
from opentelemetry.baggage.propagation import W3CBaggagePropagator
from opentelemetry.context import Context, attach, detach
from opentelemetry.propagators.composite import CompositePropagator
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from app.config import Settings
from app.observability import ReplicaInvocationState, disable_genai_content_recording, pre_handler_delay_ms
from app.routers.agent import _traced_proxy_events
from app.services.copilot_agent import CopilotAgent, InvocationTelemetry
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
@pytest.mark.parametrize(
    ("replica_invocation_outcome", "readiness_source", "cold_start"),
    [
        ("cold-initialized-by-this-request", "first_invocation_fallback", True),
        ("joined-in-flight-initialization", "first_invocation_fallback", False),
        ("warm", "early_init", False),
    ],
)
async def test_proxy_to_hosted_agent_phase_hierarchy_and_duration(
    monkeypatch,
    replica_invocation_outcome,
    readiness_source,
    cold_start,
):
    monkeypatch.setenv("AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED", "true")
    monkeypatch.setenv("OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", "true")
    exporter = InMemorySpanExporter()
    metric_reader = InMemoryMetricReader()
    metric_provider = MeterProvider(metric_readers=[metric_reader])
    operation_duration = metric_provider.get_meter("invoke-agent-tests").create_histogram(
        "gen_ai.client.operation.duration",
        unit="s",
    )
    monkeypatch.setattr("app.services.copilot_agent.operation_duration_histogram", operation_duration)
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer(__name__)
    monkeypatch.setattr("app.services.copilot_agent.tracer", tracer)
    monkeypatch.setattr("app.routers.agent._tracer", tracer)

    callback = None
    tool_arguments = "TOOL_ARGUMENTS_MUST_NOT_BE_TRACED"
    tool_result = "TOOL_RESULT_MUST_NOT_BE_TRACED"

    def on(handler):
        nonlocal callback
        callback = handler

    def emit(event_type, **data):
        callback(SimpleNamespace(type=SimpleNamespace(value=event_type), data=SimpleNamespace(**data)))

    async def send(_message, **_kwargs):
        emit("assistant.turn_start")
        emit("assistant.message_delta", delta_content="Synthetic answer")
        emit("tool.execution_start", tool_name="web_search", arguments=tool_arguments)
        emit("tool.execution_complete", tool_name="web_search", output=tool_result, success=True, duration_ms=1)
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
            telemetry = InvocationTelemetry(
                invocation_id="synthetic-invocation",
                handler_started_at=time.monotonic(),
                cold_start=cold_start,
                replica_invocation_state=replica_invocation_outcome,
                readiness_source=readiness_source,
                pre_handler_ms=42,
            )
            try:
                async for _event in agent.run(
                    payload["input"],
                    payload["conversationId"],
                    use_case=payload["useCase"],
                    invocation_telemetry=telemetry,
                ):
                    yield b""
            finally:
                telemetry.complete()

    def post(_endpoint, *, headers, json, **_kwargs):
        return _Response(hosted_handler(headers, json))

    proxy = FoundryAgentProxy(Settings(local_mode=True, foundry_agent_invocations_endpoint="http://fake/invocations"))
    proxy._http_session = SimpleNamespace(post=post)
    proxy._get_token = AsyncMock(return_value=None)
    proxy.claim_warm_session = AsyncMock(return_value=None)
    prompt = "PROMPT_TEXT_MUST_NOT_BE_TRACED"
    user_identifier = "synthetic-user-id"
    sensitive_marker = "ACCESS_TOKEN_MUST_NOT_BE_TRACED"
    backend_endpoint = agent.settings.foundry_endpoint
    proxy_endpoint = proxy._settings.foundry_agent_invocations_endpoint
    with (
        tracer.start_as_current_span("POST /api/agent/chat", kind=trace.SpanKind.SERVER) as request_span,
        tracer.start_as_current_span("request.admission") as admission_span,
    ):
        async for _event in _traced_proxy_events(
            proxy,
            message=prompt,
            conversation_id=user_identifier,
            use_case="akte-agent",
            mcp_access_tokens={"synthetic-server": sensitive_marker},
            cold_start=cold_start,
            pre_handler_ms=42,
        ):
            pass

    spans = exporter.get_finished_spans()
    server_span = next(span for span in spans if span.name == "POST /invocations")
    invoke_span = next(span for span in spans if span.name == "invoke_agent kratos-agent")
    proxy_span = next(span for span in spans if span.name == "hosted_agent.invoke")
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
    for span in (proxy_span, invoke_span):
        attributes = span.attributes or {}
        assert attributes["kratos.trace_id"] == f"{span.context.trace_id:032x}"
        assert attributes["kratos.span_id"] == f"{span.context.span_id:016x}"
        assert attributes["kratos.operation_id"] == attributes["kratos.trace_id"]
        assert attributes["kratos.use_case"] == "akte-agent"
        assert "kratos.revision_name" in attributes
        assert "kratos.replica_name" in attributes
        assert isinstance(attributes["kratos.cold_start"], bool)
    assert proxy_span.attributes["kratos.cold_start"] is cold_start
    assert proxy_span.attributes["kratos.pre_handler_delay_ms"] == 42
    assert invoke_span.attributes["kratos.cold_start"] is cold_start
    assert invoke_span.attributes["kratos.replica_invocation_state"] == replica_invocation_outcome
    assert invoke_span.attributes["kratos.readiness_source"] == readiness_source
    assert invoke_span.attributes["kratos.cold_start"] is (
        replica_invocation_outcome == "cold-initialized-by-this-request"
    )
    assert invoke_span.attributes["kratos.pre_handler_delay_ms"] == 42
    assert replica_invocation_outcome in {
        "warm",
        "cold-initialized-by-this-request",
        "joined-in-flight-initialization",
    }
    assert readiness_source in {"early_init", "first_invocation_fallback"}
    assert isinstance(invoke_span.attributes["kratos.cold_start"], bool)
    assert isinstance(invoke_span.attributes["kratos.pre_handler_delay_ms"], int)

    metric = next(
        metric
        for metric in metric_reader.get_metrics_data().resource_metrics[0].scope_metrics[0].metrics
        if metric.name == "gen_ai.client.operation.duration"
    )
    assert metric.data.data_points[0].attributes["kratos.replica_invocation_state"] == replica_invocation_outcome
    assert metric.data.data_points[0].attributes["kratos.readiness_source"] == readiness_source

    prohibited_data = (
        prompt,
        tool_arguments,
        tool_result,
        user_identifier,
        "synthetic-invocation",
        sensitive_marker,
        backend_endpoint,
        proxy_endpoint,
    )
    for span in spans:
        attributes = dict(span.attributes or {})
        assert (
            not {
                "server.address",
                "gen_ai.tool.call.arguments",
                "gen_ai.tool.call.result",
                "gen_ai.input.messages",
                "gen_ai.output.messages",
                "gen_ai.system_instructions",
                "kratos.conversation_id",
                "gen_ai.conversation.id",
                "kratos.invocation_id",
            }
            & attributes.keys()
        )
        serialized_attributes = repr(attributes)
        assert all(value not in serialized_attributes for value in prohibited_data)

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
    metric_provider.shutdown()


def test_pre_handler_delay_uses_platform_entry_time_only():
    assert pre_handler_delay_ms({"x-request-start": "t=1700000000"}, now=1700000001.25) == 1250
    assert pre_handler_delay_ms({"x-request-start": "1700000000000"}, now=1700000001.25) == 1250
    assert (
        pre_handler_delay_ms(
            {"x-envoy-request-start-time": "2023-11-14T22:13:20Z"},
            now=1700000001.25,
        )
        == 1250
    )
    assert pre_handler_delay_ms({}, now=1700000001.25) is None
    assert pre_handler_delay_ms({"x-request-start": "not-a-timestamp"}, now=1700000001.25) is None


def test_cold_start_is_claimed_once_per_replica():
    state = ReplicaInvocationState()
    assert state.begin() is True
    assert state.begin() is False


def test_content_recording_is_forced_off(monkeypatch):
    monkeypatch.setenv("AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED", "true")
    monkeypatch.setenv("OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", "true")

    disable_genai_content_recording()

    assert os.environ["AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED"] == "false"
    assert os.environ["OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"] == "false"


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
