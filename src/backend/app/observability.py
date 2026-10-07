"""OpenTelemetry setup for distributed tracing and metrics."""

import logging
import math
import os
import re
import threading
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Literal

from fastapi import FastAPI
from opentelemetry import _logs, metrics, trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.metrics.view import ExplicitBucketHistogramAggregation, View
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter

from app.config import Settings

logger = logging.getLogger(__name__)

# HTTP methods we want to keep in traces (everything else is noise)
_TRACED_HTTP_METHODS = frozenset({"GET", "POST"})


class FilteringSpanProcessor(BatchSpanProcessor):
    """BatchSpanProcessor that silently drops noisy spans before export.

    Filters out:
    - ASGI internal send/receive spans (kind=INTERNAL, name contains 'http send'
      or 'http receive') that create duplicate InProcess entries for SSE streams.
    - HTTP spans for methods other than GET/POST (OPTIONS, PATCH, DELETE, …).
    """

    def __init__(self, exporter: SpanExporter, **kwargs) -> None:
        super().__init__(exporter, **kwargs)

    def on_end(self, span: ReadableSpan) -> None:
        # Drop ASGI internal send/receive spans
        if span.kind == trace.SpanKind.INTERNAL:
            name = span.name
            if "http send" in name or "http receive" in name:
                return

        # Drop HTTP spans for methods we don't care about
        attrs = span.attributes or {}
        http_method = attrs.get("http.request.method") or attrs.get("http.method")
        if http_method and http_method.upper() not in _TRACED_HTTP_METHODS:
            return

        super().on_end(span)


# GenAI metric bucket boundaries per OTel semantic conventions
_TOKEN_BUCKETS = (1, 4, 16, 64, 256, 1024, 4096, 16384, 65536, 262144, 1048576, 4194304, 16777216, 67108864)
_DURATION_BUCKETS = (0.01, 0.02, 0.04, 0.08, 0.16, 0.32, 0.64, 1.28, 2.56, 5.12, 10.24, 20.48, 40.96, 81.92)

# Module-level reference for the tracer provider (used by instrument_fastapi_app)
_tracer_provider: TracerProvider | None = None
_USE_CASE_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}$")


