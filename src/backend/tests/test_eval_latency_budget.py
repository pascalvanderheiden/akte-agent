"""Tests for the ADR 0003 interactive-request latency budget in eval runs."""

import asyncio
import re
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.latency_budget import (
    COMPLEX_REQUEST_CEILING_MS,
    COMPLEX_REQUEST_CEILING_S,
    DEFAULT_SCENARIO_BUDGET_MS,
    INTERACTIVE_P50_BUDGET_S,
    INTERACTIVE_P95_BUDGET_MS,
    INTERACTIVE_P95_BUDGET_S,
    scenario_budget_ms,
)
from app.models import EvalMode, EvalRun, EvalRunStatus, EvalScenario
from app.services.eval_service import EvalService

ADR = Path(__file__).resolve().parents[3] / "docs" / "adr" / "0003-interactive-request-latency-budget.md"


class FakeEvalStorage:
    def __init__(self, run: EvalRun, scenarios: list[EvalScenario]) -> None:
        self.run = run.model_copy(deep=True)
        self.scenarios = scenarios
        self.records: list[dict] = []
        self.report: dict = {}

    async def save_run(self, run: EvalRun) -> None:
        self.run = run.model_copy(deep=True)

    async def load_run(self, use_case: str, run_id: str) -> EvalRun | None:
        return self.run.model_copy(deep=True)

    async def list_scenarios(self, use_case: str) -> list[EvalScenario]:
        return self.scenarios

    async def append_jsonl(self, use_case: str, run_id: str, record: dict) -> None:
        self.records.append(record)

    async def write_report(self, use_case: str, run_id: str, report: dict) -> None:
        self.report = report


def _run(scenarios: list[str]) -> EvalRun:
    now = datetime.now(UTC)
    return EvalRun(
        run_id="run-1",
        use_case="demo",
        mode=EvalMode.VALIDATION,
        status=EvalRunStatus.PENDING,
        scenarios=scenarios,
        created_at=now,
        updated_at=now,
    )


def test_unset_scenario_threshold_falls_back_to_the_complex_request_ceiling() -> None:
    assert scenario_budget_ms(None) == DEFAULT_SCENARIO_BUDGET_MS == COMPLEX_REQUEST_CEILING_MS
    assert scenario_budget_ms(INTERACTIVE_P95_BUDGET_MS) == INTERACTIVE_P95_BUDGET_MS


def test_adr_records_the_same_numbers_the_code_enforces() -> None:
    text = ADR.read_text(encoding="utf-8")
    numbers = set(re.findall(r"\*\*(\d+) s\*\*", text))
    assert numbers == {
        str(int(INTERACTIVE_P50_BUDGET_S)),
        str(int(INTERACTIVE_P95_BUDGET_S)),
        str(int(COMPLEX_REQUEST_CEILING_S)),
    }


@pytest.mark.asyncio
async def test_scenario_results_record_the_budget_they_were_judged_against() -> None:
    scenarios = [
        EvalScenario(name="slow", input_message="Run slow", max_duration_ms=1),
        EvalScenario(name="fast", input_message="Run fast"),
    ]
    run = _run(["slow", "fast"])
    storage = FakeEvalStorage(run, scenarios)
    service = EvalService(MagicMock(), storage, {"demo": MagicMock()})  # type: ignore[arg-type]

    async def invoke(message: str, **kwargs):
        if message == "Run slow":
            await asyncio.sleep(0.01)
        return "done", [], None, []

    service._invoke_hosted_agent = invoke  # type: ignore[method-assign]
    service._score_run = AsyncMock()  # type: ignore[method-assign]

    await service._execute_run(run)

    slow, fast = run.results
    assert (slow.latency_budget_ms, slow.latency_budget_exceeded) == (1, True)
    assert (fast.latency_budget_ms, fast.latency_budget_exceeded) == (DEFAULT_SCENARIO_BUDGET_MS, False)
    assert [(r["scenario"], r["latency_budget_exceeded"]) for r in storage.records] == [
        ("slow", True),
        ("fast", False),
    ]
    assert storage.report["scenarios"][0]["latency_budget_ms"] == 1
