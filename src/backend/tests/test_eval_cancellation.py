"""Regression tests for cooperative eval-run cancellation."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models import EvalMode, EvalRun, EvalRunRequest, EvalRunStatus, EvalScenario
from app.routers import evals
from app.services.eval_service import EvalService


class FakeEvalStorage:
    def __init__(self, run: EvalRun, scenarios: list[EvalScenario]) -> None:
        self.run = run.model_copy(deep=True)
        self.scenarios = scenarios

    async def save_run(self, run: EvalRun) -> None:
        self.run = run.model_copy(deep=True)

    async def load_run(self, use_case: str, run_id: str) -> EvalRun | None:
        if (use_case, run_id) != (self.run.use_case, self.run.run_id):
            return None
        return self.run.model_copy(deep=True)

    async def list_scenarios(self, use_case: str) -> list[EvalScenario]:
        return self.scenarios

    async def append_jsonl(self, use_case: str, run_id: str, record: dict) -> None:
        pass

    async def write_report(self, use_case: str, run_id: str, report: dict) -> None:
        pass


def _scenario(name: str) -> EvalScenario:
    return EvalScenario(
        name=name,
        description=name,
        input_message=f"Run {name}",
        expected_behavior="Complete the task",
    )


def _run() -> EvalRun:
    now = datetime.now(UTC)
    return EvalRun(
        run_id="run-1",
        use_case="demo",
        mode=EvalMode.VALIDATION,
        status=EvalRunStatus.PENDING,
        scenarios=["first", "second"],
        created_at=now,
        updated_at=now,
    )


def test_eval_run_request_does_not_expand_an_omitted_scenario_list() -> None:
    assert EvalRunRequest().scenarios == []


def test_start_run_endpoint_rejects_an_empty_scenario_list() -> None:
    app = FastAPI()
    app.include_router(evals.router, prefix="/api/use-cases")
    service = MagicMock()
    service.start_run = AsyncMock()
    app.state.eval_service = service
    app.state.registries = {"demo": MagicMock()}

    response = TestClient(app).post("/api/use-cases/demo/evals/run", json={})

    assert response.status_code == 400
    assert response.json()["detail"] == "At least one scenario is required"
    service.start_run.assert_not_awaited()


def test_cancel_run_endpoint_returns_the_cancelled_run() -> None:
    app = FastAPI()
    app.include_router(evals.router, prefix="/api/use-cases")
    cancelled = _run().model_copy(update={"status": EvalRunStatus.CANCELLED})
    service = MagicMock()
    service.cancel_run = AsyncMock(return_value=cancelled)
    app.state.eval_service = service
    app.state.registries = {"demo": MagicMock()}

    response = TestClient(app).post("/api/use-cases/demo/evals/runs/run-1/cancel")

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    service.cancel_run.assert_awaited_once_with("demo", "run-1")


@pytest.mark.asyncio
async def test_cancel_run_stops_before_the_next_scenario_and_persists_cancelled() -> None:
    run = _run()
    storage = FakeEvalStorage(run, [_scenario("first"), _scenario("second")])
    service = EvalService(MagicMock(), storage, {"demo": MagicMock()})  # type: ignore[arg-type]
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    invoked: list[str] = []

    async def invoke(message: str, **kwargs):
        if message == "ping":
            return "ready", [], None, []
        invoked.append(message)
        first_started.set()
        await release_first.wait()
        return "done", [], None, []

    service._invoke_hosted_agent = invoke  # type: ignore[method-assign]
    service._score_run = AsyncMock()  # type: ignore[method-assign]

    task = asyncio.create_task(service._execute_run(run))
    service._tasks[(run.use_case, run.run_id)] = task
    service._active_runs[(run.use_case, run.run_id)] = run
    await first_started.wait()

    cancelled = await service.cancel_run(run.use_case, run.run_id)
    assert cancelled is not None
    assert cancelled.status == EvalRunStatus.CANCELLED
    assert run.status == EvalRunStatus.CANCELLED
    assert not task.done()

    release_first.set()
    await task

    persisted = await storage.load_run(run.use_case, run.run_id)
    assert persisted is not None
    assert persisted.status == EvalRunStatus.CANCELLED
    assert invoked == ["Run first"]


@pytest.mark.asyncio
async def test_cancel_during_report_cannot_be_overwritten_by_completed() -> None:
    run = _run().model_copy(update={"scenarios": ["first"]})
    storage = FakeEvalStorage(run, [_scenario("first")])
    service = EvalService(MagicMock(), storage, {"demo": MagicMock()})  # type: ignore[arg-type]
    report_started = asyncio.Event()
    release_report = asyncio.Event()

    async def invoke(message: str, **kwargs):
        return "ready" if message == "ping" else "done", [], None, []

    async def write_report(use_case: str, run_id: str, report: dict) -> None:
        report_started.set()
        await release_report.wait()

    service._invoke_hosted_agent = invoke  # type: ignore[method-assign]
    service._score_run = AsyncMock()  # type: ignore[method-assign]
    storage.write_report = write_report  # type: ignore[method-assign]

    task = asyncio.create_task(service._execute_run(run))
    service._tasks[(run.use_case, run.run_id)] = task
    await report_started.wait()
    await service.cancel_run(run.use_case, run.run_id)
    release_report.set()
    await task

    persisted = await storage.load_run(run.use_case, run.run_id)
    assert persisted is not None
    assert persisted.status == EvalRunStatus.CANCELLED


@pytest.mark.asyncio
async def test_cancellation_persisted_by_another_replica_stops_the_local_run() -> None:
    """A cancel handled elsewhere only shows up as a persisted status."""
    run = _run()
    storage = FakeEvalStorage(run, [_scenario("first"), _scenario("second")])
    service = EvalService(MagicMock(), storage, {"demo": MagicMock()})  # type: ignore[arg-type]
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    invoked: list[str] = []

    async def invoke(message: str, **kwargs):
        if message == "ping":
            return "ready", [], None, []
        invoked.append(message)
        first_started.set()
        await release_first.wait()
        return "done", [], None, []

    service._invoke_hosted_agent = invoke  # type: ignore[method-assign]
    service._score_run = AsyncMock()  # type: ignore[method-assign]

    task = asyncio.create_task(service._execute_run(run))
    await first_started.wait()

    # Simulate the other replica's write — no local task or cancel request.
    storage.run = storage.run.model_copy(update={"status": EvalRunStatus.CANCELLED})
    release_first.set()
    await task

    assert invoked == ["Run first"]
    persisted = await storage.load_run(run.use_case, run.run_id)
    assert persisted is not None
    assert persisted.status == EvalRunStatus.CANCELLED
    service._score_run.assert_not_awaited()
