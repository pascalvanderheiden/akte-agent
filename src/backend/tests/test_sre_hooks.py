"""Run real lifecycle hooks with closed stdin and an isolated fake CLI boundary."""

from __future__ import annotations

import copy
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
HOOKS = REPO_ROOT / "hooks"
PREFLIGHT = HOOKS / "sre-preflight.sh"
SETUP = HOOKS / "sre-setup.sh"
SUB_ID = "00000000-0000-0000-0000-000000000000"
TENANT_ID = "11111111-1111-1111-1111-111111111111"
PRINCIPAL_ID = "22222222-2222-2222-2222-222222222222"
APP_ID = "33333333-3333-3333-3333-333333333333"
RG_ID = f"/subscriptions/{SUB_ID}/resourceGroups/rg-demo"
AGENT_ID = f"{RG_ID}/providers/Microsoft.App/agents/sre-demo"
IDENTITY_ID = f"{RG_ID}/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-sre-demo"
APP_RESOURCE_ID = f"{RG_ID}/providers/Microsoft.Insights/components/appi-demo"
WORKSPACE_ID = f"{RG_ID}/providers/Microsoft.OperationalInsights/workspaces/log-demo"
SENTINEL_SECRET = "ghp_SYNTHETIC_SENTINEL_NOT_A_REAL_TOKEN"  # noqa: S105
PROVIDER: dict[str, Any] = {
    "registrationState": "Registered",
    "resourceTypes": [
        {
            "resourceType": "agents",
            "locations": ["East US 2", "Sweden Central"],
            "apiVersions": ["2025-05-01-preview"],
        }
    ],
}
RESOURCE: dict[str, Any] = {
    "id": AGENT_ID,
    "location": "eastus2",
    "tags": {"azd-env-name": "demo"},
    "state": "Succeeded",
    "identity": {
        "type": "SystemAssigned, UserAssigned",
        "principalId": PRINCIPAL_ID,
        "userAssignedIdentities": {IDENTITY_ID: {}},
    },
    "action": {"accessLevel": "Low", "mode": "Review", "identity": IDENTITY_ID},
    "graph": {"identity": IDENTITY_ID, "managedResources": [RG_ID]},
    "appId": APP_ID,
}

FAKE_CLI = """import json, os, sys
from pathlib import Path
cli = Path(sys.argv[0]).name
args = sys.argv[1:]
log = Path(os.environ["FAKE_LOG"])
calls = [json.loads(line) for line in log.read_text().splitlines()]
with log.open("a") as stream:
    stream.write(json.dumps({"cli": cli, "args": args}) + "\\n")
key = ""
if cli == "azd" and args[:2] == ["env", "get-values"]:
    key = "VALUES"
elif cli == "azd" and args[:2] == ["env", "set"]:
    key = "SET"
elif cli == "az" and args[:2] == ["account", "show"]:
    key = "ACCOUNT"
elif cli == "az" and args[:2] == ["provider", "show"]:
    key = "PROVIDER"
elif cli == "az" and args[:2] == ["resource", "show"]:
    key = "RESOURCE"
    resource_id = args[args.index("--ids") + 1]
    if "/connectors/" in resource_id:
        key = "CONNECTOR_APP" if resource_id.endswith("/app-insights") else "CONNECTOR_LOG"
    elif "/Microsoft.Insights/" in resource_id:
        key = "SOURCE_APP"
    elif "/Microsoft.OperationalInsights/" in resource_id:
        key = "SOURCE_LOG"
elif cli == "az" and args[:3] == ["deployment", "group", "create"]:
    key = "DEPLOY_APP" if "source=app-insights" in args else "DEPLOY_LOG"
    template = Path(args[args.index("--template-file") + 1])
    assert json.loads(template.read_text()) == {"resources": []}
    assert template.stat().st_mode & 0o077 == 0
elif cli == "az" and args[:2] == ["bicep", "version"]:
    assert os.environ.get("AZURE_BICEP_CHECK_VERSION") == "false"
    key = "BICEP_VERSION"
elif cli == "az" and args[:2] == ["bicep", "build"]:
    assert os.environ.get("AZURE_BICEP_CHECK_VERSION") == "false"
    assert Path(args[args.index("--file") + 1]).name == "sre-telemetry.bicep"
    key = "BICEP_BUILD"
else:
    print("Unexpected CLI call", file=sys.stderr)
    sys.exit(99)
exit_code = int(os.environ.get("FAKE_" + key + "_EXIT", "0"))
payload = os.environ.get("FAKE_" + key, "{}")
if key.startswith(("SOURCE_", "DEPLOY_", "CONNECTOR_")):
    index = sum(call["cli"] == cli and call["args"] == args for call in calls)
    errors = json.loads(os.environ.get("FAKE_" + key + "_ERRORS", "[]"))
    if errors:
        error = errors[min(index, len(errors) - 1)]
        if error:
            if error.startswith("ERROR: {"):
                body = json.loads(error.removeprefix("ERROR: "))
                body["message"] = os.environ["SRE_GITHUB_PAT"]
                error = "ERROR: " + json.dumps(body)
            else:
                error += " " + os.environ["SRE_GITHUB_PAT"]
            print(error, file=sys.stderr)
            sys.exit(1)
    responses = json.loads(os.environ.get("FAKE_" + key + "_RESPONSES", "[]"))
    if responses:
        payload = json.dumps(responses[min(index, len(responses) - 1)])
if key == "RESOURCE":
    index = sum(call["cli"] == cli and call["args"] == args for call in calls)
    errors = json.loads(os.environ.get("FAKE_ERRORS", "[]"))
    if errors:
        error = errors[min(index, len(errors) - 1)]
        if error:
            print("ERROR: (" + error + ") " + os.environ["SRE_GITHUB_PAT"], file=sys.stderr)
            sys.exit(1)
    states = os.environ.get("FAKE_STATES", "").split()
    if states:
        resource = json.loads(payload)
        resource["state"] = states[min(index, len(states) - 1)]
        payload = json.dumps(resource)
if exit_code:
    print(os.environ["SRE_GITHUB_PAT"], file=sys.stderr)
print(payload)
sys.exit(exit_code)
"""


