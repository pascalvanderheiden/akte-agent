"""Cross-stage files in the repo and standalone assembly/authenticated ZIP export."""

import base64
import io
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.routers import export, files
from app.services.project_exporter import ProjectExporter
from app.services.skill_registry import SkillRegistry
from tests.akte_combined_fixture import create_journey

REPO = Path(__file__).resolve().parents[3]
REQUIRED_SKILLS = {
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
}


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["repository", "assemble", "authenticated-zip"])
@pytest.mark.parametrize("locale", ["en", "nl"])
async def test_cross_stage_artifacts_keep_provenance_and_pending_evidence(tmp_path, entry, locale):
    destination = REPO
    registry = SkillRegistry()
    await registry.load("akte-agent", local_root=str(REPO / "use-cases"))
    if entry != "repository":
        destination = tmp_path / "standalone"
        destination.mkdir()
        if entry == "assemble":
            ProjectExporter(REPO).assemble("akte-agent", destination)
        else:
            app = FastAPI()
            app.include_router(export.router, prefix="/api/use-cases")
            app.state.registries = {"akte-agent": registry}
            app.state.blob_skill_service = SimpleNamespace(local_base_dir=REPO / "use-cases")
            app.dependency_overrides[get_settings] = lambda: Settings(admin_auth_enabled="true")
            client = TestClient(app)
            assert client.get("/api/use-cases/akte-agent/export").status_code == 401
            principal = base64.b64encode(
                json.dumps(
                    {
                        "userId": "synthetic-reviewer",
                        "userRoles": ["authenticated"],
                    }
                ).encode()
            ).decode()
            response = client.get(
                "/api/use-cases/akte-agent/export",
                headers={"x-ms-client-principal": principal},
            )
            assert response.status_code == 200
            with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                archive.extractall(destination)
        assert {path.name for path in (destination / "use-cases").iterdir()} == {"akte-agent"}
        for runtime in ("src/backend/app/services/copilot_agent.py", "src/hosted-agent/main.py"):
            assert (destination / runtime).is_file()
        registry = SkillRegistry()
        await registry.load("akte-agent", local_root=str(destination / "use-cases"))
    assert registry.skills.keys() >= REQUIRED_SKILLS
    documents = create_journey(destination / "use-cases/akte-agent", locale)
    by_name = {document["name"]: document for document in documents}
    app = FastAPI()
    app.include_router(files.router, prefix="/api/files")
    try:
        for document in documents:
            path = Path(document["path"])
            response = TestClient(app).get(f"/api/files/download/{path.name}", params={"path": str(path)})
            assert response.content == path.read_bytes() == document["text"].encode()
            assert "SYNTHETIC-AKTE-LEGAL" in document["text"]
            assert ("Status: DRAFT" if locale == "en" else "Status: CONCEPT") in document["text"]
            assert document["receipt"]["artifact"]["storage"] == "temporary"
        assert by_name["time"]["receipt"]["total_minutes"] == "105"
        deed = by_name["deed"]
        execution = by_name["execution"]
        assert execution["input"]["clauses"][0]["excerpt"] == deed["text"]
        assert execution["input"]["clauses"][0]["reference"] == deed["input"]["version"]
        assert execution["receipt"]["review_status"] == "unresolved"
        assert {"approval", "identity", "scanner", "authority"} <= set(execution["receipt"]["missing_evidence"])
        assert execution["input"]["signing_evidence"] == []
        assert ("status unknown" if locale == "en" else "status onbekend") in execution["text"]
        assert ("[UNKNOWN: amount]" if locale == "en" else "[ONBEKEND: bedrag]") in execution["text"]
        assert ("STALE FOR REVIEW" if locale == "en" else "VEROUDERD VOOR BEOORDELING") in execution["text"]
        assert any(source["kind"] == "user_observation" for source in execution["input"]["sources"])
        assert all(source["kind"] != "verified_external" for source in execution["input"]["sources"])
        assert "totals" not in by_name["missing-amount"]["receipt"]
        assert by_name["missing-amount"]["receipt"]["review_status"] == "pending"
        supplied = by_name["supplied-funds"]["receipt"]
        assert supplied["totals"]["balance"] == "0.00"
        assert supplied["review_status"] == "pending" and "payment" in supplied["issues"]
        index = by_name["index"]
        generated = [item for item in index["input"]["items"] if item["availability"] == "generated"]
        assert {item["path"] for item in generated} == {document["path"] for document in documents[:-1]}
        for document in documents[:-1]:
            assert Path(document["path"]).name.replace("_", "\\_") in index["text"]
        assert ("no office system updated" if locale == "en" else "geen kantoorsysteem bijgewerkt") in index["text"]
    finally:
        for document in documents:
            Path(document["path"]).unlink()
