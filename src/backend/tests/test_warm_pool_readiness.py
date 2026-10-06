"""Warm-pool startup readiness and lifecycle tests."""

import asyncio
from contextlib import suppress

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from app import observability
from app.config import Settings
from app.main import _run_initial_warmup, _start_warm_pool_tasks, _stop_warm_pool_tasks
from app.routers.health import router as health_router


class FakeProxy:
    def __init__(self, state: str = "warming", pool_size: int = 0, pool_target: int = 2) -> None:
        self.warmup_state = state
        self.warm_pool_size = pool_size
        self.warm_pool_target = pool_target
        self.maintain_warm_pool = None

    def set_warmup_state(self, state: str) -> None:
        self.warmup_state = state


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(health_router)
    proxy = FakeProxy()
    app.state.foundry_proxy = proxy
    return TestClient(app), proxy


@pytest.fixture
def metric_reader(monkeypatch):
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    meter = provider.get_meter("warm-pool-tests")
    monkeypatch.setattr(
        observability,
        "warm_pool_initial_warmup_duration_histogram",
        meter.create_histogram("kratos.warm_pool.initial_warmup.duration", unit="s"),
    )
    monkeypatch.setattr(
        observability,
        "warm_pool_target_histogram",
        meter.create_histogram("kratos.warm_pool.target", unit="{sandbox}"),
    )
    monkeypatch.setattr(
        observability,
        "warm_pool_available_histogram",
        meter.create_histogram("kratos.warm_pool.available", unit="{sandbox}"),
    )
    yield reader
    provider.shutdown()


def _metric_datapoint(reader, name):
    metrics = reader.get_metrics_data().resource_metrics[0].scope_metrics[0].metrics
    metric = next(metric for metric in metrics if metric.name == name)
    return metric.data.data_points[0]


def test_readiness_transitions_and_liveness_is_unchanged(client):
    client, proxy = client

    assert client.get("/health/ready").status_code == 503
    assert client.get("/health/ready").json() == {"status": "warming"}
    assert client.get("/health").json() == {
        "status": "healthy",
        "service": "kratos-agent-service",
    }

    proxy.set_warmup_state("ready")
    assert client.get("/health/ready").status_code == 200
    assert client.get("/health/ready").json() == {"status": "ready"}

    proxy.set_warmup_state("degraded")
    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "degraded"
    assert "isolated sandboxes" in response.json()["detail"]


@pytest.mark.asyncio
async def test_initial_warmup_records_full_pool_metrics_and_marks_ready(client, metric_reader, caplog, monkeypatch):
    endpoint_client, endpoint_proxy = client
    proxy = FakeProxy()
    caplog.set_level("INFO")
    timestamps = iter((10.0, 12.5))
    monkeypatch.setattr("app.main.perf_counter", lambda: next(timestamps))

    async def maintain():
        return (2, 2)

    proxy.maintain_warm_pool = maintain

    await _run_initial_warmup(proxy, timeout_s=45)

    assert proxy.warmup_state == "ready"
    endpoint_proxy.set_warmup_state(proxy.warmup_state)
    assert endpoint_client.get("/health/ready").json() == {"status": "ready"}

    duration = _metric_datapoint(metric_reader, "kratos.warm_pool.initial_warmup.duration")
    target = _metric_datapoint(metric_reader, "kratos.warm_pool.target")
    available = _metric_datapoint(metric_reader, "kratos.warm_pool.available")
    assert duration.sum == 2.5
    assert target.sum == available.sum == 2
    assert duration.attributes == target.attributes == available.attributes == {"fallback_used": False}
    assert "outcome=succeeded duration_s=2.500 pool_target=2 pool_available=2 fallback_used=False" in caplog.text


@pytest.mark.asyncio
async def test_initial_warmup_marks_partial_fill_degraded(client, metric_reader, caplog):
    endpoint_client, endpoint_proxy = client
    proxy = FakeProxy(pool_size=1)

    async def maintain():
        return (1, 2)

    proxy.maintain_warm_pool = maintain

    await _run_initial_warmup(proxy, timeout_s=45)

    assert proxy.warmup_state == "degraded"
    assert proxy.warm_pool_size == 1
    duration = _metric_datapoint(metric_reader, "kratos.warm_pool.initial_warmup.duration")
    target = _metric_datapoint(metric_reader, "kratos.warm_pool.target")
    available = _metric_datapoint(metric_reader, "kratos.warm_pool.available")
    assert duration.count == 1 and duration.sum >= 0
    assert target.sum == 2
    assert available.sum == 1
    assert duration.attributes == target.attributes == available.attributes == {"fallback_used": True}
    assert "outcome=partial" in caplog.text
    assert "pool_target=2 pool_available=1 fallback_used=True" in caplog.text
    endpoint_proxy.set_warmup_state(proxy.warmup_state)
    assert endpoint_client.get("/health/ready").json()["status"] == "degraded"


