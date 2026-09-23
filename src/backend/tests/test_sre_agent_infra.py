"""Compiled-ARM contract tests for the opt-in Azure SRE Agent.

These tests compile the Bicep with the local toolchain and assert the ARM
template that would be *submitted* — resource types, API version, identity
model, RBAC scopes, and read-only settings. Nothing here talks to Azure, and
nothing asserts Bicep source text.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

# ``src/backend/tests/test_sre_agent_infra.py`` → parents[3] is the repo root.
REPO_ROOT = Path(__file__).resolve().parents[3]
INFRA = REPO_ROOT / "infra"

SRE_API_VERSION = "2025-05-01-preview"

# Built-in role definition IDs (Azure-wide constants).
ROLE_READER = "acdd72a7-3385-48ef-bd42-f606fba81ae7"
ROLE_MONITORING_READER = "43d0d8ad-25c7-4714-9337-8ba259a9fe05"
ROLE_SRE_AGENT_ADMINISTRATOR = "e79298df-d852-4c6d-84f9-5d13249d1e55"
ROLE_CONTRIBUTOR = "b24988ac-6180-42a0-ab88-20f7382dd24c"
ROLE_OWNER = "8e3af657-a8ff-443c-a75c-2fe8c4bcb635"


def _compile(path: Path) -> tuple[int, str, str]:
    """Compile a Bicep file to ARM JSON on stdout."""
    if shutil.which("bicep"):
        cmd = ["bicep", "build", str(path), "--stdout"]
    elif shutil.which("az"):
        cmd = ["az", "bicep", "build", "--file", str(path), "--stdout"]
    else:  # pragma: no cover - depends on the developer machine
        pytest.skip("neither the bicep CLI nor the Azure CLI is available")
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    return proc.returncode, proc.stdout, proc.stderr


@pytest.fixture(scope="module")
def sre_template() -> dict[str, Any]:
    """The compiled SRE Agent module."""
    code, out, err = _compile(INFRA / "modules" / "sre-agent.bicep")
    assert code == 0, f"sre-agent.bicep did not compile:\n{err}"
    return json.loads(out)


@pytest.fixture(scope="module")
def main_template() -> dict[str, Any]:
    """The compiled top-level template.

    Compiling ``main.bicep`` restores the Microsoft Graph Bicep extension from
    a registry. Where that registry is unreachable the compile cannot run at
    all, so the test is skipped rather than reported as a template defect.
    """
    code, out, err = _compile(INFRA / "main.bicep")
    if code != 0 and ("BCP192" in err or "Unable to restore" in err):
        pytest.skip(f"cannot restore Bicep registry artifacts in this environment:\n{err}")
    assert code == 0, f"main.bicep did not compile:\n{err}"
    return json.loads(out)


def _resources(template: dict[str, Any], resource_type: str) -> list[dict[str, Any]]:
    resources = template["resources"]
    values = resources.values() if isinstance(resources, dict) else resources
    return [r for r in values if r["type"] == resource_type]


def _agent(template: dict[str, Any]) -> dict[str, Any]:
    agents = _resources(template, "Microsoft.App/agents")
    assert len(agents) == 1, "expected exactly one SRE Agent resource"
    return agents[0]


# ---------------------------------------------------------------------------
# Core resource contract
# ---------------------------------------------------------------------------


def test_agent_uses_the_pinned_api_version(sre_template: dict[str, Any]) -> None:
    assert _agent(sre_template)["apiVersion"] == SRE_API_VERSION


def test_agent_is_read_only_and_human_reviewed(sre_template: dict[str, Any]) -> None:
    action = _agent(sre_template)["properties"]["actionConfiguration"]
    assert action["accessLevel"] == "Low"
    assert action["mode"] == "Review"


def test_agent_has_both_user_and_system_assigned_identities(sre_template: dict[str, Any]) -> None:
    identity = _agent(sre_template)["identity"]
    assert identity["type"] == "SystemAssigned, UserAssigned"
    assert len(identity["userAssignedIdentities"]) == 1


def test_discovery_is_scoped_to_this_environments_resource_group(sre_template: dict[str, Any]) -> None:
    graph = _agent(sre_template)["properties"]["knowledgeGraphConfiguration"]
    assert graph["managedResources"] == ["[resourceGroup().id]"]


def test_agent_logs_to_the_existing_component_using_its_app_id(sre_template: dict[str, Any]) -> None:
    """AppId, instrumentation key, and connection string are distinct values."""
    log_config = _agent(sre_template)["properties"]["logConfiguration"]["applicationInsightsConfiguration"]
    assert ".AppId" in log_config["appId"]
    assert ".ConnectionString" in log_config["connectionString"]
    assert "InstrumentationKey" not in json.dumps(log_config)


def test_no_duplicate_monitoring_resources_are_created(sre_template: dict[str, Any]) -> None:
    assert _resources(sre_template, "Microsoft.OperationalInsights/workspaces") == []
    assert _resources(sre_template, "Microsoft.Insights/components") == []


def test_no_remediation_or_scheduling_resources(sre_template: dict[str, Any]) -> None:
    created = {r["type"] for r in _resources(sre_template, "Microsoft.App/agents")}
    all_types = {
        r["type"]
        for r in (
            sre_template["resources"].values()
            if isinstance(sre_template["resources"], dict)
            else sre_template["resources"]
        )
    }
    assert created == {"Microsoft.App/agents"}
    assert all_types == {
        "Microsoft.App/agents",
        "Microsoft.ManagedIdentity/userAssignedIdentities",
        "Microsoft.Authorization/roleAssignments",
    }


# ---------------------------------------------------------------------------
# RBAC contract
# ---------------------------------------------------------------------------


def test_only_read_only_roles_are_assigned(sre_template: dict[str, Any]) -> None:
    body = json.dumps(sre_template)
    assert ROLE_READER in body
    assert ROLE_MONITORING_READER in body
    assert ROLE_SRE_AGENT_ADMINISTRATOR in body
    assert ROLE_CONTRIBUTOR not in body
    assert ROLE_OWNER not in body


def test_role_assignment_names_are_deterministic(sre_template: dict[str, Any]) -> None:
    """Deterministic guids are what make repeat provisioning converge."""
    for assignment in _resources(sre_template, "Microsoft.Authorization/roleAssignments"):
        assert assignment["name"].startswith("[guid(")
        assert "newGuid" not in assignment["name"]


def _admin_assignments(template: dict[str, Any]) -> list[dict[str, Any]]:
    """Assignments that target the agent resource rather than the resource group."""
    return [r for r in _resources(template, "Microsoft.Authorization/roleAssignments") if "scope" in r]


def test_operator_administration_is_scoped_to_the_agent(sre_template: dict[str, Any]) -> None:
    admin = _admin_assignments(sre_template)
    assert len(admin) == 1
    assert "Microsoft.App/agents" in admin[0]["scope"]
    assert admin[0]["condition"] == "[not(empty(parameters('operatorPrincipalId')))]"


def test_operator_principal_type_is_not_hardcoded(sre_template: dict[str, Any]) -> None:
    """A CI service principal and a local user are both supported."""
    admin = _admin_assignments(sre_template)[0]
    assert admin["properties"]["principalType"] == "[parameters('operatorPrincipalType')]"
    allowed = sre_template["parameters"]["operatorPrincipalType"]["allowedValues"]
    assert {"User", "ServicePrincipal"} <= set(allowed)


def test_identity_role_assignments_target_the_managed_identity(sre_template: dict[str, Any]) -> None:
    identity_roles = [
        r for r in _resources(sre_template, "Microsoft.Authorization/roleAssignments") if "scope" not in r
    ]
    # Resource-group scope: no explicit scope means the deployment's own RG.
    assert len(identity_roles) == 2
    for role in identity_roles:
        assert role["properties"]["principalType"] == "ServicePrincipal"
        assert "principalId" in role["properties"]["principalId"]


# ---------------------------------------------------------------------------
# Wiring into the environment's deployment
# ---------------------------------------------------------------------------


def test_sre_is_off_by_default(main_template: dict[str, Any]) -> None:
    param = main_template["parameters"]["deploySreAgent"]
    assert param["type"] == "bool"
    assert param["defaultValue"] is False


def test_sre_module_is_conditional(main_template: dict[str, Any]) -> None:
    deployments = _resources(main_template, "Microsoft.Resources/deployments")
    sre = [d for d in deployments if d["name"] == "sre-agent"]
    assert len(sre) == 1
    assert sre[0]["condition"] == "[parameters('deploySreAgent')]"


def test_sre_reuses_the_environment_monitoring_resources(main_template: dict[str, Any]) -> None:
    sre = [d for d in _resources(main_template, "Microsoft.Resources/deployments") if d["name"] == "sre-agent"][0]
    params = sre["properties"]["parameters"]
    assert "'app-insights'" in json.dumps(params["appInsightsName"])
    assert "'log-analytics'" in json.dumps(params["logAnalyticsWorkspaceId"])


def test_sre_name_and_location_support_overrides(main_template: dict[str, Any]) -> None:
    sre = [d for d in _resources(main_template, "Microsoft.Resources/deployments") if d["name"] == "sre-agent"][0]
    params = sre["properties"]["parameters"]
    # An override collapses to a conditional expression rather than a plain
    # value, so compare against the serialised argument.
    name_arg = json.dumps(params["name"])
    location_arg = json.dumps(params["location"])
    assert "parameters('sreAgentName')" in name_arg
    # Deterministic fallback name, derived from the environment's resource token.
    assert "resourceToken" in name_arg
    assert "parameters('sreLocation')" in location_arg
    assert "parameters('location')" in location_arg
    assert main_template["parameters"]["sreAgentName"]["defaultValue"] == ""
    assert main_template["parameters"]["sreLocation"]["defaultValue"] == ""


def test_sre_outputs_are_exported_and_empty_when_disabled(main_template: dict[str, Any]) -> None:
    outputs = main_template["outputs"]
    for name in (
        "SRE_AGENT_ID",
        "SRE_AGENT_NAME",
        "SRE_AGENT_LOCATION",
        "SRE_AGENT_PRINCIPAL_ID",
        "SRE_AGENT_IDENTITY_ID",
        "SRE_AGENT_PORTAL_URL",
        "SRE_APP_INSIGHTS_APP_ID",
        "SRE_APP_INSIGHTS_ID",
        "SRE_LOG_ANALYTICS_ID",
    ):
        assert name in outputs, f"missing output {name}"
        assert "parameters('deploySreAgent')" in outputs[name]["value"]
        assert "''" in outputs[name]["value"]
    assert outputs["SRE_AGENT_ENABLED"]["value"] == "[parameters('deploySreAgent')]"


def test_no_agent_endpoint_is_constructed_from_a_hostname(main_template: dict[str, Any]) -> None:
    """Endpoints are resolved from Azure, never guessed from committed defaults."""
    body = json.dumps(main_template)
    assert "azuresre.ai" not in body
    assert "azuresre.dev" not in body


def test_parameter_file_defaults_sre_off() -> None:
    params = json.loads((INFRA / "main.parameters.json").read_text())["parameters"]
    assert params["deploySreAgent"]["value"] == "${DEPLOY_SRE_AGENT=false}"
    assert params["sreAgentName"]["value"] == "${SRE_AGENT_NAME_OVERRIDE}"
    assert params["sreLocation"]["value"] == "${SRE_LOCATION}"
    assert params["principalType"]["value"] == "${AZURE_PRINCIPAL_TYPE=User}"