@pytest.fixture
def fake_bin(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for cli in ("az", "azd"):
        file = bin_dir / cli
        file.write_text(f"#!{sys.executable}\n{FAKE_CLI}")
        file.chmod(0o755)
    # No real az/azd on PATH, even when a fake is removed for missing-tool cases.
    for tool in ("bash", "sh", "dirname", "jq", "tr", "grep", "mktemp", "sleep", "rm"):
        found = shutil.which(tool)
        assert found, f"Required cloud-free test tool missing: {tool}"
        (bin_dir / tool).symlink_to(found)
    return bin_dir


@pytest.fixture
def log(tmp_path: Path) -> Path:
    path = tmp_path / "cli.log"
    path.write_text("")
    return path


def enabled_env(**overrides: str) -> dict[str, str]:
    env = {
        "DEPLOY_SRE_AGENT": "true",
        "SRE_CONNECT_TELEMETRY": "false",
        "SRE_CONNECT_GITHUB": "false",
        "AZURE_ENV_NAME": "demo",
        "AZURE_SUBSCRIPTION_ID": SUB_ID,
        "AZURE_TENANT_ID": TENANT_ID,
        "AZURE_LOCATION": "eastus2",
    }
    env.update(overrides)
    return env


def setup_env(**overrides: str) -> dict[str, str]:
    return enabled_env(
        **{
            "SRE_AGENT_ENABLED": "true",
            "SRE_AGENT_ID": AGENT_ID,
            "SRE_AGENT_NAME": "sre-demo",
            "SRE_AGENT_LOCATION": "eastus2",
            "SRE_AGENT_PRINCIPAL_ID": PRINCIPAL_ID,
            "SRE_AGENT_IDENTITY_ID": IDENTITY_ID,
            "SRE_APP_INSIGHTS_APP_ID": APP_ID,
            "SRE_APP_INSIGHTS_ID": APP_RESOURCE_ID,
            "SRE_LOG_ANALYTICS_ID": WORKSPACE_ID,
            **overrides,
        }
    )


def run_hook(
    script: Path,
    fake_bin: Path,
    log: Path,
    env: dict[str, str] | None = None,
    *,
    args: tuple[str, ...] = (),
    command: str | None = None,
    cwd: Path = REPO_ROOT,
) -> subprocess.CompletedProcess[str]:
    settings = env or {}
    full_env = {
        "PATH": str(fake_bin),
        "HOME": str(fake_bin.parent),
        "TMPDIR": str(fake_bin.parent),
        "FAKE_LOG": str(log),
        "SRE_GITHUB_PAT": SENTINEL_SECRET,
        "FAKE_VALUES": json.dumps({k: v for k, v in settings.items() if not k.startswith("FAKE_")}),
        "FAKE_ACCOUNT": json.dumps({"id": SUB_ID, "tenantId": TENANT_ID, "user": {"type": "user"}}),
        "FAKE_PROVIDER": json.dumps(PROVIDER),
        "FAKE_RESOURCE": json.dumps(RESOURCE),
        "FAKE_SOURCE_APP": json.dumps(
            {"id": APP_RESOURCE_ID, "tags": {"azd-env-name": "demo"}, "appId": APP_ID, "workspaceId": WORKSPACE_ID}
        ),
        "FAKE_SOURCE_LOG": json.dumps({"id": WORKSPACE_ID, "tags": {"azd-env-name": "demo"}}),
        "FAKE_DEPLOY_APP": json.dumps({"state": "Succeeded"}),
        "FAKE_DEPLOY_LOG": json.dumps({"state": "Succeeded"}),
        "FAKE_CONNECTOR_APP": json.dumps(connector("app-insights")),
        "FAKE_CONNECTOR_LOG": json.dumps(connector("log-analytics")),
        "FAKE_BICEP_BUILD": json.dumps({"resources": []}),
        **settings,
    }
    proc = subprocess.run(
        [str(fake_bin / "bash"), "-c", command] if command else [str(script), *args],
        cwd=cwd,
        env=full_env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert SENTINEL_SECRET not in proc.stdout + proc.stderr
    return proc


def connector(source: str) -> dict[str, Any]:
    app = source == "app-insights"
    resource_id = APP_RESOURCE_ID if app else WORKSPACE_ID
    return {
        "id": f"{AGENT_ID}/connectors/{source}",
        "properties": {
            "identity": "system",
            "dataConnectorType": "AppInsights" if app else "LogAnalytics",
            "dataSource": resource_id,
            "extendedProperties": {
                "armResourceId": resource_id,
                "resource": {"name": resource_id.rsplit("/", 1)[1]},
                **({"appId": APP_ID} if app else {}),
            },
        },
    }


def cli_calls(log: Path, cli: str = "", verb: tuple[str, ...] = ()) -> list[list[str]]:
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    return [
        [call["cli"], *call["args"]]
        for call in calls
        if (not cli or call["cli"] == cli) and (not verb or call["args"][: len(verb)] == list(verb))
    ]


def result_line(proc: subprocess.CompletedProcess[str]) -> str:
    lines = [line for line in proc.stdout.splitlines() if line.startswith("SRE_RESULT ")]
    assert len(lines) == 1, proc.stdout + proc.stderr
    assert proc.stdout.splitlines()[-1] == lines[0]
    return lines[0]


@pytest.mark.parametrize("script", [PREFLIGHT, SETUP])
@pytest.mark.parametrize("flag", ["", "false"])
def test_disabled_needs_no_tools_and_makes_no_calls(script: Path, flag: str, fake_bin: Path, log: Path) -> None:
    for tool in ("az", "azd", "jq"):
        (fake_bin / tool).unlink()
    proc = run_hook(script, fake_bin, log, {"DEPLOY_SRE_AGENT": flag})
    assert proc.returncode == 0, proc.stderr
    assert cli_calls(log) == []
    if script == SETUP:
        assert result_line(proc) == "SRE_RESULT core=disabled telemetry=disabled github=disabled"
    else:
        assert proc.stdout == ""


@pytest.mark.parametrize("script", [PREFLIGHT, SETUP])
@pytest.mark.parametrize("value", ["ture", "TRUE", "yes", "1", SENTINEL_SECRET])
def test_booleans_match_arm_and_never_echo_invalid_values(script: Path, value: str, fake_bin: Path, log: Path) -> None:
    proc = run_hook(script, fake_bin, log, {"DEPLOY_SRE_AGENT": value})
    assert proc.returncode != 0
    assert "DEPLOY_SRE_AGENT must be true or false" in proc.stderr
    assert cli_calls(log) == []
    if script == SETUP:
        assert result_line(proc).startswith("SRE_RESULT core=failed")


@pytest.mark.parametrize("script", [PREFLIGHT, SETUP])
@pytest.mark.parametrize("key", ["SRE_CONNECT_TELEMETRY", "SRE_CONNECT_GITHUB"])
def test_invalid_optional_switch_fails(script: Path, key: str, fake_bin: Path, log: Path) -> None:
    proc = run_hook(script, fake_bin, log, setup_env(**{key: "maybe"}))
    assert proc.returncode != 0
    assert key in proc.stderr
    assert cli_calls(log) == []


@pytest.mark.parametrize("script", [PREFLIGHT, SETUP])
@pytest.mark.parametrize("tool", ["az", "azd", "jq"])
def test_missing_tools_fail(script: Path, tool: str, fake_bin: Path, log: Path) -> None:
    (fake_bin / tool).unlink()
    proc = run_hook(script, fake_bin, log, setup_env())
    assert proc.returncode != 0
    assert f"'{tool}' is missing" in proc.stderr
    assert cli_calls(log) == []


@pytest.mark.parametrize("script", [PREFLIGHT, SETUP])
@pytest.mark.parametrize("key", ["AZURE_ENV_NAME", "AZURE_SUBSCRIPTION_ID"])
def test_context_is_required(script: Path, key: str, fake_bin: Path, log: Path) -> None:
    proc = run_hook(script, fake_bin, log, setup_env(**{key: ""}))
    assert proc.returncode != 0
    assert "required" in proc.stderr
    assert cli_calls(log) == []


@pytest.mark.parametrize("script", [PREFLIGHT, SETUP])
@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"FAKE_ACCOUNT_EXIT": "1"}, "az login"),
        ({"FAKE_ACCOUNT": "invalid json"}, "malformed account"),
        ({"FAKE_VALUES_EXIT": "1"}, "Cannot read"),
        ({"FAKE_VALUES": "null"}, "malformed environment"),
        ({"FAKE_VALUES": "{}"}, "Injected context differs"),
        (
            {"FAKE_ACCOUNT": json.dumps({"id": TENANT_ID, "tenantId": TENANT_ID, "user": {"type": "user"}})},
            "az account set",
        ),
        (
            {"FAKE_ACCOUNT": json.dumps({"id": SUB_ID, "tenantId": SUB_ID, "user": {"type": "user"}})},
            "subscription/tenant differs",
        ),
    ],
)
def test_context_failures_stop_before_resource_calls(
    script: Path, overrides: dict[str, str], message: str, fake_bin: Path, log: Path
) -> None:
    proc = run_hook(script, fake_bin, log, setup_env(**overrides))
    assert proc.returncode != 0
    assert message in proc.stderr
    assert cli_calls(log, "az", ("resource",)) == []
    assert cli_calls(log, "az", ("provider",)) == []


