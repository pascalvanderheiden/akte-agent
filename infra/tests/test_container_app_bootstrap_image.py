"""Guard the Container Apps bootstrap image against the ingress port contract."""

import json
import subprocess
import unittest
from pathlib import Path

CONTAINER_APP_TYPE = "Microsoft.App/containerApps"
INFRA = Path(__file__).resolve().parents[1]


def compile_template(module):
    return json.loads(
        subprocess.check_output(
            ["az", "bicep", "build", "--file", str(module), "--stdout"],
            text=True,
        )
    )


def iter_resources(template):
    resources = template.get("resources", [])
    if isinstance(resources, dict):
        resources = list(resources.values())
    for resource in resources:
        yield resource
        nested = (resource.get("properties") or {}).get("template")
        if resource.get("type") == "Microsoft.Resources/deployments" and isinstance(nested, dict):
            yield from iter_resources(nested)


def container_app_modules():
    return sorted(
        module for module in INFRA.rglob("*.bicep") if CONTAINER_APP_TYPE in module.read_text()
    )


class ContainerAppBootstrapImageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.modules = container_app_modules()
        cls.templates = {module: compile_template(module) for module in cls.modules}
        cls.bootstrap_template = compile_template(INFRA / "modules" / "container-app-image.bicep")

    def test_modules_with_container_apps_are_discovered(self):
        self.assertTrue(self.modules, "No Container App modules found to validate")

    def test_container_apps_use_the_shared_port_aware_bootstrap_module(self):
        for module, template in self.templates.items():
            for resource in iter_resources(template):
                if resource.get("type") != CONTAINER_APP_TYPE:
                    continue
                properties = resource.get("properties") or {}
                containers = (properties.get("template") or {}).get("containers", [])
                if not containers:
                    continue
                image = containers[0]["image"]
                environment = containers[0]["env"]
                self.assertIn(
                    "outputs.image.value",
                    image,
                    f"{module.name}: Container App must obtain its image from the bootstrap module",
                )
                self.assertIn("outputs.bootstrapEnv.value", environment)

    def test_bootstrap_module_configures_its_listener_from_target_port(self):
        environment = self.bootstrap_template["outputs"]["bootstrapEnv"]["value"]
        self.assertIn("ASPNETCORE_HTTP_PORTS", environment)
        self.assertIn("ASPNETCORE_URLS", environment)
        self.assertIn("parameters('targetPort')", environment)
        self.assertEqual(
            "mcr.microsoft.com/azuredocs/containerapps-helloworld",
            self.bootstrap_template["variables"]["legacyBootstrapRepository"],
        )


if __name__ == "__main__":
    unittest.main()
