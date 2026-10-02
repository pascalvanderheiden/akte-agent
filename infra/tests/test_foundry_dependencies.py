"""Check provisioning order in the compiled ARM template."""

import json
import re
import subprocess
import unittest
from pathlib import Path
from typing import Any

# Start of an env entry inside a compiled ARM expression; the value
# expression that follows is read off by balancing parentheses.
ARM_ENV_ENTRY = re.compile(r"createObject\('name', '([^']+)', 'value', ")


def _arm_value(expression: str, start: int) -> str:
    """Read one value expression out of `expression`, starting at `start`."""
    depth = 0
    quoted = False
    for index in range(start, len(expression)):
        character = expression[index]
        if character == "'":
            quoted = not quoted
        elif quoted:
            continue
        elif character == "(":
            depth += 1
        elif character == ")":
            if depth == 0:
                return expression[start:index]
            depth -= 1
    raise AssertionError(f"unterminated env value at offset {start}")


def container_env(container: dict[str, Any]) -> dict[str, str]:
    """Return the container's env as a name -> value mapping.

    A literal Bicep array compiles to a list of objects, but an array built
    with `concat()` compiles to a single ARM expression string, so the
    name/value pairs have to be read back out of that expression.
    """
    environment = container["env"]
    if isinstance(environment, list):
        return {item["name"]: item["value"] for item in environment}
    values: dict[str, str] = {}
    for match in ARM_ENV_ENTRY.finditer(environment):
        value = _arm_value(environment, match.end())
        values[match.group(1)] = (
            value[1:-1] if value.startswith("'") and value.endswith("'") else f"[{value}]"
        )
    # Fail loudly rather than dropping an entry whose shape we cannot read.
    assert len(values) == environment.count("createObject('name'"), environment
    return values



class FoundryDependenciesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        module = Path(__file__).resolve().parents[1] / "modules" / "ai-services.bicep"
        template = json.loads(
            subprocess.check_output(
                ["az", "bicep", "build", "--file", str(module), "--stdout"],
                text=True,
            )
        )
        cls.template = template
        cls.resources = template["resources"]
        cls.deployments = [
            resource
            for resource in cls.resources
            if resource["type"] == "Microsoft.CognitiveServices/accounts/deployments"
        ]

    def deployment(self, parameter_name):
        expected_name = f"[format('{{0}}/{{1}}', parameters('name'), parameters('{parameter_name}'))]"
        return next(resource for resource in self.deployments if resource["name"] == expected_name)

    def test_model_waits_for_account(self):
        self.assertEqual(3, len(self.deployments))
        for model in self.deployments:
            self.assertIn(
                "[resourceId('Microsoft.CognitiveServices/accounts', parameters('name'))]",
                model.get("dependsOn", []),
            )

    def test_deployments_are_parameterised_and_serial(self):
        parameters = self.template["parameters"]
        for role in ("orchestrator", "fast", "deepReasoning"):
            for suffix in ("DeploymentName", "ModelName", "ModelVersion", "ModelCapacity"):
                self.assertIn(f"{role}{suffix}", parameters)
        self.assertEqual("gpt-6-luna", parameters["orchestratorDeploymentName"]["defaultValue"])
        self.assertEqual("gpt-6-luna", parameters["orchestratorModelName"]["defaultValue"])
        self.assertEqual("gpt-6-sol", parameters["deepReasoningDeploymentName"]["defaultValue"])
        self.assertEqual("gpt-6-sol", parameters["deepReasoningModelName"]["defaultValue"])
        self.assertEqual("gpt-6-astra", parameters["fastDeploymentName"]["defaultValue"])
        self.assertEqual("gpt-6-astra", parameters["fastModelName"]["defaultValue"])

        deep = self.deployment("deepReasoningDeploymentName")
        self.assertIn(
            "[resourceId('Microsoft.CognitiveServices/accounts/deployments', "
            "parameters('name'), parameters('orchestratorDeploymentName'))]",
            deep["dependsOn"],
        )
        fast = self.deployment("fastDeploymentName")
        self.assertIn(
            "[resourceId('Microsoft.CognitiveServices/accounts/deployments', "
            "parameters('name'), parameters('deepReasoningDeploymentName'))]",
            fast["dependsOn"],
        )

    def test_project_waits_for_last_model(self):
        project = next(
            resource
            for resource in self.resources
            if resource["type"] == "Microsoft.CognitiveServices/accounts/projects"
        )
        self.assertIn(
            "[resourceId('Microsoft.CognitiveServices/accounts/deployments', "
            "parameters('name'), parameters('fastDeploymentName'))]",
            project.get("dependsOn", []),
            "Parallel model/project writes can lock the account and fail with RequestConflict",
        )

    def test_role_outputs_and_legacy_alias(self):
        outputs = self.template["outputs"]
        self.assertEqual(
            {
                "orchestratorModelDeployment",
                "fastModelDeployment",
                "deepReasoningModelDeployment",
            },
            {
                name
                for name in outputs
                if name in {
                    "orchestratorModelDeployment",
                    "fastModelDeployment",
                    "deepReasoningModelDeployment",
                }
            },
        )
        self.assertEqual(
            outputs["orchestratorModelDeployment"]["value"],
            outputs["modelDeploymentName"]["value"],
        )

    def test_no_gpt_54_remains(self):
        rendered = json.dumps(self.template)
        self.assertNotIn("gpt-54", rendered)
        self.assertNotIn("gpt-5.4", rendered)

    def test_connection_waits_for_project(self):
        connection = next(
            resource
            for resource in self.resources
            if resource["type"] == "Microsoft.CognitiveServices/accounts/projects/connections"
        )
        self.assertIn(
            "[resourceId('Microsoft.CognitiveServices/accounts/projects', "
            "parameters('name'), parameters('projectName'))]",
            connection.get("dependsOn", []),
        )


class ApplicationInsightsAlertTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        module = Path(__file__).resolve().parents[1] / "modules" / "app-insights.bicep"
        cls.template = json.loads(
            subprocess.check_output(
                ["az", "bicep", "build", "--file", str(module), "--stdout"],
                text=True,
            )
        )
        cls.alert = next(
            resource
            for resource in cls.template["resources"]
            if resource["type"] == "Microsoft.Insights/scheduledQueryRules"
        )

    def test_cosmos_persistence_alert_targets_application_insights(self):
        self.assertEqual(
            ["[resourceId('Microsoft.Insights/components', parameters('name'))]"],
            self.alert["properties"]["scopes"],
        )

    def test_cosmos_persistence_alert_is_narrow_and_requires_repeated_failures(self):
        properties = self.alert["properties"]
        self.assertTrue(properties["enabled"])
        self.assertEqual("PT5M", properties["evaluationFrequency"])
        criterion = properties["criteria"]["allOf"][0]

        self.assertEqual("PT15M", properties["windowSize"])
        self.assertEqual("GreaterThan", criterion["operator"])
        self.assertEqual(2, criterion["threshold"])
        self.assertIn("traces", criterion["query"])
        self.assertIn("exceptions", criterion["query"])
        self.assertIn("operation_Id", criterion["query"])
        self.assertIn("| summarize timestamp = min(timestamp) by operation_Id", criterion["query"])
        self.assertIn("| summarize count() by bin(timestamp, 15m)", criterion["query"])
        hosted_agent = (Path(__file__).resolve().parents[2] / "src" / "hosted-agent" / "main.py").read_text()
        for signature in (
            "Failed to persist user message to Cosmos (non-fatal)",
            "Failed to persist assistant message to Cosmos (non-fatal)",
        ):
            self.assertIn(signature, criterion["query"])
            self.assertIn(signature, hosted_agent)

    def test_cosmos_persistence_alert_matches_the_firewall_denial_signature(self):
        """The 2026-09-28 fault produced 403 exception rows, not warning traces.

        A traces-only query never saw them, so the alert has to search
        `exceptions` as well and carry the denial signatures emitted by the
        Cosmos service itself.
        """
        query = self.alert["properties"]["criteria"]["allOf"][0]["query"]
        cosmos_service = (
            Path(__file__).resolve().parents[2] / "src" / "backend" / "app" / "services" / "cosmos_service.py"
        ).read_text()

        self.assertIn("exceptions", query)
        self.assertIn("CosmosHttpResponseError", query)
        for signature in (
            "Cosmos persistence denied by network rules (firewall)",
            "Cosmos persistence denied by RBAC role assignment",
            "Cosmos persistence denied (unclassified 403)",
            "Cosmos persistence operation timed out",
        ):
            self.assertIn(signature, query)
            self.assertIn(signature, cosmos_service)


class AgentServiceModelEnvironmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        module = Path(__file__).resolve().parents[1] / "modules" / "agent-service.bicep"
        cls.template = json.loads(
            subprocess.check_output(
                ["az", "bicep", "build", "--file", str(module), "--stdout"],
                text=True,
            )
        )

    def test_all_model_roles_and_legacy_alias_are_in_container_environment(self):
        app = next(
            resource
            for resource in self.template["resources"]
            if resource["type"] == "Microsoft.App/containerApps"
        )
        values = container_env(app["properties"]["template"]["containers"][0])
        self.assertEqual(
            "[parameters('orchestratorModelDeployment')]",
            values["MODEL_DEPLOYMENT_ORCHESTRATOR"],
        )
        self.assertEqual(
            "[parameters('deepReasoningModelDeployment')]",
            values["MODEL_DEPLOYMENT_DEEP_REASONING"],
        )
        self.assertEqual(
            "[parameters('fastModelDeployment')]",
            values["MODEL_DEPLOYMENT_FAST"],
        )
        self.assertEqual(
            "[parameters('foundryModelDeployment')]",
            values["FOUNDRY_MODEL_DEPLOYMENT"],
        )

    def test_main_wires_role_outputs_into_agent_service_and_azd_outputs(self):
        main = (Path(__file__).resolve().parents[1] / "main.bicep").read_text()
        for role in ("ORCHESTRATOR", "DEEP_REASONING", "FAST"):
            self.assertIn(f"output MODEL_DEPLOYMENT_{role} string", main)
        self.assertIn(
            "orchestratorModelDeployment: aiFoundry.outputs.orchestratorModelDeployment",
            main,
        )
        self.assertIn(
            "deepReasoningModelDeployment: aiFoundry.outputs.deepReasoningModelDeployment",
            main,
        )
        self.assertIn(
            "fastModelDeployment: aiFoundry.outputs.fastModelDeployment",
            main,
        )

    def test_azd_and_hosted_manifests_pass_every_role_and_legacy_alias(self):
        root = Path(__file__).resolve().parents[2]
        files = (
            root / "azure.yaml",
            root / "src" / "hosted-agent" / "agent.yaml",
            root / "src" / "hosted-agent" / "agent.manifest.yaml",
        )
        for path in files:
            content = path.read_text()
            for role in ("ORCHESTRATOR", "DEEP_REASONING", "FAST"):
                self.assertIn(f"MODEL_DEPLOYMENT_{role}", content, str(path))
            self.assertIn("MODEL_DEPLOYMENT_NAME", content, str(path))


if __name__ == "__main__":
    unittest.main()