def test_fresh_preflight_needs_no_outputs(fake_bin: Path, log: Path) -> None:
    env = enabled_env()
    assert not any(k.startswith("SRE_AGENT_") for k in env)
    proc = run_hook(PREFLIGHT, fake_bin, log, env)
    assert proc.returncode == 0, proc.stderr
    assert "Preflight passed" in proc.stdout
    assert "not proof of SRE subscription eligibility" in proc.stdout
    assert "2025-05-01-preview" in proc.stdout


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"FAKE_PROVIDER_EXIT": "2"}, "Could not verify"),
        ({"FAKE_PROVIDER": "invalid json"}, "Malformed"),
        ({"FAKE_PROVIDER": json.dumps({**PROVIDER, "registrationState": "NotRegistered"})}, "register"),
        ({"FAKE_PROVIDER": json.dumps({**PROVIDER, "resourceTypes": []})}, "does not prove SRE access"),
        ({"AZURE_LOCATION": "westeurope"}, "SRE_LOCATION"),
        ({"AZURE_LOCATION": ""}, "SRE_LOCATION"),
        ({"AZURE_PRINCIPAL_TYPE": "ServicePrinciple"}, "AZURE_PRINCIPAL_TYPE"),
        ({"SRE_AGENT_NAME_OVERRIDE": "../foreign"}, "DNS label"),
        ({"SRE_AGENT_NAME_OVERRIDE": "Uppercase"}, "DNS label"),
        ({"SRE_AGENT_NAME_OVERRIDE": "x" * 64}, "DNS label"),
        ({"AZURE_RESOURCE_PREFIX": "bad_prefix"}, "DNS label"),
    ],
)
def test_invalid_preflight_fails_actionably(overrides: dict[str, str], message: str, fake_bin: Path, log: Path) -> None:
    proc = run_hook(PREFLIGHT, fake_bin, log, enabled_env(**overrides))
    assert proc.returncode != 0
    assert message in proc.stderr
    assert "Preflight passed" not in proc.stdout
    assert cli_calls(log, "az", ("provider", "register")) == []


def test_pinned_api_must_be_advertised(fake_bin: Path, log: Path) -> None:
    provider = copy.deepcopy(PROVIDER)
    provider["resourceTypes"][0]["apiVersions"] = []
    proc = run_hook(PREFLIGHT, fake_bin, log, enabled_env(FAKE_PROVIDER=json.dumps(provider)))
    assert proc.returncode != 0
    assert "pinned SRE API version" in proc.stderr


def test_explicit_region_and_name_override(fake_bin: Path, log: Path) -> None:
    proc = run_hook(
        PREFLIGHT,
        fake_bin,
        log,
        enabled_env(AZURE_LOCATION="westeurope", SRE_LOCATION="swedencentral", SRE_AGENT_NAME_OVERRIDE="sre-example"),
    )
    assert proc.returncode == 0, proc.stderr
    assert "swedencentral" in proc.stdout
    for call in cli_calls(log, "azd", ("env", "set")):
        assert call[3] not in ("AZURE_LOCATION", "SRE_LOCATION")


@pytest.mark.parametrize("caller, expected", [("user", "User"), ("servicePrincipal", "ServicePrincipal")])
def test_operator_type_is_persisted_in_the_target_environment(
    caller: str, expected: str, fake_bin: Path, log: Path
) -> None:
    account = {"id": SUB_ID, "tenantId": TENANT_ID, "user": {"type": caller}}
    proc = run_hook(PREFLIGHT, fake_bin, log, enabled_env(FAKE_ACCOUNT=json.dumps(account)))
    assert proc.returncode == 0, proc.stderr
    assert cli_calls(log, "azd", ("env", "set")) == [
        ["azd", "env", "set", "AZURE_PRINCIPAL_TYPE", expected, "--environment", "demo", "--no-prompt"]
    ]


def test_type_persistence_failure_is_not_ignored(fake_bin: Path, log: Path) -> None:
    proc = run_hook(PREFLIGHT, fake_bin, log, enabled_env(FAKE_SET_EXIT="1"))
    assert proc.returncode != 0
    assert "Could not persist AZURE_PRINCIPAL_TYPE" in proc.stderr


def test_unknown_caller_requires_explicit_type(fake_bin: Path, log: Path) -> None:
    account = {"id": SUB_ID, "tenantId": TENANT_ID, "user": {"type": "unknown"}}
    proc = run_hook(PREFLIGHT, fake_bin, log, enabled_env(FAKE_ACCOUNT=json.dumps(account)))
    assert proc.returncode != 0
    assert "Set AZURE_PRINCIPAL_TYPE explicitly" in proc.stderr


