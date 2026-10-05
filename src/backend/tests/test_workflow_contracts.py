"""Contracts for the issue pipeline and hosted-agent deployment."""

import json
import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("name", ["issue-to-spec", "spec-to-tickets"])
def test_detection_install_has_compiler_pinned_checksums(name):
    workflow = yaml.safe_load((REPO_ROOT / f".github/workflows/{name}.lock.yml").read_text())
    steps = workflow["jobs"]["detection"]["steps"]
    install = next(step["run"] for step in steps if "install_threat_detect_binary.sh" in step.get("run", ""))
    for architecture in ("amd64", "arm64"):
        assert re.search(rf"--sha256-{architecture}\s+[a-f0-9]{{64}}(?:\s|$)", install)


@pytest.mark.parametrize("name", ["issue-to-spec", "spec-to-tickets"])
def test_detection_failure_blocks_all_safe_outputs(name):
    source = (REPO_ROOT / f".github/workflows/{name}.md").read_text()
    frontmatter = yaml.safe_load(source.split("---", 2)[1])
    assert frontmatter["safe-outputs"]["threat-detection"]["continue-on-error"] is False
    workflow = yaml.safe_load((REPO_ROOT / f".github/workflows/{name}.lock.yml").read_text())
    configured = [
        step["env"]["GH_AW_DETECTION_CONTINUE_ON_ERROR"]
        for step in workflow["jobs"]["detection"]["steps"]
        if "GH_AW_DETECTION_CONTINUE_ON_ERROR" in step.get("env", {})
    ]
    assert configured and all(value == "false" for value in configured)


@pytest.mark.parametrize("name", ["issue-to-spec", "spec-to-tickets"])
def test_recovery_report_output_is_exposed(name):
    source = (REPO_ROOT / f".github/workflows/{name}.md").read_text()
    frontmatter = yaml.safe_load(source.split("---", 2)[1])
    assert frontmatter["safe-outputs"]["missing-data"]["max"] == 1

    workflow = (REPO_ROOT / f".github/workflows/{name}.lock.yml").read_text()
    manifest = json.loads(re.search(r"^# gh-aw-manifest: (.+)$", workflow, re.MULTILINE).group(1))
    tools = next(server["tools"] for server in manifest["mcp_servers"] if server["name"] == "safeoutputs")
    assert "missing_data" in tools


def test_hosted_agent_uses_service_level_configuration():
    workflow = yaml.safe_load((REPO_ROOT / "azure.yaml").read_text())
    service = workflow["services"]["kratos-agent"]
    assert "config" not in service
    assert service["container"]["resources"] == {"cpu": "1", "memory": "2Gi"}
    assert service["env"]["COSMOS_DB_ENDPOINT"] == "${AZURE_COSMOS_DB_ENDPOINT}"
    assert service["docker"]["remoteBuild"] is True
