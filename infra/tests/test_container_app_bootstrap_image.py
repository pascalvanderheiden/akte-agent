"""Guard the Container Apps bootstrap image against the ingress port contract.

The placeholder image only listens on port 80, so any Container App that boots
with it while declaring `targetPort: 8000` leaves its first revision stuck in
`ActivationFailed`. The assertion runs on the compiled ARM template rather than
the Bicep source, so it follows parameters and expressions and catches any
module - including one added in the future - that reintroduces the mismatch.
"""

import json
import subprocess
import unittest
from pathlib import Path

CONTAINER_APP_TYPE = "Microsoft.App/containerApps"
PLACEHOLDER_IMAGE = "containerapps-helloworld"
APP_TARGET_PORT = 8000
MODULES = Path(__file__).resolve().parents[1] / "modules"


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
        nested = resource.get("properties", {}).get("template")
        if resource.get("type") == "Microsoft.Resources/deployments" and isinstance(nested, dict):
            yield from iter_resources(nested)


def container_app_modules():
    return sorted(
        module for module in MODULES.glob("*.bicep") if CONTAINER_APP_TYPE in module.read_text()
    )


class ContainerAppBootstrapImageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.modules = container_app_modules()
        cls.templates = {module: compile_template(module) for module in cls.modules}

    def test_modules_with_container_apps_are_discovered(self):
        self.assertTrue(self.modules, "No Container App modules found to validate")

    def test_no_container_app_boots_placeholder_image_on_port_8000(self):
        for module, template in self.templates.items():
            for resource in iter_resources(template):
                if resource.get("type") != CONTAINER_APP_TYPE:
                    continue
                properties = resource.get("properties", {})
                ingress = properties.get("configuration", {}).get("ingress") or {}
                target_port = ingress.get("targetPort")
                # A non-literal port compiles to an ARM expression, so the value is
                # unknown here and the placeholder image stays suspect either way.
                if isinstance(target_port, int) and target_port != APP_TARGET_PORT:
                    continue
                images = [
                    container.get("image") or ""
                    for container in properties.get("template", {}).get("containers", [])
                ]
                placeholders = [image for image in images if PLACEHOLDER_IMAGE in image]
                self.assertEqual(
                    [],
                    placeholders,
                    f"{module.name}: {resource.get('name')} boots the placeholder image, "
                    f"which does not listen on targetPort {target_port!r}, so its first "
                    "revision fails the readiness probe",
                )


if __name__ == "__main__":
    unittest.main()
