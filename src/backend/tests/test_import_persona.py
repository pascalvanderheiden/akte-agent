"""Tests for the persona import API (``POST /api/use-cases/import``)."""

import json
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path: Path):
    """TestClient with a local-only BlobSkillService and an empty registry map."""
    from app.config import Settings
    from app.main import app
    from app.services.blob_skill_service import BlobSkillService

    blob_service = BlobSkillService(Settings(), local_base_dir=str(tmp_path / "use-cases"))
    app.state.blob_skill_service = blob_service
    app.state.registries = {}

    yield TestClient(app, raise_server_exceptions=False)

    app.dependency_overrides.clear()


def _manifest(**overrides) -> dict:
    base = {
        "name": "Synthetic Review Bot",
        "description": "Reviews synthetic documents and flags missing evidence.",
        "instructions": "You are a synthetic document assistant. Be precise and cite supplied sources.",
        "sampleQuestions": ["Review synthetic document 12345", "What is missing from synthetic document 999?"],
        "skills": [
            {
                "name": "evidence-check",
                "description": "Check supplied evidence",
            },
            {"name": "no-package-skill", "description": "metadata only"},
        ],
        "mcpServers": [{"name": "microsoft-learn", "transport": "http", "url": "https://learn.microsoft.com/api/mcp"}],
        "traits": ["analysis", "validation"],
        "workflow_model": "agent",
    }
    base.update(overrides)
    return base


def test_import_manifest_creates_persona(client, tmp_path):
    resp = client.post("/api/use-cases/import", json={"manifest": _manifest()})
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["name"] == "synthetic-review-bot"
    assert data["displayName"] == "Synthetic Review Bot"
    assert data["created"] is True
    assert data["files"]

    # Persona registered live
    from app.main import app

    assert "synthetic-review-bot" in app.state.registries

    # Files written to the local mirror
    uc_dir = tmp_path / "use-cases" / "synthetic-review-bot"
    assert (uc_dir / "SYSTEM_PROMPT.md").exists()
    assert (uc_dir / ".mcp.json").exists()
    assert not (uc_dir / "apm.yml").exists()


def test_system_prompt_frontmatter_mapping(client, tmp_path):
    client.post("/api/use-cases/import", json={"manifest": _manifest()})
    text = (tmp_path / "use-cases" / "synthetic-review-bot" / "SYSTEM_PROMPT.md").read_text()
    assert text.startswith("---\n")
    fm_block = text.split("---\n", 2)[1]
    fm = yaml.safe_load(fm_block)
    assert fm["name"] == "Synthetic Review Bot"
    assert fm["description"].startswith("Reviews synthetic documents")
    assert fm["curated"] is True
    assert "Review synthetic document 12345" in fm["sampleQuestions"]
    assert fm["traits"] == ["analysis", "validation"]
    assert fm["workflow_model"] == "agent"
    assert fm["skills"] == _manifest()["skills"]
    # Instructions become the body
    assert "synthetic document assistant" in text.split("---\n", 2)[2]


def test_import_preserves_localized_persona_metadata(client, tmp_path):
    manifest = _manifest(
        localizations={
            "en": {
                "displayName": "Synthetic Review Bot",
                "description": "Reviews synthetic documents.",
                "sampleQuestions": ["Review synthetic document 12345"],
            },
            "nl": {
                "displayName": "Documentbeoordelaar",
                "description": "Beoordeelt synthetische documenten.",
                "sampleQuestions": ["Beoordeel synthetisch document 12345"],
            },
        }
    )
    response = client.post("/api/use-cases/import", json={"manifest": manifest})
    assert response.status_code == 201, response.text

    text = (tmp_path / "use-cases" / "synthetic-review-bot" / "SYSTEM_PROMPT.md").read_text()
    frontmatter = yaml.safe_load(text.split("---\n", 2)[1])
    assert frontmatter["localizations"]["nl"]["displayName"] == "Documentbeoordelaar"
    assert frontmatter["localizations"]["en"]["sampleQuestions"] == ["Review synthetic document 12345"]


def test_direct_mcp_mapping(client, tmp_path):
    response = client.post("/api/use-cases/import", json={"manifest": _manifest()})
    assert response.status_code == 201
    uc_dir = tmp_path / "use-cases" / "synthetic-review-bot"

    mcp = json.loads((uc_dir / ".mcp.json").read_text())
    assert mcp["microsoft-learn"]["url"] == "https://learn.microsoft.com/api/mcp"
    assert mcp["microsoft-learn"]["type"] == "http"


def test_direct_mcp_with_legacy_registry_metadata_is_preserved(client, tmp_path):
    response = client.post(
        "/api/use-cases/import",
        json={"manifest": _manifest(mcpServers=[{"name": "tools", "registry": True, "url": "https://example.test/mcp"}])},
    )
    assert response.status_code == 201, response.text
    mcp = json.loads((tmp_path / "use-cases" / "synthetic-review-bot" / ".mcp.json").read_text())
    assert mcp["tools"] == {"type": "http", "url": "https://example.test/mcp"}


