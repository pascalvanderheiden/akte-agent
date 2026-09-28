"""Guard the Container Apps bootstrap image against the ingress port contract.

A Container App's first revision is created before azd has built the
application image, so the template renders a stand-in. That stand-in has to
listen on the ingress `targetPort` or the revision never passes its readiness
probe (#64), and every later provision has to hand the deployed application
image back rather than reverting to the stand-in.

Both are decisions the compiled template makes at deploy time, so the
assertions below resolve the compiled expressions (see `arm_template`) for each
scenario instead of matching their text.
"""

import json
import re
import subprocess
import unittest
from pathlib import Path

from arm_template import Evaluator

CONTAINER_APP_TYPE = "Microsoft.App/containerApps"
DEPLOYMENT_TYPE = "Microsoft.Resources/deployments"
INFRA = Path(__file__).resolve().parents[1]

# Images known to serve only port 80, so they can never back a different
# ingress target port. This is the combination #64 was filed for.
PORT_80_ONLY_REPOSITORIES = ("containerapps-helloworld",)

# Environment variables a stand-in image may use to pick its listening port.
PORT_ENV_VARS = ("ASPNETCORE_HTTP_PORTS", "ASPNETCORE_URLS", "PORT")

DEPLOYED_APPLICATION_IMAGE = "examplecontainerregistry.azurecr.io/agent-service:deadbeef"
LEGACY_PLACEHOLDER_IMAGE = "mcr.microsoft.com/azuredocs/containerapps-helloworld:latest"


def compile_template(module):
    return json.loads(
        subprocess.check_output(
            ["az", "bicep", "build", "--file", str(module), "--stdout"],
            text=True,
        )
    )


def reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def iter_resources(template):
    """Top-level resources of one template; nested deployments are walked separately.

    Resources inside a `Microsoft.Resources/deployments` template are evaluated
    against that module's own parameters, so `resolve_container_apps` descends
    into them with a matching evaluator rather than flattening them here.
    """

    resources = template.get("resources", [])
    if isinstance(resources, dict):
        resources = list(resources.values())
    return resources


def container_app_modules():
    return sorted(
        module for module in INFRA.rglob("*.bicep") if CONTAINER_APP_TYPE in module.read_text()
    )


def build_evaluator(template, exists, deployed_image, parameters=None):
    """Evaluate `template` as if `exists`/`deployed_image` described the live app."""

    parameters = dict(parameters or {})
    if "exists" in (template.get("parameters") or {}):
        parameters.setdefault("exists", exists)

    evaluator = Evaluator(template, parameters)

    def resolve_reference(resource_id):
        if resource_id.resource_type == CONTAINER_APP_TYPE:
            # `reference()` on the existing app yields its deployed properties.
            return {"template": {"containers": [{"image": deployed_image}]}}
        if resource_id.resource_type == DEPLOYMENT_TYPE:
            nested = find_deployment(evaluator, resource_id.segments[-1])
            module = build_evaluator(
                nested["properties"]["template"],
                exists,
                deployed_image,
                nested_parameters(evaluator, nested),
            )
            return {
                "outputs": {name: {"value": module.output(name)} for name in module.template["outputs"]}
            }
        raise AssertionError(f"Unexpected reference to {resource_id.resource_type}")

    evaluator.reference_resolver = resolve_reference
    return evaluator


def nested_parameters(evaluator, deployment):
    return {
        name: evaluator.evaluate(value["value"])
        for name, value in (deployment["properties"].get("parameters") or {}).items()
    }


def find_deployment(evaluator, name):
    for resource in iter_resources(evaluator.template):
        if resource.get("type") != DEPLOYMENT_TYPE:
            continue
        if evaluator.evaluate(resource["name"]) == name:
            return resource
    raise AssertionError(f"No nested deployment named {name}")


class ResolvedContainerApp:
    """A Container App resource with its image, port and env resolved."""

    def __init__(self, module, evaluator, resource):
        self.module = module
        properties = resource.get("properties") or {}
        ingress = (properties.get("configuration") or {}).get("ingress") or {}
        self.name = evaluator.evaluate(resource["name"])
        self.target_port = evaluator.evaluate(ingress.get("targetPort"))
        self.containers = evaluator.evaluate((properties.get("template") or {}).get("containers", []))

    def env(self, container):
        return {variable["name"]: variable.get("value") for variable in container.get("env", [])}

    def listens_on_target_port(self, container):
        port = str(self.target_port)
        # `:8000` must not satisfy a target port of 800, so match it as a whole number.
        delimited = re.compile(rf"(?<!\d):{re.escape(port)}(?!\d)")
        return any(
            str(value) == port or delimited.search(str(value))
            for name, value in self.env(container).items()
            if name in PORT_ENV_VARS
        )


