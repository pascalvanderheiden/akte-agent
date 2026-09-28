"""Compiled-ARM contract tests for the Container Apps' bootstrap image.

Both `host: containerapp` services declare `targetPort: 8000`, but their first
revision is created before `azd deploy` has built any application image. A
hardcoded stand-in image that listens on some other port (the classic
`containerapps-helloworld`, hardcoded to port 80) leaves that first revision
stuck in `ActivationFailed`, and re-rendering a stand-in on a later provision
would replace a healthy application image with it.

These tests compile the Bicep with the local toolchain and assert the ARM
template that would be *submitted*. Nothing here talks to Azure.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

# ``src/backend/tests/test_container_app_bootstrap_infra.py`` → repo root.
REPO_ROOT = Path(__file__).resolve().parents[3]
INFRA = REPO_ROOT / "infra"
MODULES = INFRA / "modules"

# The shared mechanism both container app modules must route their image through.
IMAGE_MODULE = "container-app-image.bicep"

# The port every container app in this template serves on.
TARGET_PORT = 8000

# Port-80-only stand-ins: rendering one of these against `targetPort: 8000` is
# the regression this file exists to catch.
PORT_80_IMAGES = (
    "mcr.microsoft.com/azuredocs/containerapps-helloworld",
    "mcr.microsoft.com/k8se/quickstart",
    "mcr.microsoft.com/azuredocs/aci-helloworld",
)

SERVICE_MODULES = ("agent-service.bicep", "obo-mcp-server.bicep")


def _compile(path: Path) -> tuple[int, str, str]:
    """Compile a Bicep file to ARM JSON on stdout."""
    if shutil.which("bicep"):
        cmd = ["bicep", "build", str(path), "--stdout"]
    elif shutil.which("az"):
        cmd = ["az", "bicep", "build", "--file", str(path), "--stdout"]
    else:  # pragma: no cover - depends on the developer machine
        pytest.fail("Install the Bicep CLI or Azure CLI to run the cloud-free ARM contract tests.")
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    return proc.returncode, proc.stdout, proc.stderr


def _template(module: str) -> dict[str, Any]:
    code, out, err = _compile(MODULES / module)
    assert code == 0, f"{module} did not compile:\n{err}"
    return json.loads(out)


@pytest.fixture(scope="module")
def service_templates() -> dict[str, dict[str, Any]]:
    """The compiled container app modules, keyed by file name."""
    return {module: _template(module) for module in SERVICE_MODULES}


@pytest.fixture(scope="module")
def image_template() -> dict[str, Any]:
    """The compiled shared image-resolution module."""
    return _template(IMAGE_MODULE)


def _resources(template: dict[str, Any], resource_type: str) -> list[dict[str, Any]]:
    resources = template["resources"]
    values = resources.values() if isinstance(resources, dict) else resources
    return [r for r in values if r["type"] == resource_type]


def _container_app(template: dict[str, Any]) -> dict[str, Any]:
    apps = _resources(template, "Microsoft.App/containerApps")
    assert len(apps) == 1, "expected exactly one Container App per service module"
    return apps[0]


def _target_port(template: dict[str, Any]) -> int:
    """The ingress target port, resolving the variable the module renders it from."""
    port = _container_app(template)["properties"]["configuration"]["ingress"]["targetPort"]
    if isinstance(port, str):
        name = port.removeprefix("[variables('").removesuffix("')]")
        return int(template["variables"][name])
    return int(port)


def _image_deployment(template: dict[str, Any]) -> dict[str, Any]:
    """The nested deployment of the shared image module."""
    nested = [
        d
        for d in _resources(template, "Microsoft.Resources/deployments")
        if "image" in d["properties"]["template"].get("outputs", {})
    ]
    assert len(nested) == 1, "expected exactly one shared image-resolution deployment"
    return nested[0]


# ---------------------------------------------------------------------------
# Both container app modules
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("module", SERVICE_MODULES)
def test_no_port_80_placeholder_image_is_rendered(module: str, service_templates: dict[str, Any]) -> None:
    body = json.dumps(service_templates[module])
    for image in PORT_80_IMAGES:
        assert image not in body, f"{module} renders {image}, which cannot answer on port {TARGET_PORT}"


@pytest.mark.parametrize("module", SERVICE_MODULES)
def test_ingress_still_targets_port_8000(module: str, service_templates: dict[str, Any]) -> None:
    assert _target_port(service_templates[module]) == TARGET_PORT


@pytest.mark.parametrize("module", SERVICE_MODULES)
def test_image_comes_from_the_shared_module(module: str, service_templates: dict[str, Any]) -> None:
    """Neither module may hardcode an image; both go through the same mechanism."""
    template = service_templates[module]
    container = _container_app(template)["properties"]["template"]["containers"][0]
    deployment_name = _image_deployment(template)["name"]
    for field in ("image", "env"):
        expression = json.dumps(container[field])
        assert "reference(resourceId('Microsoft.Resources/deployments'" in expression, (
            f"{module} does not resolve the container {field} from the shared image module"
        )
        assert deployment_name.strip("[]") in expression


@pytest.mark.parametrize("module", SERVICE_MODULES)
def test_shared_module_is_fed_this_apps_name_and_ingress_port(module: str, service_templates: dict[str, Any]) -> None:
    """The bootstrap listener port cannot drift from the declared ingress port."""
    template = service_templates[module]
    params = _image_deployment(template)["properties"]["parameters"]
    assert params["containerAppName"]["value"] == "[parameters('name')]"
    assert params["exists"]["value"] == "[parameters('exists')]"
    port = params["targetPort"]["value"]
    name = port.removeprefix("[variables('").removesuffix("')]")
    assert template["variables"][name] == TARGET_PORT
    ingress = _container_app(template)["properties"]["configuration"]["ingress"]["targetPort"]
    assert ingress == port, "ingress and bootstrap listener must read the same value"


@pytest.mark.parametrize("module", SERVICE_MODULES)
def test_single_revision_mode_is_unchanged(module: str, service_templates: dict[str, Any]) -> None:
    configuration = _container_app(service_templates[module])["properties"]["configuration"]
    assert configuration["activeRevisionsMode"] == "Single"


def test_both_modules_use_an_identical_mechanism(service_templates: dict[str, Any]) -> None:
    """A fix to one module cannot be forgotten in the other."""
    deployments = {
        module: _image_deployment(template)["properties"]["template"] for module, template in service_templates.items()
    }
    first, reference = next(iter(deployments.items()))
    for module, deployment in deployments.items():
        assert deployment == reference, f"{module} resolves its image differently from {first}"


@pytest.mark.parametrize("module", SERVICE_MODULES)
def test_module_explains_the_bootstrap_image_strategy(module: str) -> None:
    """A comment so an incident responder does not 'fix' this back to a placeholder."""
    source = (MODULES / module).read_text()
    assert IMAGE_MODULE in source
    comments = [line.strip() for line in source.splitlines() if line.strip().startswith(("//", "/*", "*"))]
    assert any(IMAGE_MODULE in line for line in comments), (
        f"{module} does not explain why its image comes from {IMAGE_MODULE}"
    )


@pytest.mark.parametrize("module", SERVICE_MODULES)
def test_no_command_override_is_rendered(module: str, service_templates: dict[str, Any]) -> None:
    """`azd deploy` swaps only the image, so a command set here would outlive
    the bootstrap image and run against the application image."""
    container = _container_app(service_templates[module])["properties"]["template"]["containers"][0]
    assert "command" not in container
    assert "args" not in container


def test_exists_flags_are_fed_from_azd() -> None:
    """azd records these once a service image has been deployed."""
    params = json.loads((INFRA / "main.parameters.json").read_text())["parameters"]
    assert params["agentServiceExists"]["value"] == "${SERVICE_AGENT_SERVICE_RESOURCE_EXISTS=false}"
    assert params["oboMcpServerExists"]["value"] == "${SERVICE_OBO_MCP_SERVER_RESOURCE_EXISTS=false}"
    # Unrelated gating must stay exactly as it was.
    assert params["deployObo"]["value"] == "${DEPLOY_OBO=true}"


# ---------------------------------------------------------------------------
# The shared image-resolution module
# ---------------------------------------------------------------------------


def test_bootstrap_image_serves_http_on_the_requested_port(image_template: dict[str, Any]) -> None:
    bootstrap_env = image_template["outputs"]["bootstrapEnv"]["value"]
    names = ("ASPNETCORE_HTTP_PORTS", "ASPNETCORE_URLS")
    for name in names:
        assert name in bootstrap_env
    # One reference per variable: neither may hardcode a port of its own.
    assert bootstrap_env.count("parameters('targetPort')") >= len(names), (
        f"the bootstrap listening port must come from the ingress target port: {bootstrap_env}"
    )
    default_image = image_template["parameters"]["bootstrapImage"]["defaultValue"]
    assert default_image.split("/")[0] == "mcr.microsoft.com"
    # Digest-pinned: servicing tags are republished in place, so only a digest
    # makes first-provision behaviour reproducible.
    assert re.fullmatch(r"[^@:]+@sha256:[0-9a-f]{64}", default_image), (
        f"{default_image} is not pinned to an image digest"
    )
    assert default_image.split("@", 1)[0] not in PORT_80_IMAGES


def test_a_deployed_application_image_is_read_back_and_preserved(image_template: dict[str, Any]) -> None:
    """Provisioning an already-deployed app is a no-op for its running image."""
    image = image_template["outputs"]["image"]["value"]
    assert "reference(resourceId('Microsoft.App/containerApps', parameters('containerAppName'))" in image
    assert ".template.containers[0].image" in image
    assert "parameters('bootstrapImage')" in image


def test_a_read_back_bootstrap_image_still_counts_as_bootstrapping(image_template: dict[str, Any]) -> None:
    """A provision whose deploy never ran leaves the stand-in image on the app;
    rendering it again without its port variables would fail activation."""
    for output in ("image", "bootstrapEnv"):
        value = image_template["outputs"][output]["value"]
        assert "startsWith(" in value and "bootstrapRepository" in value, (
            f"output {output} does not treat a read-back bootstrap image as still bootstrapping"
        )


def test_the_bootstrap_repository_is_parsed_from_a_digest_reference(image_template: dict[str, Any]) -> None:
    """Classification strips the digest, so re-pinning cannot mistake an older
    stand-in image for an application image."""
    repository = image_template["variables"]["bootstrapRepository"]
    # The digest must be stripped first: splitting on ':' alone would cut a
    # `repo@sha256:…` reference in half and never match a read-back stand-in.
    assert re.search(
        r"split\(\s*split\(\s*parameters\('bootstrapImage'\)\s*,\s*'@'\)\[0\]\s*,\s*':'\)\[0\]",
        repository,
    ), f"bootstrapRepository does not strip a digest before a tag: {repository}"


def test_the_existing_app_is_only_read_when_it_exists(image_template: dict[str, Any]) -> None:
    """ARM cannot read a resource that may not exist, so the read is gated."""
    for output in ("image", "bootstrapEnv", "isBootstrap"):
        value = image_template["outputs"][output]["value"]
        assert "if(parameters('exists')" in value, f"output {output} reads the app unconditionally"


def test_no_container_app_is_created_by_the_shared_module(image_template: dict[str, Any]) -> None:
    assert _resources(image_template, "Microsoft.App/containerApps") == []


# ---------------------------------------------------------------------------
# Azure-side detection of the `exists` flags
# ---------------------------------------------------------------------------

DETECT_HOOK = REPO_ROOT / "hooks" / "detect-container-apps.sh"

AZ_STUB = """#!/usr/bin/env bash
case "$1 $2" in
  "group exists") echo "{group_exists}" ;;
  "containerapp list") printf '%s' "{tags}" ;;
  *) exit 1 ;;