def test_explicit_operator_type_is_not_overwritten(fake_bin: Path, log: Path) -> None:
    proc = run_hook(PREFLIGHT, fake_bin, log, enabled_env(AZURE_PRINCIPAL_TYPE="Group"))
    assert proc.returncode == 0, proc.stderr
    assert cli_calls(log, "azd", ("env", "set")) == []


@pytest.mark.parametrize("telemetry", ["true", "false"])
@pytest.mark.parametrize("github", ["true", "false"])
def test_ready_core_keeps_optional_results_truthful(telemetry: str, github: str, fake_bin: Path, log: Path) -> None:
    proc = run_hook(SETUP, fake_bin, log, setup_env(SRE_CONNECT_TELEMETRY=telemetry, SRE_CONNECT_GITHUB=github))
    assert proc.returncode == 0, proc.stderr
    ts = "pending" if telemetry == "true" else "disabled"
    gs = "pending" if github == "true" else "disabled"
    assert result_line(proc) == f"SRE_RESULT core=ready telemetry={ts} github={gs}"
    assert f"SRE_TELEMETRY_RESULT app_insights={ts} log_analytics={ts}" in proc.stdout
    reads = cli_calls(log, "az", ("resource", "show"))
    assert len(reads) == (5 if telemetry == "true" else 1)
    assert reads[0][reads[0].index("--ids") + 1] == AGENT_ID
    assert reads[0][reads[0].index("--subscription") + 1] == SUB_ID
    assert reads[0][reads[0].index("--api-version") + 1] == "2025-05-01-preview"
    deployments = cli_calls(log, "az", ("deployment",))
    assert len(deployments) == (2 if telemetry == "true" else 0)
    if telemetry == "true":
        assert "configured/read back" in proc.stdout
        assert "pending connector-identity query verification (not ready)" in proc.stdout


@pytest.mark.parametrize(
    "overrides",
    [
        {"SRE_AGENT_ID": ""},
        {"SRE_AGENT_ID": AGENT_ID.replace("rg-demo", "rg-foreign")},
        {"SRE_AGENT_ENABLED": "false"},
        {"SRE_AGENT_NAME": "sre-different"},
        {"SRE_AGENT_LOCATION": ""},
        {"SRE_AGENT_PRINCIPAL_ID": ""},
        {"SRE_AGENT_IDENTITY_ID": ""},
        {"SRE_APP_INSIGHTS_APP_ID": ""},
    ],
)
def test_missing_or_foreign_outputs_fail_before_reading_agent(
    overrides: dict[str, str], fake_bin: Path, log: Path
) -> None:
    proc = run_hook(SETUP, fake_bin, log, setup_env(**overrides))
    assert proc.returncode != 0
    assert result_line(proc).startswith("SRE_RESULT core=failed")
    assert cli_calls(log, "az", ("resource",)) == []


@pytest.mark.parametrize(
    "path, value",
    [
        (("action", "accessLevel"), "High"),
        (("action", "mode"), "Automatic"),
        (("action", "identity"), "foreign"),
        (("graph", "identity"), "foreign"),
        (("graph", "managedResources"), [RG_ID, "foreign"]),
        (("identity", "type"), "UserAssigned"),
        (("identity", "principalId"), "foreign"),
        (("identity", "userAssignedIdentities"), {}),
        (("tags", "azd-env-name"), "foreign"),
        (("location",), "swedencentral"),
        (("id",), AGENT_ID.replace("rg-demo", "rg-foreign")),
        (("appId",), "instrumentation-key-not-app-id"),
    ],
)
def test_succeeded_is_not_enough_for_core_ready(path: tuple[str, ...], value: Any, fake_bin: Path, log: Path) -> None:
    resource = copy.deepcopy(RESOURCE)
    target = resource
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    proc = run_hook(SETUP, fake_bin, log, setup_env(FAKE_RESOURCE=json.dumps(resource)))
    assert proc.returncode != 0
    assert "verification failed" in proc.stderr
    assert result_line(proc).startswith("SRE_RESULT core=failed")


@pytest.mark.parametrize("response", ["null", "{}", "not json", '{"state":5}'])
def test_malformed_readiness_fails(response: str, fake_bin: Path, log: Path) -> None:
    proc = run_hook(SETUP, fake_bin, log, setup_env(FAKE_RESOURCE=response))
    assert proc.returncode != 0
    assert "Malformed" in proc.stderr
    assert len(cli_calls(log, "az", ("resource",))) == 1


@pytest.mark.parametrize("state", ["Failed", "Canceled", "Cancelled", "Unknown"])
def test_terminal_and_unknown_states_do_not_retry(state: str, fake_bin: Path, log: Path) -> None:
    proc = run_hook(SETUP, fake_bin, log, setup_env(FAKE_STATES=state))
    assert proc.returncode != 0
    assert len(cli_calls(log, "az", ("resource",))) == 1


@pytest.mark.parametrize("error", ["AuthorizationFailed", "Forbidden", "InvalidApiVersionParameter", "Unexpected"])
def test_permanent_or_unknown_cli_failures_do_not_retry(error: str, fake_bin: Path, log: Path) -> None:
    proc = run_hook(SETUP, fake_bin, log, setup_env(FAKE_ERRORS=json.dumps([error])))
    assert proc.returncode != 0
    assert "raw diagnostics suppressed" in proc.stderr
    assert len(cli_calls(log, "az", ("resource",))) == 1
    assert list(fake_bin.parent.glob("tmp.*")) == []


@pytest.mark.parametrize(
    "overrides, count, success",
    [
        ({"FAKE_STATES": "Provisioning"}, 3, False),
        ({"FAKE_STATES": "Provisioning Updating Succeeded"}, 3, True),
        ({"FAKE_ERRORS": json.dumps(["ResourceNotFound", "TooManyRequests", ""])}, 3, True),
        ({"FAKE_ERRORS": json.dumps(["ServiceUnavailable"])}, 3, False),
    ],
)
def test_bounded_retries(overrides: dict[str, str], count: int, success: bool, fake_bin: Path, log: Path) -> None:
    proc = run_hook(
        SETUP,
        fake_bin,
        log,
        setup_env(SRE_READY_ATTEMPTS="3", SRE_READY_DELAY_SECONDS="0", **overrides),
    )
    assert (proc.returncode == 0) == success, proc.stderr
    assert len(cli_calls(log, "az", ("resource",))) == count
    assert result_line(proc).startswith(f"SRE_RESULT core={'ready' if success else 'failed'}")
    if not success:
        assert "after 3 attempts" in proc.stderr


