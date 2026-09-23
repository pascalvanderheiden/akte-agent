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
else:
    print("Unexpected CLI call", file=sys.stderr)
    sys.exit(99)
exit_code = int(os.environ.get("FAKE_" + key + "_EXIT", "0"))
payload = os.environ.get("FAKE_" + key, "{}")
if key == "RESOURCE":
    index = sum(call["cli"] == "az" and call["args"][:2] == ["resource", "show"] for call in calls)
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
    assert len(reads) == 1
    assert reads[0][reads[0].index("--ids") + 1] == AGENT_ID
    assert reads[0][reads[0].index("--subscription") + 1] == SUB_ID
    assert reads[0][reads[0].index("--api-version") + 1] == "2025-05-01-preview"
    assert all(call[:3] in (["az", "account", "show"], ["az", "resource", "show"]) for call in cli_calls(log, "az"))


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
    for name in ("sre-lib.sh", "sre-preflight.sh", "sre-setup.sh"):
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
        ["azd", "env", "set", "SRE_CONNECT_TELEMETRY", "false"],
        ["azd", "env", "set", "SRE_CONNECT_GITHUB", "false"],
        ["azd", "env", "set", "SRE_LOCATION", location],
        ["azd", "env", "set", "SRE_AGENT_NAME_OVERRIDE", name],
        ["azd", "env", "set", "AZURE_PRINCIPAL_TYPE", "ServicePrincipal"],
    ]
