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


def test_hosted_agent_runtime_definition_is_referenced_explicitly():
    # azure.ai.agents >= 1.0.0-beta.18 refuses to infer agent.yaml and fails
    # packaging with "no runtime definition in azure.yaml; found legacy file
    # agent.yaml", which aborts the whole `azd deploy`.
    service = yaml.safe_load((REPO_ROOT / "azure.yaml").read_text())["services"]["kratos-agent"]
    reference = service["$ref"]
    assert (REPO_ROOT / reference).is_file()


def test_agent_yaml_does_not_redeclare_sizing_resources():
    # Under the $ref merge, `resources` is the service schema's list of
    # connected resources. A sizing object here fails to unmarshal; cpu/memory
    # belong on the service's container.resources instead.
    agent = yaml.safe_load((REPO_ROOT / "src/hosted-agent/agent.yaml").read_text())
    assert "resources" not in agent
    assert agent["environment_variables"], "runtime env vars must stay in agent.yaml"


def test_deploy_pins_tenant_into_the_azd_environment():
    # deploy.yml sets AZURE_SUBSCRIPTION_ID explicitly, so azd requires
    # AZURE_TENANT_ID too; without it a federated service principal cannot
    # resolve the ARM endpoint. Provision runs get it from the postprovision
    # hook, deploy-only runs would otherwise never have it.
    workflow = yaml.safe_load((REPO_ROOT / ".github/workflows/deploy.yml").read_text())
    deploy = workflow["jobs"]["deploy"]
    job_env = deploy["env"]
    steps = deploy["steps"]
    assert job_env["AZURE_TENANT_ID"] == "${{ secrets.AZURE_TENANT_ID }}"
    credential_check = next(
        (step for step in steps if step.get("name") == "Check required credentials are configured"),
        None,
    )
    assert credential_check is not None, "workflow must validate required credentials before setup"
    assert "AZURE_TENANT_ID" in credential_check["run"]

    select = next((s for s in steps if s.get("name") == "Select azd environment"), None)
    assert select is not None, "workflow must select an azd environment"
    assert steps.index(credential_check) < steps.index(select)
    assert "azd env set AZURE_TENANT_ID" in select["run"]
    for step in steps:
        run = step.get("run", "")
        if any(cmd in run for cmd in ("azd deploy", "azd provision", "azd env refresh")):
            assert "AZURE_TENANT_ID" in job_env, step.get("name")


def test_deploy_pins_validated_azd_toolchain_versions():
    project = yaml.safe_load((REPO_ROOT / "azure.yaml").read_text())
    assert project["requiredVersions"]["extensions"]["azure.ai.agents"] == "1.0.0-beta.18"

    workflow = yaml.safe_load((REPO_ROOT / ".github/workflows/deploy.yml").read_text())
    steps = workflow["jobs"]["deploy"]["steps"]
    setup = next(step for step in steps if step.get("name") == "Install azd")
    assert setup["with"]["version"] == "1.35.0"
    extension = next(step for step in steps if step.get("name") == "Install azd extensions")
    assert "--version 1.0.0-beta.18" in extension["run"]


def test_smoke_telemetry_query_uses_compatible_azure_cli_filters():
    workflow = yaml.safe_load((REPO_ROOT / ".github/workflows/deploy.yml").read_text())
    steps = workflow["jobs"]["integration-test"]["steps"]
    resolve = next(step for step in steps if step.get("name") == "Resolve smoke telemetry resource")
    command = resolve["run"]

    assert '--resource-group "rg-$AZURE_ENV_NAME"' in command
    assert "--resource-type" not in command
    assert "microsoft.insights/components" in command
