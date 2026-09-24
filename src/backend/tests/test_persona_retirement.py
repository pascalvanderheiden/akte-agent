"""Cloud-free upgrade fixture: stale catalogs, a custom import and durable history."""

import base64
import json
import shutil
import subprocess
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import main
from app.config import Settings, get_settings
from app.models import Conversation, EvalMode, EvalRun, EvalRunStatus, Message, ScenarioResult
from app.personas import RETIRED_PERSONAS, PersonaUnavailable
from app.routers import (
    admin_analysis,
    admin_mcp,
    admin_prompt,
    admin_skills,
    agent,
    conversations,
    copilot_studio,
    evals,
    export,
    files,
    import_persona,
    use_cases,
)
from app.services.blob_skill_service import BlobSkillService
from app.services.copilot_agent import CopilotAgent
from app.services.foundry_agent_proxy import FoundryAgentProxy
from app.services.project_exporter import ProjectExporter
from app.services.skill_registry import SkillRegistry
from app.services.skill_tools import ALL_TOOLS

REPO = Path(__file__).resolve().parents[3]
PRINCIPAL = base64.b64encode(json.dumps({"userId": "synthetic-admin"}).encode()).decode()


class StoredBlobs:
    def __init__(self, content):
        self.content = content

    async def list_blobs(self, name_starts_with):
        for name in sorted(self.content):
            if name.startswith(name_starts_with):
                yield SimpleNamespace(name=name)

    def get_blob_client(self, name):
        async def upload(content, **kwargs):
            self.content[name] = content

        async def download():
            return SimpleNamespace(readall=AsyncMock(return_value=self.content[name]))

        return SimpleNamespace(upload_blob=upload, download_blob=download)

    async def close(self):
        pass


@pytest.fixture(params=[False, True], ids=["local-fallback", "blob-backed"])
def upgraded(request, monkeypatch, tmp_path):
    root = tmp_path / "use-cases"
    shutil.copytree(REPO / "use-cases", root)
    stale = {}
    for name in RETIRED_PERSONAS:
        directory = root / name
        (directory / "skills/legacy").mkdir(parents=True)
        (directory / "SYSTEM_PROMPT.md").write_text("---\nname: Retired\ncurated: true\n---\nRetired instructions")
        (directory / "skills/legacy/SKILL.md").write_text("---\nname: legacy\n---\nRetired tool")
        stale[f"use-cases/{name}/SYSTEM_PROMPT.md"] = (directory / "SYSTEM_PROMPT.md").read_bytes()
        stale[f"use-cases/{name}/skills/legacy/SKILL.md"] = (directory / "skills/legacy/SKILL.md").read_bytes()
    blobs = StoredBlobs(dict(stale))
    settings = Settings(
        local_mode=True,
        local_data_dir=str(tmp_path / "data"),
        persona_assets_root=str(root),
        keep_warm_enabled=False,
        foundry_agent_invocations_endpoint="http://127.0.0.1:8088/invoke",
        admin_auth_enabled="true",
    )

    async def initialize(service):
        if request.param:
            service._container_client = blobs

    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr(main, "setup_telemetry", lambda settings: None)
    monkeypatch.setattr(BlobSkillService, "initialize", initialize)
    monkeypatch.setattr(FoundryAgentProxy, "start", AsyncMock())
    monkeypatch.setattr(FoundryAgentProxy, "stop", AsyncMock())
    artifact = tmp_path / "historical.txt"
    artifact.write_text("SYNTHETIC saved artifact; unchanged")
    monkeypatch.setattr(files, "_ALLOWED_ROOTS", (str(tmp_path),))

    @asynccontextmanager
    async def lifespan(app):
        async with main.lifespan(app):
            now = datetime.now(UTC)
            await app.state.cosmos_service.upsert_conversation(
                Conversation(
                    id="synthetic-history",
                    userId="default-user",
                    title="Saved history",
                    useCase="insurance",
                    status="active",
                    createdAt=now,
                    updatedAt=now,
                )
            )
            await app.state.cosmos_service.upsert_message(
                Message(
                    id="synthetic-message",
                    conversationId="synthetic-history",
                    role="assistant",
                    content="SYNTHETIC original response",
                    createdAt=now,
                    metadata={"file": str(artifact)},
                )
            )
            await app.state.eval_storage.save_run(
                EvalRun(
                    run_id="synthetic-run",
                    use_case="insurance",
                    mode=EvalMode.VALIDATION,
                    status=EvalRunStatus.COMPLETED,
                    created_at=now,
                    updated_at=now,
                    results=[ScenarioResult(scenario="synthetic-old", query="Original", response="Saved result")],
                )
            )
            yield

    app = FastAPI(lifespan=lifespan)
    for router, prefix in [
        (use_cases, "/api/use-cases"),
        (import_persona, "/api/use-cases"),
        (export, "/api/use-cases"),
        (evals, "/api/use-cases"),
        (conversations, "/api/conversations"),
        (agent, "/api/agent"),
        (copilot_studio, "/api/copilot-studio"),
        (admin_skills, "/api/admin/skills"),
        (admin_prompt, "/api/admin/system-prompt"),
        (admin_mcp, "/api/admin/mcp-servers"),
        (admin_analysis, "/api/admin/analysis"),
        (files, "/api/files"),
    ]:
        app.include_router(router.router, prefix=prefix)
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app, headers={"x-ms-client-principal": PRINCIPAL}) as client:
        yield SimpleNamespace(client=client, app=app, root=root, blobs=blobs, stale=stale, artifact=artifact)


