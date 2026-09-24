"""Check provisioning order in the compiled ARM template."""

import json
import subprocess
import unittest
from pathlib import Path


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
        environment = app["properties"]["template"]["containers"][0]["env"]
        values = {item["name"]: item["value"] for item in environment}
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
