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
        cls.resources = {resource["type"]: resource for resource in template["resources"]}

    def test_model_waits_for_account(self):
        model = self.resources["Microsoft.CognitiveServices/accounts/deployments"]
        self.assertIn(
            "[resourceId('Microsoft.CognitiveServices/accounts', parameters('name'))]",
            model.get("dependsOn", []),
        )

    def test_project_waits_for_model(self):
        project = self.resources["Microsoft.CognitiveServices/accounts/projects"]
        self.assertIn(
            "[resourceId('Microsoft.CognitiveServices/accounts/deployments', "
            "parameters('name'), parameters('modelDeploymentName'))]",
            project.get("dependsOn", []),
            "Parallel model/project writes can lock the account and fail with RequestConflict",
        )

    def test_connection_waits_for_project(self):
        connection = self.resources["Microsoft.CognitiveServices/accounts/projects/connections"]
        self.assertIn(
            "[resourceId('Microsoft.CognitiveServices/accounts/projects', "
            "parameters('name'), parameters('projectName'))]",
            connection.get("dependsOn", []),
        )


if __name__ == "__main__":
    unittest.main()