def test_upgrade_hides_stale_definitions_and_preserves_custom_import(upgraded):
    client = upgraded.client
    assert {p["name"] for p in client.get("/api/use-cases").json()["useCases"]} == {"akte-agent"}
    response = client.post(
        "/api/use-cases/import",
        json={
            "manifest": {
                "name": "Synthetic Custom",
                "instructions": "Review synthetic notes.",
            }
        },
    )
    assert response.status_code == 201, response.text
    slug = response.json()["name"]
    assert slug == "synthetic-custom"
    assert client.post("/api/conversations", json={"useCase": slug}).status_code == 201
    assert client.get(f"/api/admin/system-prompt?use_case={slug}").status_code == 200
    # A surviving in-memory entry is not authority to reactivate a retired persona.
    upgraded.app.state.registries.update({name: SkillRegistry(use_case=name) for name in RETIRED_PERSONAS})
    catalog = client.get("/api/use-cases").json()["useCases"]
    assert {p["name"] for p in catalog} == {"akte-agent", slug}
    assert all(upgraded.blobs.content[name] == content for name, content in upgraded.stale.items())


@pytest.mark.parametrize("name", sorted(RETIRED_PERSONAS))
def test_stale_cached_persona_is_denied_at_all_entry_points(upgraded, name):
    upgraded.app.state.registries[name] = SkillRegistry(use_case=name, system_prompt="Retired")
    client = upgraded.client
    chat = {"useCase": name, "conversationId": "synthetic-history", "message": "Do not execute"}
    for method, url, body in [
        ("POST", "/api/conversations", {"useCase": name}),
        ("POST", "/api/agent/chat", chat),
        ("POST", "/api/copilot-studio/chat", chat),
        ("GET", f"/api/use-cases/{name}/export", None),
        ("GET", f"/api/admin/skills?use_case={name}", None),
        ("POST", f"/api/admin/skills?use_case={name}", {"name": "new-skill", "description": "Synthetic"}),
        ("GET", f"/api/admin/system-prompt?use_case={name}", None),
        ("PUT", f"/api/admin/system-prompt?use_case={name}", {"content": "Replace"}),
        ("GET", f"/api/admin/mcp-servers?use_case={name}", None),
        ("PUT", f"/api/admin/mcp-servers?use_case={name}", {"servers": {}}),
        ("POST", f"/api/admin/analysis/consistency?use_case={name}", {}),
        ("POST", f"/api/use-cases/{name}/evals/run", {}),
        ("POST", f"/api/use-cases/{name}/evals/scenarios/generate", {"count": 1}),
        ("POST", "/api/use-cases/import", {"name": name, "manifest": {"name": name}, "overwrite": True}),
    ]:
        response = client.request(method, url, json=body)
        assert response.status_code == 410, (method, url, response.text)
        assert response.json()["detail"]["code"] == "PERSONA_UNAVAILABLE"


def test_history_files_and_evals_survive_denied_execution(upgraded):
    client = upgraded.client
    paths = [
        "/api/conversations/synthetic-history",
        "/api/conversations/synthetic-history/messages",
        "/api/use-cases/insurance/evals/runs",
        "/api/use-cases/insurance/evals/runs/synthetic-run",
    ]
    before = {path: client.get(path).json() for path in paths}
    assert before[paths[1]][0]["content"] == "SYNTHETIC original response"
    assert before[paths[3]]["results"][0]["response"] == "Saved result"
    assert (
        client.post(
            "/api/agent/chat",
            json={
                "conversationId": "synthetic-history",
                "useCase": "generic",
                "message": "Do not switch history",
            },
        ).status_code
        == 410
    )
    assert client.post("/api/use-cases/insurance/evals/run", json={}).status_code == 410
    for path in paths:
        response = client.get(path)
        assert response.status_code == 200
        assert response.json() == before[path]
    download = client.get("/api/files/download/historical.txt", params={"path": str(upgraded.artifact)})
    assert download.status_code == 200
    assert download.text == "SYNTHETIC saved artifact; unchanged"
    for name in RETIRED_PERSONAS:
        assert (upgraded.root / name / "SYSTEM_PROMPT.md").is_file()