@pytest.mark.parametrize(
    "key, value",
    [
        ("SRE_READY_ATTEMPTS", "0"),
        ("SRE_READY_ATTEMPTS", "-1"),
        ("SRE_READY_ATTEMPTS", "61"),
        ("SRE_READY_ATTEMPTS", "9999999999999999999999"),
        ("SRE_READY_ATTEMPTS", "abc"),
        ("SRE_READY_DELAY_SECONDS", "301"),
        ("SRE_READY_DELAY_SECONDS", "-1"),
        ("SRE_READY_DELAY_SECONDS", "0.5"),
        ("SRE_READY_DELAY_SECONDS", "abc"),
    ],
)
def test_retry_limits_are_validated(key: str, value: str, fake_bin: Path, log: Path) -> None:
    proc = run_hook(SETUP, fake_bin, log, setup_env(**{key: value}))
    assert proc.returncode != 0
    assert "must be an integer" in proc.stderr
    assert cli_calls(log) == []
    assert result_line(proc).startswith("SRE_RESULT core=failed")


def test_standalone_loads_only_selected_environment_without_eval(fake_bin: Path, log: Path) -> None:
    selected = setup_env()
    selected["UNRELATED_SECRET"] = SENTINEL_SECRET
    selected["IGNORED_COMMAND"] = "$(exit 99)"
    proc = run_hook(
        SETUP,
        fake_bin,
        log,
        {"FAKE_VALUES": json.dumps(selected), "SRE_AGENT_ID": "stale", "SRE_CONNECT_GITHUB": "true"},
        args=("--environment", "demo"),
    )
    assert proc.returncode == 0, proc.stderr
    assert result_line(proc) == "SRE_RESULT core=ready telemetry=disabled github=disabled"
    assert cli_calls(log, "azd")[0] == [
        "azd",
        "env",
        "get-values",
        "--environment",
        "demo",
        "--output",
        "json",
        "--no-prompt",
    ]


def test_standalone_refuses_wrong_environment(fake_bin: Path, log: Path) -> None:
    proc = run_hook(SETUP, fake_bin, log, setup_env(), args=("--environment", "foreign"))
    assert proc.returncode != 0
    assert cli_calls(log, "az") == []


def test_standalone_disabled_clears_stale_opt_in(fake_bin: Path, log: Path) -> None:
    proc = run_hook(
        SETUP,
        fake_bin,
        log,
        setup_env(FAKE_VALUES=json.dumps({"AZURE_ENV_NAME": "demo"})),
        args=("--environment", "demo"),
    )
    assert proc.returncode == 0, proc.stderr
    assert result_line(proc) == "SRE_RESULT core=disabled telemetry=disabled github=disabled"
    assert cli_calls(log, "az") == []


def test_repeated_setup_only_reads_same_resource(fake_bin: Path, log: Path) -> None:
    first = run_hook(SETUP, fake_bin, log, setup_env())
    second = run_hook(SETUP, fake_bin, log, setup_env())
    assert first.returncode == second.returncode == 0
    assert result_line(first) == result_line(second)
    reads = cli_calls(log, "az", ("resource",))
    assert len(reads) == 2 and reads[0] == reads[1]
    assert cli_calls(log, "azd", ("env", "set")) == []


@pytest.fixture
def lifecycle_workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    hooks = root / "hooks"
    hooks.mkdir(parents=True)
    for name in ("sre-lib.sh", "sre-preflight.sh", "sre-setup.sh", "sre-telemetry.sh"):
        shutil.copy2(HOOKS / name, hooks / name)
    for name in ("select-use-cases", "grant-obo-consent", "assign-agent-roles", "postdeploy"):
        file = hooks / f"{name}.sh"
        file.write_text(f'#!/usr/bin/env bash\nprintf "{name} %s\\n" "$*"\nexit "${{STUB_EXIT:-0}}"\n')
        file.chmod(0o755)
    return root


@pytest.mark.parametrize("enabled", [True, False])
def test_preprovision_chain_preserves_skills(
    enabled: bool, fake_bin: Path, log: Path, lifecycle_workspace: Path
) -> None:
    hook = yaml.safe_load((REPO_ROOT / "azure.yaml").read_text())["hooks"]["preprovision"]
    proc = run_hook(
        PREFLIGHT,
        fake_bin,
        log,
        enabled_env() if enabled else {},
        command=hook["run"],
        cwd=lifecycle_workspace,
    )
    assert proc.returncode == 0, proc.stderr
    assert "select-use-cases" in proc.stdout
    if enabled:
        assert proc.stdout.index("Preflight passed") < proc.stdout.index("select-use-cases")
    else:
        assert cli_calls(log) == []


def test_preprovision_failure_stops_chain(fake_bin: Path, log: Path, lifecycle_workspace: Path) -> None:
    hook = yaml.safe_load((REPO_ROOT / "azure.yaml").read_text())["hooks"]["preprovision"]
    proc = run_hook(
        PREFLIGHT,
        fake_bin,
        log,
        enabled_env(FAKE_PROVIDER_EXIT="1"),
        command=hook["run"],
        cwd=lifecycle_workspace,
    )
    assert proc.returncode != 0
    assert "select-use-cases" not in proc.stdout


@pytest.mark.parametrize("state", ["Succeeded", "Failed"])
def test_postprovision_core_is_gating_but_consent_is_best_effort(
    state: str, fake_bin: Path, log: Path, lifecycle_workspace: Path
) -> None:
    hook = yaml.safe_load((REPO_ROOT / "azure.yaml").read_text())["hooks"]["postprovision"]
    assert not hook.get("interactive", False)
    proc = run_hook(
        SETUP,
        fake_bin,
        log,
        setup_env(FAKE_STATES=state, STUB_EXIT="1"),
        command=hook["run"],
        cwd=lifecycle_workspace,
    )
    assert (proc.returncode == 0) == (state == "Succeeded"), proc.stderr
    assert "grant-obo-consent" in proc.stdout
    assert ("Deployment complete." in proc.stdout) == (state == "Succeeded")


def test_postdeploy_preserves_roles_and_noninteractive_upload(
    fake_bin: Path, log: Path, lifecycle_workspace: Path
) -> None:
    hook = yaml.safe_load((REPO_ROOT / "azure.yaml").read_text())["hooks"]["postdeploy"]
    assert not hook.get("interactive", False)
    proc = run_hook(SETUP, fake_bin, log, command=hook["run"], cwd=lifecycle_workspace)
    assert proc.returncode == 0
    assert proc.stdout.index("assign-agent-roles") < proc.stdout.index("postdeploy --from-deploy")
    assert cli_calls(log) == []