@pytest.mark.asyncio
async def test_initial_warmup_timeout_is_degraded_and_retains_partial_pool(monkeypatch, client, metric_reader, caplog):
    endpoint_client, endpoint_proxy = client
    proxy = FakeProxy(pool_size=1)
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def maintain():
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    proxy.maintain_warm_pool = maintain

    async def timeout(awaitable, *, timeout):
        task = asyncio.ensure_future(awaitable)
        await started.wait()
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        raise TimeoutError

    monkeypatch.setattr("app.main.asyncio.wait_for", timeout)
    await _run_initial_warmup(proxy, timeout_s=45)

    assert cancelled.is_set()
    assert proxy.warmup_state == "degraded"
    assert proxy.warm_pool_size == 1
    duration = _metric_datapoint(metric_reader, "kratos.warm_pool.initial_warmup.duration")
    target = _metric_datapoint(metric_reader, "kratos.warm_pool.target")
    available = _metric_datapoint(metric_reader, "kratos.warm_pool.available")
    assert duration.count == 1 and duration.sum >= 0
    assert target.sum == 2
    assert available.sum == 1
    assert duration.attributes == target.attributes == available.attributes == {"fallback_used": True}
    assert "outcome=timed_out" in caplog.text
    assert "pool_target=2 pool_available=1 fallback_used=True" in caplog.text
    endpoint_proxy.set_warmup_state(proxy.warmup_state)
    assert endpoint_client.get("/health/ready").json()["status"] == "degraded"


@pytest.mark.asyncio
async def test_initial_warmup_exception_records_failure_metrics(client, metric_reader, caplog):
    endpoint_client, endpoint_proxy = client
    proxy = FakeProxy()

    async def maintain():
        raise RuntimeError("dependency unavailable")

    proxy.maintain_warm_pool = maintain

    await _run_initial_warmup(proxy, timeout_s=45)

    assert proxy.warmup_state == "degraded"
    duration = _metric_datapoint(metric_reader, "kratos.warm_pool.initial_warmup.duration")
    target = _metric_datapoint(metric_reader, "kratos.warm_pool.target")
    available = _metric_datapoint(metric_reader, "kratos.warm_pool.available")
    assert duration.count == 1 and duration.sum >= 0
    assert target.sum == 2
    assert available.sum == 0
    assert duration.attributes == target.attributes == available.attributes == {"fallback_used": True}
    assert "outcome=failed" in caplog.text
    assert "pool_target=2 pool_available=0 fallback_used=True" in caplog.text
    endpoint_proxy.set_warmup_state(proxy.warmup_state)
    assert endpoint_client.get("/health/ready").json()["status"] == "degraded"


@pytest.mark.asyncio
async def test_initial_warmup_telemetry_failure_does_not_break_readiness(monkeypatch):
    proxy = FakeProxy()

    async def maintain():
        return (2, 2)

    def fail_telemetry(*_args):
        raise RuntimeError("metrics unavailable")

    proxy.maintain_warm_pool = maintain
    monkeypatch.setattr("app.main.record_initial_warmup_metrics", fail_telemetry)

    await _run_initial_warmup(proxy, timeout_s=45)

    assert proxy.warmup_state == "ready"


@pytest.mark.parametrize(
    "settings",
    [
        Settings(local_mode=False, keep_warm_enabled=False),
        Settings(local_mode=True, keep_warm_enabled=True),
        Settings(local_mode=False, keep_warm_enabled=True, warm_pool_size=0),
    ],
    ids=["keep-warm-disabled", "local-mode", "zero-target"],
)
@pytest.mark.asyncio
async def test_warm_pool_tasks_are_skipped_when_not_applicable(settings):
    proxy = FakeProxy()

    assert _start_warm_pool_tasks(proxy, settings) == (None, None)
    assert proxy.warmup_state == "ready"


@pytest.mark.asyncio
async def test_shutdown_cancels_in_flight_initial_warmup():
    proxy = FakeProxy()
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def maintain():
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    proxy.maintain_warm_pool = maintain
    settings = Settings(local_mode=False, keep_warm_enabled=True, warm_pool_size=1)
    warmup_task, keep_warm_task = _start_warm_pool_tasks(proxy, settings)
    assert warmup_task is not None
    assert keep_warm_task is not None

    await started.wait()
    await _stop_warm_pool_tasks(warmup_task, keep_warm_task)

    assert cancelled.is_set()
    assert proxy.warmup_state == "degraded"
    assert warmup_task.done()
    assert keep_warm_task.done()


def test_warmup_timeout_default_scales_with_pool_and_is_bounded():
    assert Settings(warm_pool_size=2).effective_warm_pool_warmup_timeout_s == 37
    assert Settings(warm_pool_size=20).effective_warm_pool_warmup_timeout_s == 90
    assert Settings(warm_pool_size=2, warm_pool_warmup_timeout_s=60).effective_warm_pool_warmup_timeout_s == 60