class ReplicaInvocationState:
    """Track whether this process has handled its first real invocation."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._invoked = False

    def begin(self) -> bool:
        with self._lock:
            cold_start = not self._invoked
            self._invoked = True
            return cold_start


replica_invocation_state = ReplicaInvocationState()

ReplicaInvocationOutcome = Literal[
    "warm",
    "cold-initialized-by-this-request",
    "joined-in-flight-initialization",
]


def disable_genai_content_recording() -> None:
    os.environ["AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED"] = "false"
    os.environ["OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"] = "false"


def safe_use_case_id(value: str | None) -> str | None:
    """Return a synthetic slug ID suitable for span attributes."""
    return value if value and _USE_CASE_ID_RE.fullmatch(value) else None


def pre_handler_delay_ms(headers: Mapping[str, str], now: float | None = None) -> int | None:
    """Return elapsed time from a recognized platform request-entry header."""
    request_entry: float | None = None
    for name in ("x-request-start", "x-envoy-request-start-time"):
        value = headers.get(name)
        if not value:
            continue
        try:
            if name == "x-request-start":
                timestamp = value.strip()
                if timestamp.startswith("t="):
                    timestamp = timestamp[2:]
                request_entry = float(timestamp)
                if request_entry >= 100_000_000_000:
                    request_entry /= 1000
            else:
                try:
                    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
                except ValueError:
                    from email.utils import parsedate_to_datetime

                    parsed = parsedate_to_datetime(value.strip())
                request_entry = parsed.replace(tzinfo=parsed.tzinfo or UTC).timestamp()
        except (TypeError, ValueError, OverflowError):
            request_entry = None
        if request_entry is not None and not math.isfinite(request_entry):
            request_entry = None
        if request_entry is not None:
            break

    if request_entry is None:
        return None
    elapsed = (time.time() if now is None else now) - request_entry
    if not math.isfinite(elapsed):
        return None
    elapsed_ms = int(elapsed * 1000)
    return elapsed_ms if 0 <= elapsed_ms <= 86_400_000 else None


def set_invocation_span_attributes(
    span: trace.Span,
    *,
    use_case: str | None = None,
    cold_start: bool = False,
    replica_invocation_state: ReplicaInvocationOutcome | None = None,
    readiness_source: Literal["early_init", "first_invocation_fallback"] | None = None,
    pre_handler_ms: int | None = None,
) -> None:
    """Attach only synthetic correlation and platform timing metadata to a span."""
    if replica_invocation_state is not None:
        cold_start = replica_invocation_state == "cold-initialized-by-this-request"
        span.set_attribute("kratos.replica_invocation_state", replica_invocation_state)
    if readiness_source is not None:
        span.set_attribute("kratos.readiness_source", readiness_source)

    span_context = span.get_span_context()
    if (
        isinstance(span_context.trace_id, int)
        and span_context.trace_id > 0
        and isinstance(span_context.span_id, int)
        and span_context.span_id > 0
    ):
        trace_id = trace.format_trace_id(span_context.trace_id)
        span.set_attribute("kratos.trace_id", trace_id)
        span.set_attribute("kratos.span_id", trace.format_span_id(span_context.span_id))
        span.set_attribute("kratos.operation_id", trace_id)

    span.set_attribute(
        "kratos.revision_name",
        os.environ.get("CONTAINER_APP_REVISION") or os.environ.get("KRATOS_SOURCE_REVISION") or "unknown",
    )
    span.set_attribute("kratos.replica_name", os.environ.get("CONTAINER_APP_REPLICA_NAME") or "unknown")
    span.set_attribute("kratos.cold_start", cold_start)
    span.set_attribute("kratos.pre_handler_delay_available", pre_handler_ms is not None)
    span.set_attribute(
        "kratos.pre_handler_delay_source", "request_header" if pre_handler_ms is not None else "platform_logs"
    )
    if pre_handler_ms is not None:
        span.set_attribute("kratos.pre_handler_delay_ms", pre_handler_ms)
    safe_id = safe_use_case_id(use_case)
    if safe_id:
        span.set_attribute("kratos.use_case", safe_id)


def setup_telemetry(settings: Settings) -> None:
    """Configure OpenTelemetry with Azure Monitor exporters (traces, metrics, logs/events)."""
    global _tracer_provider

    disable_genai_content_recording()

    resource = Resource.create(
        {
            "service.name": settings.otel_service_name,
            "service.version": "0.1.0",
            "deployment.environment": settings.environment,
        }
    )

    provider = TracerProvider(resource=resource)

    metric_reader = None
    if settings.applicationinsights_connection_string:
        try:
            from azure.monitor.opentelemetry.exporter import (
                AzureMonitorLogExporter,
                AzureMonitorMetricExporter,
                AzureMonitorTraceExporter,
            )

            conn_str = settings.applicationinsights_connection_string

            try:
                # Traces → AppInsights 'dependencies' and 'requests' tables
                trace_exporter = AzureMonitorTraceExporter(connection_string=conn_str)
                provider.add_span_processor(FilteringSpanProcessor(trace_exporter))
                logger.info("Azure Monitor trace exporter configured")
            except Exception:
                logger.warning("Failed to configure Azure Monitor trace exporter", exc_info=True)

            try:
                # Metrics → AppInsights 'customMetrics' table
                metric_exporter = AzureMonitorMetricExporter(connection_string=conn_str)
                metric_reader = PeriodicExportingMetricReader(metric_exporter, export_interval_millis=60000)
            except Exception:
                logger.warning("Failed to configure Azure Monitor metric exporter", exc_info=True)

            try:
                # Logs/Events → AppInsights 'traces' and 'customEvents' tables
                log_exporter = AzureMonitorLogExporter(connection_string=conn_str)
                log_provider = LoggerProvider(resource=resource)
                log_provider.add_log_record_processor(BatchLogRecordProcessor(log_exporter))
                _logs.set_logger_provider(log_provider)

                # Bridge Python logging → OTel Logs → AppInsights 'traces' table.
                # This captures all app logs (copilot_agent events, skill calls, etc.)
                # and correlates them with the active trace context.
                otel_handler = LoggingHandler(level=logging.INFO, logger_provider=log_provider)
                logging.getLogger().addHandler(otel_handler)
                logger.info("Azure Monitor log/events exporter configured (with Python logging bridge)")
            except Exception:
                logger.warning("Failed to configure Azure Monitor log/events exporter", exc_info=True)

        except Exception:
            logger.warning("Failed to load Azure Monitor exporters", exc_info=True)

    if metric_reader:
        meter_provider = MeterProvider(
            resource=resource,
            metric_readers=[metric_reader],
            views=[
                View(instrument_name=name, aggregation=ExplicitBucketHistogramAggregation(boundaries=buckets))
                for name, buckets in (
                    ("gen_ai.client.token.usage", _TOKEN_BUCKETS),
                    ("gen_ai.client.operation.duration", _DURATION_BUCKETS),
                    ("gen_ai.agent.tool_calls", _DURATION_BUCKETS),
                    ("gen_ai.tool.duration", _DURATION_BUCKETS),
                )
            ],
        )
        metrics.set_meter_provider(meter_provider)
        logger.info("Azure Monitor metric exporter configured")

    trace.set_tracer_provider(provider)
    _tracer_provider = provider

    # Instrument OpenAI SDK for Foundry model call tracing
    try:
        from opentelemetry.instrumentation.openai_v2 import OpenAIInstrumentor

        OpenAIInstrumentor().instrument(tracer_provider=provider)
        logger.info("OpenAI SDK tracing instrumented")
    except Exception:
        logger.warning("Failed to instrument OpenAI SDK — model call traces will not be captured")

    logger.info("OpenTelemetry initialized — service=%s", settings.otel_service_name)


def instrument_fastapi_app(app: FastAPI) -> None:
    """Instrument a specific FastAPI app instance for HTTP request tracing.

    Must be called AFTER the FastAPI app is created but BEFORE the first request.
    Uses the global tracer provider (set later by setup_telemetry via the lifespan).
    The OpenTelemetry ProxyTracerProvider ensures spans are routed correctly once
    the real provider is configured.
    """
    FastAPIInstrumentor.instrument_app(app)
    logger.info("FastAPI app instrumented for HTTP request tracing")


# ── GenAI Metrics ────────────────────────────────────────────────────────────
# Expose histograms per OTel GenAI semantic conventions so copilot_agent.py can
# record token usage and operation duration without coupling to the exporter setup.
# Every recording carries `gen_ai.request.model` and `kratos.routing.role`
# (orchestrator / deep-reasoning / fast, per ADR 0001); token usage is split by
# `gen_ai.token.type` = input / output / reasoning.

_meter = metrics.get_meter("kratos-agent", "0.1.0")

token_usage_histogram = _meter.create_histogram(
    name="gen_ai.client.token.usage",
    description="Number of input, output and reasoning tokens used",
    unit="{token}",
)

input_token_source_histogram = _meter.create_histogram(
    name="gen_ai.client.token.usage.by_source",
    description="Estimated input tokens by context source",
    unit="{token}",
)

operation_duration_histogram = _meter.create_histogram(
    name="gen_ai.client.operation.duration",
    description="GenAI operation duration",
    unit="s",
)

tool_call_count_histogram = _meter.create_histogram(
    name="gen_ai.agent.tool_calls",
    description="Number of tool calls made by an agent invocation",
    unit="{tool}",
)

tool_duration_histogram = _meter.create_histogram(
    name="gen_ai.tool.duration",
    description="GenAI tool execution duration",
    unit="s",
)

hosted_agent_initialization_duration_histogram = _meter.create_histogram(
    name="kratos.hosted_agent.initialization.duration",
    description="Hosted-agent shared-core initialization phase duration",
    unit="s",
)

_HOSTED_AGENT_INITIALIZATION_PHASES = frozenset({"telemetry", "core_parallel", "seed"})


def record_hosted_agent_initialization_phases(phases_ms: Mapping[str, float]) -> None:
    """Record bounded initialization phases, converting milliseconds to seconds."""
    for phase, duration_ms in phases_ms.items():
        if phase in _HOSTED_AGENT_INITIALIZATION_PHASES:
            hosted_agent_initialization_duration_histogram.record(
                duration_ms / 1000,
                {"phase": phase},
            )


warm_pool_initial_warmup_duration_histogram = _meter.create_histogram(
    name="kratos.warm_pool.initial_warmup.duration",
    description="Initial warm-pool fill duration",
    unit="s",
)

warm_pool_target_histogram = _meter.create_histogram(
    name="kratos.warm_pool.target",
    description="Target number of warm-pool sandboxes",
    unit="{sandbox}",
)

warm_pool_available_histogram = _meter.create_histogram(
    name="kratos.warm_pool.available",
    description="Available warm-pool sandboxes after initial fill",
    unit="{sandbox}",
)


def record_initial_warmup_metrics(
    duration_s: float,
    target: int,
    available: int,
    fallback_used: bool,
) -> None:
    """Record the initial warm-pool fill outcome."""
    attributes = {"fallback_used": fallback_used}
    warm_pool_initial_warmup_duration_histogram.record(duration_s, attributes)
    warm_pool_target_histogram.record(target, attributes)
    warm_pool_available_histogram.record(available, attributes)