def test_import_auth_is_still_required(upgraded):
    response = upgraded.client.post(
        "/api/use-cases/import",
        headers={"x-ms-client-principal": ""},
        json={"manifest": {"name": "Synthetic Unauthenticated"}},
    )
    assert response.status_code == 401


def test_unknown_persona_cannot_create_empty_registry(upgraded):
    response = upgraded.client.post("/api/conversations", json={"useCase": "synthetic-absent"})
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "PERSONA_UNAVAILABLE"
    assert "synthetic-absent" not in upgraded.app.state.registries


async def test_generic_standalone_export_is_retired(tmp_path):
    with pytest.raises(PersonaUnavailable):
        ProjectExporter(REPO).assemble("generic", tmp_path)


def test_explicit_retired_upload_selection_fails_before_cloud_access(tmp_path):
    shutil.copytree(REPO / "hooks", tmp_path / "hooks")
    for name in ("generic", "insurance", "synthetic-custom"):
        folder = tmp_path / "use-cases" / name
        folder.mkdir(parents=True)
        (folder / "SYSTEM_PROMPT.md").write_text("Synthetic")
    result = subprocess.run(
        ["bash", "hooks/postdeploy.sh", "--from-deploy"],
        cwd=tmp_path,
        env={
            "PATH": "/usr/bin:/bin",
            "AZURE_BLOB_STORAGE_ACCOUNT_NAME": "synthetic",
            "KRATOS_UPLOAD_USE_CASES": "insurance",
        },
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 1
    assert "Persona unavailable: insurance" in result.stdout
    assert result.stderr == ""


@pytest.mark.parametrize("name", sorted(RETIRED_PERSONAS))
async def test_direct_load_export_and_cached_sdk_session_cannot_execute(tmp_path, name):
    (tmp_path / name).mkdir()
    (tmp_path / name / "SYSTEM_PROMPT.md").write_text("Stale persona")
    with pytest.raises(PersonaUnavailable):
        await SkillRegistry().load(name, local_root=str(tmp_path))
    with pytest.raises(PersonaUnavailable):
        await BlobSkillService(Settings(), str(tmp_path)).sync_to_local(name)
    blobs = BlobSkillService(Settings(), str(tmp_path))
    for operation in [
        blobs.download_system_prompt(name),
        blobs.list_skill_names(name),
        blobs.upload_skill_file(name, "legacy", "SKILL.md", b"Replace"),
        blobs.upload_skill_folder(name, "legacy", tmp_path),
        blobs.delete_skill(name, "legacy"),
        blobs.delete_skill_file(name, "legacy", "SKILL.md"),
        blobs.upload_mcp_config(name, b"{}"),
    ]:
        with pytest.raises(PersonaUnavailable):
            await operation
    with pytest.raises(PersonaUnavailable):
        ProjectExporter(tmp_path).assemble(name, tmp_path / "export")
    runtime = CopilotAgent(Settings())
    runtime._sessions["synthetic"] = object()
    runtime.set_conversation_use_case("synthetic", name)
    events = [event async for event in runtime.run("No execution", "synthetic")]
    assert len(events) == 1 and events[0].code == "PERSONA_UNAVAILABLE"
    proxy = FoundryAgentProxy(Settings())
    events = [event async for event in proxy.invoke("No execution", "synthetic", use_case=name)]
    assert len(events) == 1 and events[0]["data"]["code"] == "PERSONA_UNAVAILABLE"


def test_upload_selection_matches_runtime_policy():
    for name in [*RETIRED_PERSONAS, "akte-agent", "synthetic-custom"]:
        result = subprocess.run(
            [
                "bash",
                "-c",
                'source "$1"; persona_is_available "$2"',
                "retirement-test",
                str(REPO / "hooks/personas.sh"),
                name,
            ],
            check=False,
        )
        assert result.returncode == (1 if name in RETIRED_PERSONAS else 0)


def test_shared_tools_and_explicit_ingestion_configuration(monkeypatch):
    from app.services import ai_search_tools

    assert {tool.name for tool in ALL_TOOLS} == {"web_search", "rag_search", "code_interpreter", "foundry_agent"}
    monkeypatch.setattr(ai_search_tools, "PDF_INGEST_FOLDER", "")
    assert ai_search_tools.ingest_pdfs("synthetic-index") == {
        "status": "error",
        "message": "Set PDF_INGEST_FOLDER or supply --folder",
    }
