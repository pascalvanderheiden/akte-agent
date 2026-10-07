import asyncio
import importlib.util
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from azure.core.exceptions import HttpResponseError
from azure.cosmos.exceptions import CosmosHttpResponseError
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from starlette.requests import Request

from app.services.cosmos_service import CosmosService


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
        (
            True,
            None,
            HttpResponseError(response=SimpleNamespace(status_code=403, headers={}, reason="Forbidden")),
            "HttpResponseError",
        ),
    ],
)
@pytest.mark.parametrize("early_init", [False, True])
async def test_startup_blob_local_only_telemetry(
    hosted, monkeypatch, caplog, available, reason, list_error, expected_reason, early_init
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

    class FakeCosmosService(CosmosService):
        def __init__(self, settings):
            pass

        async def initialize(self):
            database = SimpleNamespace(
                read=AsyncMock(side_effect=CosmosHttpResponseError(status_code=403, message="synthetic denial"))
            )
            assert await self._probe_reachable(database)

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
        if early_init:
            monkeypatch.setattr(hosted.app, "run_async", AsyncMock(), raising=False)
            monkeypatch.setattr(hosted, "_shutdown", AsyncMock())
            await hosted._run_host()
        assert await hosted._ensure_shared_core() == ("warm" if early_init else "cold-initialized-by-this-request")

    assert hosted._startup_state == "ready"
    assert hosted._registries == {}
    assert hosted._blob_service.is_available is (expected_reason is None)
    assert "Cosmos reachable but database read refused (status=403)" in caplog.text
    assert not any(record.levelno >= logging.ERROR for record in caplog.records)

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


@pytest.mark.parametrize(
    "failure", ["cosmos", "blob", "copilot", "constructor", "after_init", "cancelled", "telemetry"]
)
@pytest.mark.parametrize("cleanup_fails", [False, True])
async def test_partial_startup_is_cleaned_before_retry(hosted, monkeypatch, failure, cleanup_fails):
    settings = SimpleNamespace(environment="test", foundry_model_deployment="test-model")
    monkeypatch.setattr(hosted, "get_settings", lambda: settings)
    telemetry = Mock(side_effect=RuntimeError("synthetic startup failure") if failure == "telemetry" else None)
    monkeypatch.setattr(hosted, "setup_telemetry", telemetry)
    started = {name: asyncio.Event() for name in ("cosmos", "blob", "copilot")}
    finished = set()
    resources = {}
    failing = True

    def service(name):
        async def initialize():
            started[name].set()
            try:
                await asyncio.gather(*(event.wait() for event in started.values()))
                if failing:
                    if failure == name:
                        raise RuntimeError("synthetic startup failure")
                    if failure == "cancelled" and name == "cosmos":
                        raise asyncio.CancelledError()
                    if failure in started or failure == "cancelled":
                        await asyncio.Event().wait()
            finally:
                finished.add(name)

        async def close():
            if failure != "constructor":
                assert finished == set(started)
            if cleanup_fails and name == "copilot":
                raise RuntimeError("synthetic cleanup failure")

        def set_cosmos_service(value):
            if failing and failure == "after_init":
                raise RuntimeError("synthetic startup failure")

        def construct(value):
            if failing and failure == "constructor" and name == "copilot":
                raise RuntimeError("synthetic startup failure")
            instance = SimpleNamespace(
                initialize=initialize,
                start=initialize,
                close=AsyncMock(side_effect=close),
                stop=AsyncMock(side_effect=close),
                set_registries=Mock(),
                set_cosmos_service=set_cosmos_service,
                is_available=False,
                unavailability_reason=None,
            )
            resources[name] = instance
            return instance

        return construct

    monkeypatch.setattr(hosted, "CosmosService", service("cosmos"))
    monkeypatch.setattr(hosted, "BlobSkillService", service("blob"))
    monkeypatch.setattr(hosted, "CopilotAgent", service("copilot"))
    expected_error = asyncio.CancelledError if failure == "cancelled" else RuntimeError
    with pytest.raises(expected_error):
        await asyncio.wait_for(hosted._ensure_shared_core(readiness_source="early_init"), timeout=2)

    assert hosted._startup_state == "failed"
    assert hosted._copilot_agent is hosted._cosmos_service is hosted._blob_service is None
    assert hosted._settings is None
    if failure not in ("constructor", "telemetry"):
        assert finished == set(started)
    for name, instance in resources.items():
        (instance.stop if name == "copilot" else instance.close).assert_awaited_once()

    old_resources = resources.copy()
    failing = False
    assert await hosted._ensure_shared_core() == "cold-initialized-by-this-request"
    telemetry.assert_called_once_with(settings)
    assert hosted._copilot_agent is resources["copilot"]
    assert hosted._cosmos_service is resources["cosmos"]
    assert hosted._blob_service is resources["blob"]
    await hosted._shutdown()
    for name, instance in resources.items():
        assert instance is not old_resources.get(name)
        (instance.stop if name == "copilot" else instance.close).assert_awaited_once()


async def test_cosmos_close_releases_partial_azure_resources(monkeypatch):
    from app.services import cosmos_service

    credential = SimpleNamespace(close=AsyncMock())
    client = SimpleNamespace(
        get_database_client=Mock(side_effect=RuntimeError("synthetic startup failure")),
        close=AsyncMock(side_effect=RuntimeError("synthetic close failure")),
    )
    monkeypatch.setattr(cosmos_service, "DefaultAzureCredential", lambda: credential)
    monkeypatch.setattr(cosmos_service, "CosmosClient", lambda *args, **kwargs: client)
    service = CosmosService(SimpleNamespace(cosmos_db_endpoint="synthetic", cosmos_db_database="test"))
    with pytest.raises(RuntimeError, match="synthetic startup failure"):
        await service.initialize()
    with pytest.raises(RuntimeError, match="synthetic close failure"):
        await service.close()
    client.close.assert_awaited_once()
    credential.close.assert_awaited_once()
    await service.close()
    client.close.assert_awaited_once()
    credential.close.assert_awaited_once()


@pytest.mark.parametrize("kind", ["blob", "copilot"])
async def test_client_cleanup_failure_still_closes_credential(kind):
    from app.services.blob_skill_service import BlobSkillService
    from app.services.copilot_agent import CopilotAgent

    settings = SimpleNamespace(use_cases_root="use-cases", copilot_model="", foundry_model_deployment="test-model")
    service = BlobSkillService(settings) if kind == "blob" else CopilotAgent(settings)
    credential = SimpleNamespace(close=AsyncMock())
    service._credential = credential
    cleanup = AsyncMock(side_effect=RuntimeError("synthetic close failure"))
    if kind == "blob":
        service._container_client = SimpleNamespace(close=cleanup)
        close = service.close
    else:
        service._client = SimpleNamespace(stop=cleanup)
        close = service.stop
    with pytest.raises(RuntimeError, match="synthetic close failure"):
        await close()
    credential.close.assert_awaited_once()


async def test_early_init_readiness_source_is_retained(hosted, monkeypatch):
    async def startup():
        pass

    monkeypatch.setattr(hosted, "_startup", startup)

    assert await hosted._ensure_shared_core(readiness_source="early_init") == "cold-initialized-by-this-request"
    assert hosted._startup_readiness_source == "early_init"


async def test_process_startup_failure_is_nonfatal_and_request_retries(hosted, monkeypatch, caplog):
    startup = AsyncMock(side_effect=[RuntimeError("synthetic startup failure"), None])
    monkeypatch.setattr(hosted, "_startup", startup)
    shutdown = AsyncMock()
    monkeypatch.setattr(hosted, "_shutdown", shutdown)

    async def serve():
        assert hosted._startup_state == "failed"

        async def receive():
            return {"type": "http.request", "body": b'{"warmup": true}'}

        request = Request({"type": "http", "method": "POST", "path": "/invocations", "headers": []}, receive)
        response = await hosted.handle_invoke(request)
        assert response.status_code == 200
        assert json.loads(response.body)["core_ready_before_ping"] is False
        assert request.state.replica_invocation_state == "cold-initialized-by-this-request"
        assert request.state.readiness_source == "first_invocation_fallback"
        assert await hosted._ensure_shared_core() == "warm"

    monkeypatch.setattr(hosted.app, "run_async", serve, raising=False)
    await hosted._run_host()

    assert startup.await_count == 2
    assert "Early shared-core initialization failed" in caplog.text
    shutdown.assert_awaited_once()


async def test_process_startup_and_serving_share_event_loop(hosted, monkeypatch):
    loop = asyncio.get_running_loop()

    async def startup():
        assert asyncio.get_running_loop() is loop

    async def serve():
        assert asyncio.get_running_loop() is loop
        assert hosted._startup_state == "ready"
        assert hosted._startup_readiness_source == "early_init"
        assert hosted._registries == {}
        raise RuntimeError("synthetic server failure")

    monkeypatch.setattr(hosted, "_startup", startup)
    monkeypatch.setattr(hosted.app, "run_async", serve, raising=False)
    shutdown = AsyncMock()
    monkeypatch.setattr(hosted, "_shutdown", shutdown)

    with pytest.raises(RuntimeError, match="synthetic server failure"):
        await hosted._run_host()
    shutdown.assert_awaited_once()


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
