"""Regression tests for cooperative eval-run cancellation."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from app.models import EvalMode, EvalRun, EvalRunRequest, EvalRunStatus, EvalScenario
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


def test_eval_run_request_requires_an_explicit_non_empty_scenario_list() -> None:
    with pytest.raises(ValidationError):
        EvalRunRequest()
    with pytest.raises(ValidationError):
        EvalRunRequest(scenarios=[])


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
    service._score_run = MagicMock()  # type: ignore[method-assign]

    task = asyncio.create_task(service._execute_run(run))
    service._tasks[(run.use_case, run.run_id)] = task
    await first_started.wait()

    cancelled = await service.cancel_run(run.use_case, run.run_id)
    assert cancelled is not None
    assert cancelled.status == EvalRunStatus.CANCELLED
    assert not task.done()

    release_first.set()
    await task

    persisted = await storage.load_run(run.use_case, run.run_id)
    assert persisted is not None
    assert persisted.status == EvalRunStatus.CANCELLED
    assert invoked == ["Run first"]