def test_direct_command_mcp_mapping(client, tmp_path):
    response = client.post(
        "/api/use-cases/import",
        json={
            "manifest": _manifest(
                mcpServers=[{"name": "local-tools", "transport": "stdio", "command": "python", "args": ["server.py"]}]
            )
        },
    )
    assert response.status_code == 201, response.text
    mcp = json.loads((tmp_path / "use-cases" / "synthetic-review-bot" / ".mcp.json").read_text())
    # Command-backed servers always map to the runtime's "local" type — the
    # manifest's MCP-spec "stdio" transport hint is not a runnable Kratos type.
    assert mcp["local-tools"] == {"type": "local", "command": "python", "args": ["server.py"]}


def test_remote_mcp_with_unsupported_transport_is_rejected(client, tmp_path):
    response = client.post(
        "/api/use-cases/import",
        json={
            "manifest": _manifest(
                mcpServers=[{"name": "tools", "transport": "local", "url": "https://example.test/mcp"}]
            )
        },
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "UNSUPPORTED_MCP_TRANSPORT"
    assert not (tmp_path / "use-cases" / "synthetic-review-bot").exists()


def test_package_dependent_import_is_rejected(client, tmp_path):
    manifest = _manifest(
        mcpServers=[
            {"name": "tools", "transport": "http", "registry": True},
            {"name": "microsoft-learn", "transport": "http", "url": "https://learn.microsoft.com/api/mcp"},
        ]
    )
    resp = client.post("/api/use-cases/import", json={"manifest": manifest})
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "UNSUPPORTED_PACKAGE_DEPENDENCY"
    assert not (tmp_path / "use-cases" / "synthetic-review-bot").exists()


def test_import_dedupes_slug(client):
    first = client.post("/api/use-cases/import", json={"manifest": _manifest()})
    second = client.post("/api/use-cases/import", json={"manifest": _manifest()})
    assert first.json()["name"] == "synthetic-review-bot"
    assert second.status_code == 201
    assert second.json()["name"] == "synthetic-review-bot-2"


def test_import_overwrite_replaces(client):
    client.post("/api/use-cases/import", json={"manifest": _manifest()})
    resp = client.post(
        "/api/use-cases/import",
        json={"manifest": _manifest(description="updated"), "overwrite": True},
    )
    assert resp.status_code == 201
    assert resp.json()["name"] == "synthetic-review-bot"
    assert resp.json()["created"] is False


def test_conflict_when_no_dedupe_no_overwrite(client):
    client.post("/api/use-cases/import", json={"manifest": _manifest()})
    resp = client.post(
        "/api/use-cases/import",
        json={"manifest": _manifest(), "dedupe": False},
    )
    assert resp.status_code == 409


def test_requires_exactly_one_source(client):
    both = client.post(
        "/api/use-cases/import",
        json={"manifest": _manifest(), "prompt": "make a bot"},
    )
    assert both.status_code == 422
    neither = client.post("/api/use-cases/import", json={})
    assert neither.status_code == 422


def test_auth_enforced_when_enabled(client):
    from app.config import Settings, get_settings
    from app.main import app

    app.dependency_overrides[get_settings] = lambda: Settings(admin_auth_enabled="true")
    resp = client.post("/api/use-cases/import", json={"manifest": _manifest()})
    assert resp.status_code == 401


def test_partial_translation_keeps_missing_fields_optional_in_catalog(client):
    response = client.post(
        "/api/use-cases/import",
        json={
            "manifest": _manifest(localizations={"nl": {"displayName": "Synthetische naam"}}),
        },
    )
    assert response.status_code == 201
    catalog = client.get("/api/use-cases")
    assert catalog.status_code == 200
    persona = catalog.json()["useCases"][0]
    assert persona["localizations"] == {"nl": {"displayName": "Synthetische naam"}}
    assert persona["description"] == _manifest()["description"]


def test_invalid_prompt_localization_is_rejected_without_losing_previous_metadata(client):
    response = client.post("/api/use-cases/import", json={"manifest": _manifest()})
    slug = response.json()["name"]
    url = f"/api/admin/system-prompt?use_case={slug}"
    before = client.get(url).json()
    invalid = "---\nlocalizations:\n  fr:\n    displayName: Nope\n---\n\nInstructions"
    assert client.put(url, json={"content": invalid}).status_code == 422
    assert client.get(url).json() == before


def test_catalog_accepts_inert_curated_metadata(client):
    from app.main import app
    from app.services.skill_registry import SkillRegistry

    root = Path(__file__).parents[3]
    registry = SkillRegistry()
    registry.system_prompt = (root / "use-cases/akte-agent/SYSTEM_PROMPT.md").read_text()
    app.state.registries = {"akte-agent": registry}
    persona = client.get("/api/use-cases").json()["useCases"][0]
    assert persona["curated"] is True
    for locale in ("en", "nl"):
        localized = persona["localizations"][locale]
        assert localized["displayName"]
        assert localized["description"]
        assert len(localized["sampleQuestions"]) == len(persona["sampleQuestions"]) > 0
    assert persona["displayName"] == persona["localizations"]["en"]["displayName"]