def test_workflow_is_manual_and_deploy_only_does_not_configure_or_provision_sre() -> None:
    workflow = yaml.safe_load((REPO_ROOT / ".github/workflows/deploy.yml").read_text())
    # PyYAML's YAML 1.1 parser treats the Actions "on" key as boolean True.
    trigger = workflow[True]
    assert set(trigger) == {"workflow_dispatch"}
    inputs = trigger["workflow_dispatch"]["inputs"]
    assert inputs["deploy_sre_agent"]["default"] is False
    steps = {step["name"]: step for step in workflow["jobs"]["deploy"]["steps"] if "name" in step}
    assert steps["Configure SRE Agent options"]["if"] == "inputs.provision"
    assert steps["Provision infrastructure"]["if"] == "inputs.provision"
    assert steps["Refresh environment outputs"]["if"] == "${{ !inputs.provision }}"
    assert steps["Refresh environment outputs"]["run"].strip().endswith("azd env refresh --no-prompt")
    services = yaml.safe_load((REPO_ROOT / "azure.yaml").read_text())["services"]
    assert set(services) == {"agent-service", "kratos-agent", "obo-mcp-server", "web"}
    assert services["obo-mcp-server"]["condition"] == "${DEPLOY_OBO=true}"
    assert all(services[name]["docker"]["remoteBuild"] for name in ("agent-service", "kratos-agent", "obo-mcp-server"))


@pytest.mark.parametrize("location, name", [("", ""), ("swedencentral", "sre-example")])
def test_workflow_options_execute_safely_and_clear_old_overrides(
    location: str, name: str, fake_bin: Path, log: Path
) -> None:
    workflow = yaml.safe_load((REPO_ROOT / ".github/workflows/deploy.yml").read_text())
    step = next(
        step for step in workflow["jobs"]["deploy"]["steps"] if step.get("name") == "Configure SRE Agent options"
    )
    proc = run_hook(
        PREFLIGHT,
        fake_bin,
        log,
        {
            "DEPLOY_SRE_AGENT_INPUT": "true",
            "SRE_LOCATION_INPUT": location,
            "SRE_AGENT_NAME_INPUT": name,
        },
        command=step["run"],
    )
    assert proc.returncode == 0, proc.stderr
    assert cli_calls(log) == [
        ["azd", "env", "set", "DEPLOY_SRE_AGENT", "true"],
        ["azd", "env", "set", "SRE_CONNECT_TELEMETRY", "true"],
        ["azd", "env", "set", "SRE_CONNECT_GITHUB", "false"],
        ["azd", "env", "set", "SRE_LOCATION", location],
        ["azd", "env", "set", "SRE_AGENT_NAME_OVERRIDE", name],
        ["azd", "env", "set", "AZURE_PRINCIPAL_TYPE", "ServicePrincipal"],
    ]


def telemetry_env(**overrides: str) -> dict[str, str]:
    return setup_env(
        **{"SRE_CONNECT_TELEMETRY": "true", "SRE_READY_ATTEMPTS": "3", "SRE_READY_DELAY_SECONDS": "0", **overrides}
    )


def test_telemetry_deploys_each_source_with_selected_outputs_and_no_secrets(fake_bin: Path, log: Path) -> None:
    proc = run_hook(SETUP, fake_bin, log, telemetry_env(), args=("--environment", "demo"))
    assert proc.returncode == 0, proc.stderr
    deployments = cli_calls(log, "az", ("deployment", "group", "create"))
    assert len(deployments) == 2
    for call, source in zip(deployments, ("app-insights", "log-analytics"), strict=True):
        assert call[call.index("--subscription") + 1] == SUB_ID
        assert call[call.index("--resource-group") + 1] == "rg-demo"
        assert call[call.index("--mode") + 1] == "Incremental"
        assert call[call.index("--query") + 1] == "{state:properties.provisioningState}"
        assert f"source={source}" in call
        assert f"agentPrincipalId={PRINCIPAL_ID}" in call
        assert "agentName=sre-demo" in call
        assert f"appInsightsResourceId={APP_RESOURCE_ID}" in call
        assert f"logAnalyticsResourceId={WORKSPACE_ID}" in call
        assert f"appInsightsAppId={APP_ID}" in call
        assert not any("token" in arg.lower() or "password" in arg.lower() for arg in call)
    assert "telemetry=pending" in result_line(proc)
    assert "not ready" in proc.stdout
    assert list(fake_bin.parent.glob("tmp.*")) == []
    assert all("rest" not in call and "get-access-token" not in call for call in cli_calls(log, "az"))


@pytest.mark.parametrize(
    "key", ["SOURCE_APP", "DEPLOY_APP", "CONNECTOR_APP", "SOURCE_LOG", "DEPLOY_LOG", "CONNECTOR_LOG"]
)
@pytest.mark.parametrize(
    "error,state,exit_code",
    [
        ("AuthorizationFailed", "pending", 0),
        ("RequestDisallowedByPolicy", "unavailable", 0),
        ("InvalidTemplate", "failed", 1),
        ("InvalidApiVersionParameter", "failed", 1),
        ("NoRegisteredProviderFound", "failed", 1),
        ("Unexpected", "failed", 1),
    ],
)
def test_telemetry_independent_errors_preserve_core_and_other_connector(
    key: str, error: str, state: str, exit_code: int, fake_bin: Path, log: Path
) -> None:
    env = telemetry_env(**{f"FAKE_{key}_ERRORS": json.dumps([f"ERROR: ({error})"]), "SRE_CONNECT_GITHUB": "true"})
    proc = run_hook(SETUP, fake_bin, log, env)
    assert proc.returncode == exit_code, proc.stderr
    app, law = (state, "pending") if key.endswith("APP") else ("pending", state)
    assert f"SRE_TELEMETRY_RESULT app_insights={app} log_analytics={law}" in proc.stdout
    assert result_line(proc) == f"SRE_RESULT core=ready telemetry={state} github=pending"
    other = "log-analytics" if key.endswith("APP") else "app-insights"
    assert f"Telemetry {other} configured/read back" in proc.stdout
    assert "GitHub pending:" in proc.stdout  # Optional failure did not short-circuit the next integration.
    calls = cli_calls(log, "az")
    assert all("delete" not in call and "login" not in call and "install" not in call for call in calls)
    assert list(fake_bin.parent.glob("tmp.*")) == []


@pytest.mark.parametrize(
    "details,state,exit_code",
    [
        ([{"code": "AuthorizationFailed"}], "pending", 0),
        ([{"code": "RequestDisallowedByPolicy"}], "unavailable", 0),
        ([{"code": "RequestDisallowedByPolicy"}, {"code": "InvalidTemplate"}], "failed", 1),
        ([{"code": "DeploymentFailed", "details": [{"code": "Forbidden"}]}], "pending", 0),
    ],
)
def test_telemetry_nested_arm_errors_never_hide_template_defects(
    details: list[dict[str, Any]], state: str, exit_code: int, fake_bin: Path, log: Path
) -> None:
    error = "ERROR: " + json.dumps({"error": {"code": "DeploymentFailed", "details": details}})
    proc = run_hook(SETUP, fake_bin, log, telemetry_env(FAKE_DEPLOY_APP_ERRORS=json.dumps([error])))
    assert proc.returncode == exit_code, proc.stderr
    assert f"app_insights={state} log_analytics=pending" in proc.stdout