esac
exit {az_status}
"""

AZD_STUB = """#!/usr/bin/env bash
if [ "$1 $2" = "env get-values" ]; then
  printf '%s' "${AZD_ENV_VALUES:-}"
  exit 0
fi
echo "$*" >> "$AZD_LOG"
"""


def _run_detect_hook(
    tmp_path: Path,
    *,
    group_exists: str,
    tags: str,
    az_status: int = 0,
    subscription: str | None = None,
    recorded: str = "",
    expected_status: int = 0,
) -> list[str]:
    """Run the detection hook against stubbed `az`/`azd`; return the azd env sets."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "az").write_text(AZ_STUB.format(group_exists=group_exists, tags=tags, az_status=az_status))
    (bin_dir / "azd").write_text(AZD_STUB)
    for stub in ("az", "azd"):
        (bin_dir / stub).chmod(0o755)
    log = tmp_path / "azd.log"
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "AZURE_ENV_NAME": "an-environment",
        "AZD_LOG": str(log),
        "AZD_ENV_VALUES": recorded,
    }
    if subscription is not None:
        env["AZURE_SUBSCRIPTION_ID"] = subscription
    proc = subprocess.run(["bash", str(DETECT_HOOK)], capture_output=True, text=True, env=env, timeout=60)
    assert proc.returncode == expected_status, f"unexpected exit status:\n{proc.stdout}{proc.stderr}"
    return log.read_text().splitlines() if log.exists() else []


