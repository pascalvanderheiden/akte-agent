"""Cloud-free assertions against the submitted optional ARM template."""

import json
from typing import Any

import pytest

from tests.test_sre_agent_infra import INFRA, SRE_API_VERSION, _compile, _resources


@pytest.fixture(scope="module")
def telemetry_template() -> dict[str, Any]:
    code, out, err = _compile(INFRA / "sre-telemetry.bicep")
    assert code == 0, err
    return json.loads(out)


def test_optional_declaration_only_creates_connectors_and_query_roles(telemetry_template: dict[str, Any]) -> None:
    resources = telemetry_template["resources"]
    assert {r["type"] for r in resources} == {
        "Microsoft.App/agents/connectors",
        "Microsoft.Authorization/roleAssignments",
    }
    assert len(resources) == 4
    assert telemetry_template["parameters"]["source"]["allowedValues"] == ["app-insights", "log-analytics"]


@pytest.mark.parametrize(
    "source,kind,target,role,scope_type",
    [
        (
            "app-insights",
            "AppInsights",
            "appInsightsResourceId",
            "43d0d8ad-25c7-4714-9337-8ba259a9fe05",
            "Microsoft.Insights/components",
        ),
        (
            "log-analytics",
            "LogAnalytics",
            "logAnalyticsResourceId",
            "73c42c96-874c-492b-b04d-ab87d138a893",
            "Microsoft.OperationalInsights/workspaces",
        ),
    ],
)
def test_source_contract_and_system_identity_query_scope(
    telemetry_template: dict[str, Any], source: str, kind: str, target: str, role: str, scope_type: str
) -> None:
    condition = f"[equals(parameters('source'), '{source}')]"
    connector = next(
        r for r in _resources(telemetry_template, "Microsoft.App/agents/connectors") if r["condition"] == condition
    )
    assert connector["apiVersion"] == SRE_API_VERSION
    assert connector["name"] == f"[format('{{0}}/{{1}}', parameters('agentName'), '{source}')]"
    props = connector["properties"]
    assert props["identity"] == "system"
    assert props["dataConnectorType"] == kind
    assert props["dataSource"] == f"[parameters('{target}')]"
    assert props["extendedProperties"]["armResourceId"] == props["dataSource"]
    assert target in props["extendedProperties"]["resource"]["name"]
    if kind == "AppInsights":
        assert props["extendedProperties"]["appId"] == "[parameters('appInsightsAppId')]"
    else:
        assert "appId" not in props["extendedProperties"]
    assignment = next(
        r
        for r in _resources(telemetry_template, "Microsoft.Authorization/roleAssignments")
        if r["condition"] == condition
    )
    assert scope_type in assignment["scope"]
    assert target in assignment["scope"]
    assert assignment["properties"]["principalId"] == "[parameters('agentPrincipalId')]"
    assert assignment["properties"]["principalType"] == "ServicePrincipal"
    assert role in json.dumps(telemetry_template["variables"])
    role_variable = "monitoringReader" if kind == "AppInsights" else "logAnalyticsReader"
    assert assignment["properties"]["roleDefinitionId"] == (
        f"[subscriptionResourceId('Microsoft.Authorization/roleDefinitions', variables('{role_variable}'))]"
    )
    assert assignment["name"].startswith("[guid(")
    assert "agentPrincipalId" in assignment["name"]
    assert scope_type in assignment["name"]
    assert len(connector["dependsOn"]) == 1
    assert "roleAssignments" in connector["dependsOn"][0]


def test_telemetry_is_not_nested_in_core_provision() -> None:
    code, out, err = _compile(INFRA / "main.bicep")
    assert code == 0, err
    assert "Microsoft.App/agents/connectors" not in out
    assert "73c42c96-874c-492b-b04d-ab87d138a893" not in out