def resolve_container_apps(module, template, exists, deployed_image="", evaluator=None):
    """Every Container App in `template`, including nested modules, fully resolved."""

    if evaluator is None:
        evaluator = build_evaluator(template, exists, deployed_image)
    apps = []
    for resource in iter_resources(template):
        if resource.get("type") == CONTAINER_APP_TYPE:
            apps.append(ResolvedContainerApp(module, evaluator, resource))
            continue
        nested = (resource.get("properties") or {}).get("template")
        if resource.get("type") != DEPLOYMENT_TYPE or not isinstance(nested, dict):
            continue
        nested_evaluator = build_evaluator(
            nested, exists, deployed_image, nested_parameters(evaluator, resource)
        )
        apps.extend(
            resolve_container_apps(module, nested, exists, deployed_image, nested_evaluator)
        )
    return apps


class ContainerAppBootstrapImageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.modules = container_app_modules()
        cls.templates = {module: compile_template(module) for module in cls.modules}
        cls.bootstrap_template = compile_template(INFRA / "modules" / "container-app-image.bicep")

    def apps(self, exists, deployed_image=""):
        for module, template in self.templates.items():
            for app in resolve_container_apps(module, template, exists, deployed_image):
                for container in app.containers:
                    yield app, container

    def assertNotPortEightyOnly(self, app, container):
        for repository in PORT_80_ONLY_REPOSITORIES:
            self.assertNotIn(
                repository,
                container["image"],
                f"{app.module.name}: {container['image']} listens on port 80, but the app's "
                f"ingress targetPort is {app.target_port}, so its revision cannot pass readiness",
            )

    def test_modules_with_container_apps_are_discovered(self):
        self.assertTrue(
            list(self.apps(exists=False)),
            "No compiled Container App resources found to validate",
        )

    def test_first_provision_images_listen_on_the_ingress_target_port(self):
        for app, container in self.apps(exists=False):
            with self.subTest(module=app.module.name, container=container.get("name")):
                self.assertIsInstance(
                    app.target_port,
                    int,
                    f"{app.module.name}: ingress targetPort must resolve to a port number",
                )
                self.assertNotPortEightyOnly(app, container)
                self.assertTrue(
                    app.listens_on_target_port(container),
                    f"{app.module.name}: nothing tells {container['image']} to listen on "
                    f"targetPort {app.target_port}",
                )

    def test_first_provision_uses_the_shared_bootstrap_image(self):
        bootstrap_image = self.bootstrap_default_image()
        for app, container in self.apps(exists=False):
            with self.subTest(module=app.module.name, container=container.get("name")):
                self.assertEqual(
                    bootstrap_image,
                    container["image"],
                    f"{app.module.name}: a first provision must render the shared bootstrap "
                    f"image, not {container['image']}",
                )

    def test_deployed_application_images_are_preserved(self):
        for app, container in self.apps(exists=True, deployed_image=DEPLOYED_APPLICATION_IMAGE):
            with self.subTest(module=app.module.name, container=container.get("name")):
                self.assertEqual(
                    DEPLOYED_APPLICATION_IMAGE,
                    container["image"],
                    f"{app.module.name}: provisioning must not overwrite the deployed image",
                )
                for name in self.bootstrap_env_vars():
                    self.assertNotIn(
                        name,
                        app.env(container),
                        f"{app.module.name}: bootstrap-only {name} must not leak into the "
                        "deployed application image",
                    )

    def test_placeholder_images_are_replaced_on_the_next_provision(self):
        for app, container in self.apps(exists=True, deployed_image=LEGACY_PLACEHOLDER_IMAGE):
            with self.subTest(module=app.module.name, container=container.get("name")):
                self.assertNotPortEightyOnly(app, container)
                self.assertTrue(
                    app.listens_on_target_port(container),
                    f"{app.module.name}: nothing tells the replacement image "
                    f"{container['image']} to listen on targetPort {app.target_port}",
                )

    def bootstrap_default_image(self):
        return Evaluator(self.bootstrap_template).parameters["bootstrapImage"]

    def bootstrap_env_vars(self):
        """Names the bootstrap module injects, read from its compiled output."""

        evaluator = Evaluator(self.bootstrap_template, {"exists": False})
        return [variable["name"] for variable in evaluator.output("bootstrapEnv")]

    def test_bootstrap_default_image_is_not_a_port_80_placeholder(self):
        image = self.bootstrap_default_image()
        for repository in PORT_80_ONLY_REPOSITORIES:
            self.assertNotIn(
                repository,
                image,
                f"The bootstrap default {image} cannot serve a non-80 ingress targetPort",
            )

    def test_azd_resource_exists_flags_preserve_deployed_images(self):
        parameters = json.loads(
            (INFRA / "main.parameters.json").read_text(),
            object_pairs_hook=reject_duplicate_keys,
        )["parameters"]
        self.assertEqual(
            "${SERVICE_AGENT_SERVICE_RESOURCE_EXISTS=false}",
            parameters["agentServiceExists"]["value"],
        )
        self.assertEqual(
            "${SERVICE_OBO_MCP_SERVER_RESOURCE_EXISTS=false}",
            parameters["oboMcpServerExists"]["value"],
        )


if __name__ == "__main__":
    unittest.main()