@pytest.mark.parametrize("key", ["SOURCE_APP", "DEPLOY_APP", "CONNECTOR_APP"])
@pytest.mark.parametrize("exhausted", [False, True])
def test_telemetry_propagation_retries_are_bounded_and_exhaustion_is_nonzero(
    key: str, exhausted: bool, fake_bin: Path, log: Path
) -> None:
    errors = (
        ["ERROR: (PrincipalNotFound)"] if exhausted else ["ERROR: (ResourceNotFound)", "ERROR: (TooManyRequests)", ""]
    )
    proc = run_hook(SETUP, fake_bin, log, telemetry_env(**{f"FAKE_{key}_ERRORS": json.dumps(errors)}))
    assert proc.returncode == (1 if exhausted else 0), proc.stderr
    if key == "DEPLOY_APP":
        calls = [c for c in cli_calls(log, "az", ("deployment",)) if "source=app-insights" in c]
    else:
        target = APP_RESOURCE_ID if key == "SOURCE_APP" else f"{AGENT_ID}/connectors/app-insights"
        calls = [c for c in cli_calls(log, "az", ("resource",)) if target in c]
    assert len(calls) == 3
    assert "app_insights=pending log_analytics=pending" in proc.stdout
    assert "Telemetry log-analytics configured/read back" in proc.stdout
    if exhausted:
        assert "exhausted 3 attempts" in proc.stdout


@pytest.mark.parametrize("exhausted", [False, True])
def test_telemetry_connector_readiness_has_one_retry_budget(exhausted: bool, fake_bin: Path, log: Path) -> None:
    waiting = connector("app-insights")
    waiting["properties"]["provisioningState"] = "Updating"
    responses = [waiting] if exhausted else [waiting, waiting, connector("app-insights")]
    proc = run_hook(SETUP, fake_bin, log, telemetry_env(FAKE_CONNECTOR_APP_RESPONSES=json.dumps(responses)))
    assert proc.returncode == (1 if exhausted else 0), proc.stderr
    reads = [c for c in cli_calls(log, "az", ("resource",)) if f"{AGENT_ID}/connectors/app-insights" in c]
    assert len(reads) == 3


@pytest.mark.parametrize("state", ["Failed", "Unknown", None, 5, False])
def test_telemetry_invalid_connector_state_fails_without_retry(state: Any, fake_bin: Path, log: Path) -> None:
    response = connector("app-insights")
    response["properties"]["provisioningState"] = state
    proc = run_hook(SETUP, fake_bin, log, telemetry_env(FAKE_CONNECTOR_APP=json.dumps(response)))
    assert proc.returncode == 1
    assert "app_insights=failed log_analytics=pending" in proc.stdout
    reads = [c for c in cli_calls(log, "az", ("resource",)) if f"{AGENT_ID}/connectors/app-insights" in c]
    assert len(reads) == 1


@pytest.mark.parametrize("key", ["SOURCE_APP", "DEPLOY_APP", "CONNECTOR_APP"])
@pytest.mark.parametrize("payload", ["invalid json", "null", "[]", "{}"])
def test_telemetry_malformed_success_is_nonzero(key: str, payload: str, fake_bin: Path, log: Path) -> None:
    proc = run_hook(SETUP, fake_bin, log, telemetry_env(**{f"FAKE_{key}": payload}))
    assert proc.returncode == 1
    assert "app_insights=failed log_analytics=pending" in proc.stdout


@pytest.mark.parametrize(
    "path,value",
    [
        (("id",), "foreign"),
        (("properties", "identity"), IDENTITY_ID),
        (("properties", "dataConnectorType"), "LogAnalytics"),
        (("properties", "dataSource"), WORKSPACE_ID),
        (("properties", "extendedProperties", "appId"), "ingestion-key-not-app-id"),
        (("properties", "extendedProperties", "armResourceId"), WORKSPACE_ID),
        (("properties", "extendedProperties", "resource", "name"), "foreign"),
    ],
)
def test_telemetry_readback_verifies_target_and_identity(
    path: tuple[str, ...], value: str, fake_bin: Path, log: Path
) -> None:
    response = connector("app-insights")
    target = response
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    proc = run_hook(SETUP, fake_bin, log, telemetry_env(FAKE_CONNECTOR_APP=json.dumps(response)))
    assert proc.returncode == 1
    assert "read-back differs" in proc.stderr


@pytest.mark.parametrize("key", ["SRE_APP_INSIGHTS_ID", "SRE_LOG_ANALYTICS_ID"])
@pytest.mark.parametrize("value", ["", "foreign", f"{RG_ID}/providers/Microsoft.Insights/components/../../foreign"])
def test_telemetry_missing_or_foreign_monitoring_outputs_are_isolated(
    key: str, value: str, fake_bin: Path, log: Path
) -> None:
    proc = run_hook(SETUP, fake_bin, log, telemetry_env(**{key: value}))
    assert proc.returncode == 1
    assert "core=ready" in result_line(proc)
    source = "app-insights" if key == "SRE_APP_INSIGHTS_ID" else "log-analytics"
    assert all(f"source={source}" not in c for c in cli_calls(log, "az", ("deployment",)))


@pytest.mark.parametrize(
    "key", ["SRE_AGENT_ID", "SRE_AGENT_PRINCIPAL_ID", "SRE_APP_INSIGHTS_APP_ID", "SRE_APP_INSIGHTS_ID"]
)
def test_telemetry_rejects_stale_injected_outputs(key: str, fake_bin: Path, log: Path) -> None:
    selected = telemetry_env(**{key: "stale"})
    proc = run_hook(SETUP, fake_bin, log, telemetry_env(FAKE_VALUES=json.dumps(selected)))
    assert proc.returncode == 1
    assert "selected environment" in proc.stderr
    assert all("source=app-insights" not in c for c in cli_calls(log, "az", ("deployment",)))


@pytest.mark.parametrize(
    "overrides",
    [{"FAKE_BICEP_VERSION_EXIT": "1"}, {"FAKE_BICEP_BUILD_EXIT": "1"}, {"FAKE_BICEP_BUILD": "null"}],
)
def test_telemetry_missing_compiler_and_compile_errors_fail_without_install(
    overrides: dict[str, str], fake_bin: Path, log: Path
) -> None:
    proc = run_hook(SETUP, fake_bin, log, telemetry_env(**overrides))
    assert proc.returncode == 1
    assert result_line(proc) == "SRE_RESULT core=ready telemetry=failed github=disabled"
    assert cli_calls(log, "az", ("deployment",)) == []
    assert all("install" not in c and "upgrade" not in c for c in cli_calls(log))
    assert list(fake_bin.parent.glob("tmp.*")) == []


