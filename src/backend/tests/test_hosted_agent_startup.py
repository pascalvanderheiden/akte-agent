import asyncio
import importlib.util
import logging
import sys
from datetime import datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader


@pytest.fixture
def hosted(monkeypatch):
    class Host:
        def invoke_handler(self, handler):
            return handler

    module = ModuleType("azure.ai.agentserver.invocations")
    module.InvocationAgentServerHost = Host
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setattr(sys, "path", sys.path.copy())
    spec = importlib.util.spec_from_file_location(
        "startup_hosted_fixture", Path(__file__).parents[2] / "hosted-agent" / "main.py"
    )
    hosted_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hosted_module)
    return hosted_module


@pytest.mark.parametrize(
    ("available", "reason", "list_error", "expected_reason"),
    [
        (False, "TimeoutError", None, "TimeoutError"),
        (False, None, None, "not_configured"),
        (True, None, None, None),
        (True, None, TimeoutError(), "TimeoutError"),
    ],
)
async def test_startup_blob_local_only_telemetry(
    hosted, monkeypatch, caplog, available, reason, list_error, expected_reason
):
    class FakeBlobSkillService:
        def __init__(self, settings):
            self._available = available
            self._unavailability_reason = reason

        @property
        def is_available(self):
            return self._available

        @property
        def unavailability_reason(self):
            return self._unavailability_reason

        async def initialize(self):
            pass

        async def _disable(self):
            self._available = False

        async def list_use_cases(self):
            if list_error:
                raise list_error
            return []

        async def seed_from_local(self):
            return []

    class FakeCosmosService:
        def __init__(self, settings):
            pass

        async def initialize(self):
            pass

    class FakeCopilotAgent:
        def __init__(self, settings):
            pass

        def set_registries(self, registries):
            pass

        def set_cosmos_service(self, cosmos_service):
            pass

        async def start(self):
            pass

    settings = SimpleNamespace(environment="test", foundry_model_deployment="test-model")
    monkeypatch.setattr(hosted, "get_settings", lambda: settings)
    monkeypatch.setattr(hosted, "setup_telemetry", lambda value: None)
    monkeypatch.setattr(hosted, "BlobSkillService", FakeBlobSkillService)
    monkeypatch.setattr(hosted, "CosmosService", FakeCosmosService)
    monkeypatch.setattr(hosted, "CopilotAgent", FakeCopilotAgent)

    with caplog.at_level(logging.WARNING, logger=hosted.__name__):
        await hosted._startup()

    records = [
        record for record in caplog.records if getattr(record, "event_name", None) == "HOSTED_AGENT_BLOB_LOCAL_ONLY"
    ]
    assert len(records) == int(expected_reason is not None)
    if records:
        record = records[0]
        assert record.failure_reason == expected_reason
        assert record.environment == "test"
        assert record.model_deployment == "test-model"
        assert datetime.fromisoformat(record.event_timestamp).tzinfo is not None


async def test_shared_core_startup_is_single_flight(hosted, monkeypatch):
    startup_started = asyncio.Event()
    finish_startup = asyncio.Event()
    startup_calls = 0

    async def startup():
        nonlocal startup_calls
        startup_calls += 1
        startup_started.set()
        await finish_startup.wait()

    monkeypatch.setattr(hosted, "_startup", startup)

    first = asyncio.create_task(hosted._ensure_shared_core())
    await startup_started.wait()
    joined = [asyncio.Event() for _ in range(5)]

    async def join_startup(entered):
        entered.set()
        return await hosted._ensure_shared_core()

    waiters = [asyncio.create_task(join_startup(entered)) for entered in joined]
    await asyncio.gather(*(entered.wait() for entered in joined))

    assert startup_calls == 1
    assert not first.done()
    finish_startup.set()

    outcomes = await asyncio.gather(first, *waiters)

    assert outcomes == ["cold-initialized-by-this-request", *["joined-in-flight-initialization"] * len(waiters)]
    assert startup_calls == 1
    assert hosted._startup_readiness_source == "first_invocation_fallback"
    assert await hosted._ensure_shared_core() == "warm"
    assert startup_calls == 1


async def test_failed_shared_core_startup_can_be_retried(hosted, monkeypatch):
    startup_calls = 0

    async def startup():
        nonlocal startup_calls
        startup_calls += 1
        if startup_calls == 1:
            raise RuntimeError("synthetic startup failure")

    monkeypatch.setattr(hosted, "_startup", startup)

    with pytest.raises(RuntimeError, match="synthetic startup failure"):
        await hosted._ensure_shared_core()

    assert hosted._startup_state == "failed"
    assert await hosted._ensure_shared_core() == "cold-initialized-by-this-request"
    assert hosted._startup_state == "ready"
    assert await hosted._ensure_shared_core() == "warm"
    assert startup_calls == 2


async def test_early_init_readiness_source_is_retained(hosted, monkeypatch):
    async def startup():
        pass

    monkeypatch.setattr(hosted, "_startup", startup)

    assert await hosted._ensure_shared_core(readiness_source="early_init") == "cold-initialized-by-this-request"
    assert hosted._startup_readiness_source == "early_init"


async def test_startup_records_phase_duration_metrics(hosted, monkeypatch):
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    histogram = provider.get_meter("hosted-agent-startup-tests").create_histogram(
        "kratos.hosted_agent.initialization.duration",
        unit="s",
    )
    monkeypatch.setattr("app.observability.hosted_agent_initialization_duration_histogram", histogram)

    class FakeBlobSkillService:
        is_available = True

        def __init__(self, _settings):
            pass

        async def initialize(self):
            pass

        async def list_use_cases(self):
            return []

        async def seed_from_local(self):
            return []

    class FakeCosmosService:
        def __init__(self, _settings):
            pass

        async def initialize(self):
            pass

    class FakeCopilotAgent:
        def __init__(self, _settings):
            pass

        def set_registries(self, _registries):
            pass

        def set_cosmos_service(self, _cosmos_service):
            pass

        async def start(self):
            pass

    monkeypatch.setattr(
        hosted,
        "get_settings",
        lambda: SimpleNamespace(environment="test", foundry_model_deployment="test-model"),
    )
    monkeypatch.setattr(hosted, "setup_telemetry", lambda _settings: None)
    monkeypatch.setattr(hosted, "BlobSkillService", FakeBlobSkillService)
    monkeypatch.setattr(hosted, "CosmosService", FakeCosmosService)
    monkeypatch.setattr(hosted, "CopilotAgent", FakeCopilotAgent)

    await hosted._startup()

    metrics = reader.get_metrics_data().resource_metrics[0].scope_metrics[0].metrics
    metric = next(metric for metric in metrics if metric.name == "kratos.hosted_agent.initialization.duration")
    points = metric.data.data_points
    assert {point.attributes["phase"] for point in points} == {"telemetry", "core_parallel", "seed"}
    assert all(isinstance(point.attributes["phase"], str) and isinstance(point.sum, (int, float)) for point in points)
    assert all(point.sum >= 0 for point in points)
    provider.shutdown()