def test_a_live_container_app_is_detected_from_azure(tmp_path: Path) -> None:
    """The flag is raised from Azure, so a fresh runner with no `.azure/` state
    still preserves the running image."""
    calls = _run_detect_hook(tmp_path, group_exists="true", tags="agent-service\n")
    assert "env set SERVICE_AGENT_SERVICE_RESOURCE_EXISTS true" in calls
    assert "env set SERVICE_OBO_MCP_SERVER_RESOURCE_EXISTS false" in calls


def test_detection_works_with_an_explicit_subscription(tmp_path: Path) -> None:
    """Guards the `set -u`-safe expansion of the optional --subscription args."""
    calls = _run_detect_hook(
        tmp_path, group_exists="true", tags="agent-service\nobo-mcp-server\n", subscription="a-subscription"
    )
    assert "env set SERVICE_AGENT_SERVICE_RESOURCE_EXISTS true" in calls
    assert "env set SERVICE_OBO_MCP_SERVER_RESOURCE_EXISTS true" in calls


def test_an_empty_resource_group_means_a_first_create(tmp_path: Path) -> None:
    calls = _run_detect_hook(tmp_path, group_exists="false", tags="")
    assert "env set SERVICE_AGENT_SERVICE_RESOURCE_EXISTS false" in calls
    assert "env set SERVICE_OBO_MCP_SERVER_RESOURCE_EXISTS false" in calls