def test_telemetry_repeat_runs_preserve_connector_and_deployment_names(fake_bin: Path, log: Path) -> None:
    for _ in range(2):
        proc = run_hook(SETUP, fake_bin, log, telemetry_env())
        assert proc.returncode == 0, proc.stderr
    deployments = cli_calls(log, "az", ("deployment",))
    names = [c[c.index("--name") + 1] for c in deployments]
    assert names[:2] == names[2:] and len(set(names)) == 2
    for first, second in zip(deployments[:2], deployments[2:], strict=True):
        assert first[first.index("--parameters") :] == second[second.index("--parameters") :]


@pytest.mark.parametrize("suffix", ["a", "b"])
def test_telemetry_deployment_name_preserves_long_agent_identity(suffix: str, fake_bin: Path, log: Path) -> None:
    name = "s" * 62 + suffix
    agent_id = AGENT_ID.replace("sre-demo", name)
    resource = copy.deepcopy(RESOURCE)
    resource["id"] = agent_id
    overrides = {"SRE_AGENT_NAME": name, "SRE_AGENT_ID": agent_id, "FAKE_RESOURCE": json.dumps(resource)}
    for key, source in (("FAKE_CONNECTOR_APP", "app-insights"), ("FAKE_CONNECTOR_LOG", "log-analytics")):
        response = connector(source)
        response["id"] = f"{agent_id}/connectors/{source}"
        overrides[key] = json.dumps(response)
    proc = run_hook(SETUP, fake_bin, log, telemetry_env(**overrides))
    assert proc.returncode == 0, proc.stderr
    names = [c[c.index("--name") + 1] for c in cli_calls(log, "az", ("deployment",))]
    assert names == [f"a{name}", f"l{name}"]
    assert all(len(n) == 64 for n in names)


@pytest.mark.parametrize("value,success", [("false", True), ("true", True), ("TRUE", False), ("yes", False)])
def test_workflow_telemetry_boolean_matches_local_hook(value: str, success: bool, fake_bin: Path, log: Path) -> None:
    workflow = yaml.safe_load((REPO_ROOT / ".github/workflows/deploy.yml").read_text())
    assert workflow[True]["workflow_dispatch"]["inputs"]["sre_connect_telemetry"]["default"] is True
    step = next(s for s in workflow["jobs"]["deploy"]["steps"] if s.get("name") == "Configure SRE Agent options")
    proc = run_hook(
        SETUP,
        fake_bin,
        log,
        {"SRE_CONNECT_TELEMETRY_INPUT": value, "DEPLOY_SRE_AGENT_INPUT": "true"},
        command=step["run"],
    )
    assert (proc.returncode == 0) == success
    if success:
        assert ["azd", "env", "set", "SRE_CONNECT_TELEMETRY", value] in cli_calls(log)
    else:
        assert cli_calls(log) == []


def test_telemetry_off_skips_compilation_and_every_workload_call(fake_bin: Path, log: Path) -> None:
    proc = run_hook(
        SETUP,
        fake_bin,
        log,
        setup_env(SRE_APP_INSIGHTS_ID="", SRE_LOG_ANALYTICS_ID="", FAKE_BICEP_VERSION_EXIT="1"),
    )
    assert proc.returncode == 0, proc.stderr
    assert result_line(proc) == "SRE_RESULT core=ready telemetry=disabled github=disabled"
    assert all(c[:3] in (["az", "account", "show"], ["az", "resource", "show"]) for c in cli_calls(log, "az"))
    assert len(cli_calls(log, "az", ("resource",))) == 1


def test_telemetry_default_is_enabled_without_a_persisted_switch(fake_bin: Path, log: Path) -> None:
    env = telemetry_env()
    del env["SRE_CONNECT_TELEMETRY"]
    proc = run_hook(SETUP, fake_bin, log, env)
    assert proc.returncode == 0, proc.stderr
    assert len(cli_calls(log, "az", ("deployment",))) == 2


@pytest.mark.parametrize(
    "error",
    [
        'ERROR: {"code":"AuthorizationFailed","details":"invalid"}',
        'ERROR: {"error":{"code":"DeploymentFailed","details":["invalid"]}}',
        "unstructured transport failure",
    ],
)
def test_telemetry_malformed_errors_are_failures_not_restrictions(error: str, fake_bin: Path, log: Path) -> None:
    proc = run_hook(SETUP, fake_bin, log, telemetry_env(FAKE_DEPLOY_APP_ERRORS=json.dumps([error])))
    assert proc.returncode == 1
    assert "app_insights=failed log_analytics=pending" in proc.stdout


@pytest.mark.parametrize("field,value", [("tags", {}), ("appId", "foreign"), ("workspaceId", "foreign")])
def test_telemetry_monitoring_drift_blocks_only_affected_deployment(
    field: str, value: Any, fake_bin: Path, log: Path
) -> None:
    response = {"id": APP_RESOURCE_ID, "tags": {"azd-env-name": "demo"}, "appId": APP_ID, "workspaceId": WORKSPACE_ID}
    response[field] = value
    proc = run_hook(SETUP, fake_bin, log, telemetry_env(FAKE_SOURCE_APP=json.dumps(response)))
    assert proc.returncode == 1
    deployments = cli_calls(log, "az", ("deployment",))
    assert len(deployments) == 1 and "source=log-analytics" in deployments[0]


def test_postprovision_runs_telemetry_using_new_outputs(fake_bin: Path, log: Path, lifecycle_workspace: Path) -> None:
    hook = yaml.safe_load((REPO_ROOT / "azure.yaml").read_text())["hooks"]["postprovision"]
    proc = run_hook(
        SETUP,
        fake_bin,
        log,
        telemetry_env(),
        command=hook["run"],
        cwd=lifecycle_workspace,
    )
    assert proc.returncode == 0, proc.stderr
    assert "grant-obo-consent" in proc.stdout
    assert "Deployment complete." in proc.stdout
    assert len(cli_calls(log, "az", ("deployment",))) == 2
    assert "SRE_RESULT core=ready telemetry=pending github=disabled" in proc.stdout
    assert all(c[:3] != ["azd", "env", "refresh"] for c in cli_calls(log))


def test_standalone_telemetry_discards_other_environments_monitoring_ids(fake_bin: Path, log: Path) -> None:
    proc = run_hook(
        SETUP,
        fake_bin,
        log,
        {
            "SRE_APP_INSIGHTS_ID": "stale",
            "SRE_LOG_ANALYTICS_ID": "stale",
            "FAKE_VALUES": json.dumps(telemetry_env()),
            "SRE_READY_DELAY_SECONDS": "0",
        },
        args=("--environment", "demo"),
    )
    assert proc.returncode == 0, proc.stderr
    assert len(cli_calls(log, "az", ("deployment",))) == 2
    assert all("stale" not in arg for call in cli_calls(log) for arg in call)
