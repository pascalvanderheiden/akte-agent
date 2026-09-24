"""Dynamic catalog and self-contained Akte export contracts."""

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models import AgentRequest, ConversationCreate
from app.personas import resolve_use_case
from app.routers import use_cases
from app.services.eval_storage import EvalStorage
from app.services.project_exporter import ProjectExporter
from app.services.skill_registry import SkillRegistry
from tests.test_akte_artifacts import time_input

REPO = Path(__file__).resolve().parents[3]


@pytest.mark.asyncio
async def test_clean_catalog_exposes_only_akte_and_defaults_new_work():
    app = FastAPI()
    app.include_router(use_cases.router, prefix="/api/use-cases")
    app.state.registries = {}
    for directory in sorted((REPO / "use-cases").iterdir()):
        if not (directory / "SYSTEM_PROMPT.md").is_file():
            continue
        registry = SkillRegistry()
        await registry.load(directory.name, local_root=str(REPO / "use-cases"))
        app.state.registries[directory.name] = registry
    catalog = TestClient(app).get("/api/use-cases").json()["useCases"]
    assert {item["name"] for item in catalog} == {"akte-agent"}
    akte = catalog[0]
    assert akte["displayName"] == "Akte Agent"
    for locale in ("en", "nl"):
        presentation = akte["localizations"][locale]
        assert presentation["displayName"] and presentation["description"]
        assert len(presentation["sampleQuestions"]) >= 6
        assert all(presentation["sampleQuestions"])
    # New work with an omitted persona selection resolves to Akte; existing
    # conversations must never be silently defaulted onto a different persona.
    assert AgentRequest(conversationId="synthetic", message="Hello").useCase is None
    assert resolve_use_case(AgentRequest(conversationId="synthetic", message="Hello").useCase, None) == "akte-agent"
    assert ConversationCreate(title="synthetic").useCase == "akte-agent"
    names = app.state.registries["akte-agent"].get_enabled_tool_names()
    assert {
        "code_interpreter",
        "web_search",
        "rag_search",
        "email_draft",
        "working_artifacts",
        "notarial_intake",
        "legal_preparation",
        "execution_preparation",
        "reconciliation",
        "billing_handoff",
    } <= names


@pytest.mark.asyncio
async def test_standalone_export_runs_without_other_persona(tmp_path):
    destination = tmp_path / "export"
    destination.mkdir()
    exporter = ProjectExporter(REPO)
    exporter.assemble("akte-agent", destination)
    assert [path.name for path in (destination / "use-cases").iterdir()] == ["akte-agent"]
    registry = SkillRegistry()
    await registry.load("akte-agent", local_root=str(destination / "use-cases"))
    assert {
        "code-interpreter",
        "document-summary",
        "email-draft",
        "file-sharing",
        "notarial-intake",
        "rag-search",
        "web-search",
        "working-artifacts",
        "legal-preparation",
        "execution-preparation",
        "reconciliation",
        "billing-handoff",
    } <= registry.skills.keys()
    assert registry.mcp_servers == {}
    script = destination / "use-cases/akte-agent/skills/working-artifacts/scripts/time_record.py"
    input_path = tmp_path / "input.json"
    input_path.write_text(json.dumps(time_input("nl")))
    process = subprocess.run(
        [sys.executable, str(script), str(input_path), "--export"],
        cwd=destination,
        capture_output=True,
        text=True,
        check=True,
    )
    result = json.loads(process.stdout)
    artifact = Path(result["artifact"]["path"])
    try:
        assert result["hours_display"] == "1,75"
        assert "105 minuten" in artifact.read_text()
    finally:
        artifact.unlink()
    assert (destination / "src/backend/app/locale.py").is_file()
    assert (destination / "src/hosted-agent/main.py").is_file()


@pytest.mark.asyncio
async def test_bilingual_scenarios_load_through_existing_eval_harness():
    storage = EvalStorage(SimpleNamespace(is_available=False), local_base_dir=REPO / "use-cases")
    scenarios = await storage.list_scenarios("akte-agent")
    for stem in ("intake-time", "missing-injection", "time-review", "unavailable"):
        for locale in ("en", "nl"):
            scenario = next(item for item in scenarios if item.name == f"{stem}-{locale}")
            assert scenario.input_data["synthetic"] is True
            assert scenario.expected_behavior