def test_an_unreadable_subscription_stops_the_provision(tmp_path: Path) -> None:
    """A failed query must never be read as 'absent': that is the case that
    overwrites a running application image with the bootstrap stand-in. The
    parameters file falls back to `false`, so an unanswered query has to fail
    the provision rather than let that fallback through."""
    calls = _run_detect_hook(tmp_path, group_exists="", tags="", az_status=1, expected_status=1)
    assert calls == []


def test_an_unreadable_subscription_is_tolerated_when_every_app_is_recorded(tmp_path: Path) -> None:
    """Nothing can regress when azd already records every service as existing:
    the flags keep preserving the running images."""
    recorded = 'SERVICE_AGENT_SERVICE_RESOURCE_EXISTS="true"\nSERVICE_OBO_MCP_SERVER_RESOURCE_EXISTS="true"\n'
    calls = _run_detect_hook(tmp_path, group_exists="", tags="", az_status=1, recorded=recorded)
    assert calls == []


def test_detection_runs_before_every_provision() -> None:
    preprovision = (REPO_ROOT / "azure.yaml").read_text().split("preprovision:", 1)[1]
    assert f"./hooks/{DETECT_HOOK.name} || exit 1" in preprovision


def test_the_hook_covers_every_container_app_service() -> None:
    """A new `host: containerapp` service must not silently skip detection."""
    services = yaml.safe_load((REPO_ROOT / "azure.yaml").read_text())["services"]
    declared = {name for name, service in services.items() if service.get("host") == "containerapp"}
    assert declared == {module.removesuffix(".bicep") for module in SERVICE_MODULES}
    listed = re.search(r"^SERVICES=\((.*?)\)$", DETECT_HOOK.read_text(), re.M)
    assert listed is not None
    assert set(listed.group(1).split()) == declared
