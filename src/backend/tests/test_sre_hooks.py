"""Lifecycle-hook tests for the opt-in Azure SRE Agent.

The hooks are run as real processes with a fake ``az``/``azd`` on ``PATH`` and
stdin closed, so what is asserted is the observable contract: exit status, the
reported setup result, the Azure commands actually issued, and whether a hook
would stop and wait for a human. Nothing here touches Azure.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

# ``src/backend/tests/test_sre_hooks.py`` → parents[3] is the repo root.
REPO_ROOT = Path(__file__).resolve().parents[3]
HOOKS = REPO_ROOT / "hooks"

PREFLIGHT = HOOKS / "sre-preflight.sh"
SETUP = HOOKS / "sre-setup.sh"

AGENT_ID = (
    "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-demo/providers/Microsoft.App/agents/sre-demo"
)
SUB_ID = "00000000-0000-0000-0000-000000000000"

# A value that must never be echoed by a hook, in success or in failure.
SENTINEL_SECRET = "ghp_SYNTHETIC_SENTINEL_NOT_A_REAL_TOKEN"  # noqa: S105

FAKE_AZ = """#!/usr/bin/env bash
echo "az $*" >> "$FAKE_LOG"
ARGS="$*"
case "$ARGS" in
  *"account show --query id"*)
    [ -n "${FAKE_SUB_ID:-}" ] || exit 1
    echo "$FAKE_SUB_ID" ;;
  *"account show --query user.type"*)
    echo "${FAKE_USER_TYPE:-user}" ;;
  *"registrationState"*)
    echo "${FAKE_REGISTRATION:-Registered}" ;;
  *"resourceTypes"*)
    [ "${FAKE_LOCATIONS_FAIL:-0}" = "1" ] && exit 2
    [ -n "${FAKE_LOCATIONS-}" ] && printf '%s\\n' "$FAKE_LOCATIONS" ;;
  *"resource show"*)
    # Answer with the Nth state of FAKE_STATES, repeating the last one, so a
    # test can script "not ready, not ready, ready".
    # shellcheck disable=SC2086
    set -- ${FAKE_STATES:-Succeeded}
    IDX="$(grep -c 'resource show' "$FAKE_LOG")"
    [ "$IDX" -gt "$#" ] && IDX=$#
    eval "echo \\${$IDX}" ;;
  *) exit 1 ;;
esac
exit 0
"""

FAKE_AZD = """#!/usr/bin/env bash
echo "azd $*" >> "$FAKE_LOG"
exit 0
"""


@pytest.fixture
def fake_bin(tmp_path: Path) -> Path:
    """A PATH containing only fake ``az``/``azd`` plus the real shell tools."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "az").write_text(FAKE_AZ)
    (bin_dir / "azd").write_text(FAKE_AZD)
    for tool in ("az", "azd"):
        (bin_dir / tool).chmod(0o755)
    return bin_dir


@pytest.fixture
def bin_without_az(tmp_path: Path) -> Path:
    """A PATH with the usual shell tools but no ``az``/``azd`` at all."""
    bin_dir = tmp_path / "nocli"
    bin_dir.mkdir()
    for tool in ("bash", "sh", "dirname", "sed", "tr", "sleep", "wc", "grep", "cat", "env", "printf"):
        found = shutil.which(tool)
        if found:
            (bin_dir / tool).symlink_to(found)
    return bin_dir


@pytest.fixture
def log(tmp_path: Path) -> Path:
    path = tmp_path / "cli.log"
    path.write_text("")
    return path


def run_hook(
    script: Path,
    fake_bin: Path,
    log: Path,
    env: dict[str, str] | None = None,
    path: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a hook as a real process with stdin closed."""
    path = path or f"{fake_bin}:/usr/bin:/bin"
    full_env = {
        "PATH": path,
        "HOME": os.environ.get("HOME", "/tmp"),
        "FAKE_LOG": str(log),
        # A synthetic secret in the environment of every run.
        "SRE_GITHUB_PAT": SENTINEL_SECRET,
    }
    full_env.update(env or {})
    return subprocess.run(
        [str(script)],
        cwd=REPO_ROOT,
        env=full_env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
    )


def cli_calls(log: Path) -> list[str]:
    return [line for line in log.read_text().splitlines() if line.strip()]


def result_line(proc: subprocess.CompletedProcess[str]) -> str:
    lines = [ln for ln in (proc.stdout + proc.stderr).splitlines() if ln.startswith("SRE_RESULT ")]
    assert lines, f"no SRE_RESULT contract line in output:\n{proc.stdout}\n{proc.stderr}"
    return lines[-1]


def enabled_env(**overrides: str) -> dict[str, str]:
    env = {
        "DEPLOY_SRE_AGENT": "true",
        "SRE_CONNECT_TELEMETRY": "false",
        "SRE_CONNECT_GITHUB": "false",
        "AZURE_SUBSCRIPTION_ID": SUB_ID,
        "AZURE_LOCATION": "eastus2",
        "FAKE_SUB_ID": SUB_ID,
        "FAKE_LOCATIONS": "East US 2\nSweden Central",
    }
    env.update(overrides)
    return env


# ---------------------------------------------------------------------------
# Preflight — disabled environments
# ---------------------------------------------------------------------------


def test_preflight_is_a_noop_when_unset(fake_bin: Path, log: Path) -> None:
    """The default (no opt-in) must not touch Azure or print noise."""
    proc = run_hook(PREFLIGHT, fake_bin, log)
    assert proc.returncode == 0
    assert cli_calls(log) == []
    assert proc.stdout == ""


def test_preflight_is_a_noop_when_disabled(fake_bin: Path, log: Path) -> None:
    proc = run_hook(PREFLIGHT, fake_bin, log, {"DEPLOY_SRE_AGENT": "false"})
    assert proc.returncode == 0
    assert cli_calls(log) == []


def test_preflight_rejects_a_malformed_boolean(fake_bin: Path, log: Path) -> None:
    """A typo must fail loudly instead of quietly skipping SRE."""
    proc = run_hook(PREFLIGHT, fake_bin, log, {"DEPLOY_SRE_AGENT": "ture"})
    assert proc.returncode != 0
    assert "DEPLOY_SRE_AGENT" in proc.stderr
    assert cli_calls(log) == []


def test_preflight_rejects_a_malformed_connector_switch(fake_bin: Path, log: Path) -> None:
    proc = run_hook(PREFLIGHT, fake_bin, log, enabled_env(SRE_CONNECT_TELEMETRY="maybe"))
    assert proc.returncode != 0
    assert "SRE_CONNECT_TELEMETRY" in proc.stderr


# ---------------------------------------------------------------------------
# Preflight — environment checks
# ---------------------------------------------------------------------------


def test_preflight_reports_missing_azure_cli(fake_bin: Path, log: Path, bin_without_az: Path) -> None:
    proc = run_hook(PREFLIGHT, fake_bin, log, enabled_env(), path=str(bin_without_az))
    assert proc.returncode != 0
    assert "Azure CLI" in proc.stderr
    assert "DEPLOY_SRE_AGENT=false" in proc.stderr


def test_preflight_does_not_log_in_for_you(fake_bin: Path, log: Path) -> None:
    proc = run_hook(PREFLIGHT, fake_bin, log, enabled_env(FAKE_SUB_ID=""))
    assert proc.returncode != 0
    assert "az login" in proc.stderr
    assert not any("login" in call for call in cli_calls(log))


def test_preflight_refuses_a_mismatched_subscription(fake_bin: Path, log: Path) -> None:
    other = "11111111-1111-1111-1111-111111111111"
    proc = run_hook(PREFLIGHT, fake_bin, log, enabled_env(FAKE_SUB_ID=other))
    assert proc.returncode != 0
    assert "az account set" in proc.stderr
    # Reports the mismatch; never switches context on the operator's behalf.
    assert not any("account set" in call for call in cli_calls(log))


def test_preflight_does_not_register_the_provider(fake_bin: Path, log: Path) -> None:
    proc = run_hook(PREFLIGHT, fake_bin, log, enabled_env(FAKE_REGISTRATION="NotRegistered"))
    assert proc.returncode != 0
    assert "az provider register" in proc.stderr
    assert not any(call.startswith("az provider register") for call in cli_calls(log))


def test_preflight_rejects_an_unsupported_region(fake_bin: Path, log: Path) -> None:
    proc = run_hook(PREFLIGHT, fake_bin, log, enabled_env(AZURE_LOCATION="westeurope"))
    assert proc.returncode != 0
    assert "SRE_LOCATION" in proc.stderr
    assert "Sweden Central" in proc.stderr


def test_preflight_accepts_a_region_override(fake_bin: Path, log: Path) -> None:
    """An unsupported application region is fixed by SRE_LOCATION, not by moving the app."""
    proc = run_hook(
        PREFLIGHT,
        fake_bin,
        log,
        enabled_env(AZURE_LOCATION="westeurope", SRE_LOCATION="swedencentral"),
    )
    assert proc.returncode == 0, proc.stderr
    assert "swedencentral" in proc.stdout


def test_preflight_explains_denied_service_access(fake_bin: Path, log: Path) -> None:
    """Provider registration alone does not prove the subscription has SRE."""
    proc = run_hook(PREFLIGHT, fake_bin, log, enabled_env(FAKE_LOCATIONS=""))
    assert proc.returncode != 0
    assert "not offered" in proc.stderr
    assert "registration of the" in proc.stderr
    assert "does not by itself make the service available" in proc.stderr


def test_preflight_explains_unverifiable_availability(fake_bin: Path, log: Path) -> None:
    """A failed availability query is reported as unverified, not as eligibility."""
    proc = run_hook(PREFLIGHT, fake_bin, log, enabled_env(FAKE_LOCATIONS_FAIL="1"))
    assert proc.returncode == 0
    assert "Could not verify" in proc.stderr
    assert "not a confirmation" in proc.stderr


def test_preflight_passes_on_a_supported_environment(fake_bin: Path, log: Path) -> None:
    proc = run_hook(PREFLIGHT, fake_bin, log, enabled_env())
    assert proc.returncode == 0, proc.stderr
    assert "Preflight passed" in proc.stdout
    assert "2025-05-01-preview" in proc.stdout


def test_preflight_records_a_service_principal_caller(fake_bin: Path, log: Path) -> None:
    """CI deploys as a service principal; assuming 'User' breaks role assignment."""
    proc = run_hook(PREFLIGHT, fake_bin, log, enabled_env(FAKE_USER_TYPE="servicePrincipal"))
    assert proc.returncode == 0, proc.stderr
    assert "azd env set AZURE_PRINCIPAL_TYPE ServicePrincipal" in cli_calls(log)


def test_preflight_records_a_user_caller(fake_bin: Path, log: Path) -> None:
    proc = run_hook(PREFLIGHT, fake_bin, log, enabled_env(FAKE_USER_TYPE="user"))
    assert proc.returncode == 0, proc.stderr
    assert "azd env set AZURE_PRINCIPAL_TYPE User" in cli_calls(log)


def test_preflight_keeps_an_explicit_principal_type(fake_bin: Path, log: Path) -> None:
    proc = run_hook(PREFLIGHT, fake_bin, log, enabled_env(AZURE_PRINCIPAL_TYPE="ServicePrincipal"))
    assert proc.returncode == 0, proc.stderr
    assert not any("AZURE_PRINCIPAL_TYPE" in call for call in cli_calls(log) if call.startswith("azd"))


def test_preflight_needs_no_existing_outputs(fake_bin: Path, log: Path) -> None:
    """A brand-new environment has no azd outputs yet; preflight must not need any."""
    env = enabled_env()
    assert not any(key.startswith("SRE_AGENT_") for key in env)
    proc = run_hook(PREFLIGHT, fake_bin, log, env)
    assert proc.returncode == 0, proc.stderr


# ---------------------------------------------------------------------------
# Setup — result contract
# ---------------------------------------------------------------------------


def test_setup_reports_disabled_without_calling_azure(fake_bin: Path, log: Path) -> None:
    proc = run_hook(SETUP, fake_bin, log)
    assert proc.returncode == 0
    assert result_line(proc) == "SRE_RESULT core=disabled telemetry=disabled github=disabled"
    assert cli_calls(log) == []


def test_setup_rejects_a_malformed_boolean(fake_bin: Path, log: Path) -> None:
    proc = run_hook(SETUP, fake_bin, log, {"DEPLOY_SRE_AGENT": "on-ish"})
    assert proc.returncode != 0
    assert cli_calls(log) == []


def test_setup_reports_core_ready_in_core_only_mode(fake_bin: Path, log: Path) -> None:
    proc = run_hook(SETUP, fake_bin, log, enabled_env(SRE_AGENT_ID=AGENT_ID, SRE_AGENT_NAME="sre-demo"))
    assert proc.returncode == 0, proc.stderr
    assert result_line(proc) == "SRE_RESULT core=ready telemetry=disabled github=disabled"
    calls = cli_calls(log)
    assert len(calls) == 1
    assert AGENT_ID in calls[0]
    assert "2025-05-01-preview" in calls[0]


def test_core_only_mode_makes_no_connector_or_github_calls(fake_bin: Path, log: Path) -> None:
    run_hook(SETUP, fake_bin, log, enabled_env(SRE_AGENT_ID=AGENT_ID))
    for call in cli_calls(log):
        assert "rest" not in call
        assert "github" not in call.lower()


def test_setup_reports_unimplemented_integrations_as_pending(fake_bin: Path, log: Path) -> None:
    """Not implemented yet is `pending` — never `ready` and never `unavailable`."""
    proc = run_hook(
        SETUP,
        fake_bin,
        log,
        enabled_env(
            SRE_AGENT_ID=AGENT_ID,
            SRE_CONNECT_TELEMETRY="true",
            SRE_CONNECT_GITHUB="true",
        ),
    )
    assert proc.returncode == 0, proc.stderr
    assert result_line(proc) == "SRE_RESULT core=ready telemetry=pending github=pending"
    assert cli_calls(log) == [call for call in cli_calls(log) if "resource show" in call]


def test_setup_fails_without_a_provisioned_agent(fake_bin: Path, log: Path) -> None:
    proc = run_hook(SETUP, fake_bin, log, enabled_env())
    assert proc.returncode != 0
    assert result_line(proc).startswith("SRE_RESULT core=failed")
    assert "azd provision" in proc.stderr


def test_setup_fails_fast_on_a_failed_provisioning_state(fake_bin: Path, log: Path) -> None:
    proc = run_hook(
        SETUP,
        fake_bin,
        log,
        enabled_env(SRE_AGENT_ID=AGENT_ID, FAKE_STATES="Failed"),
    )
    assert proc.returncode != 0
    assert result_line(proc).startswith("SRE_RESULT core=failed")
    assert len(cli_calls(log)) == 1


def test_setup_readiness_retry_is_bounded(fake_bin: Path, log: Path) -> None:
    proc = run_hook(
        SETUP,
        fake_bin,
        log,
        enabled_env(
            SRE_AGENT_ID=AGENT_ID,
            FAKE_STATES="Provisioning",
            SRE_READY_ATTEMPTS="3",
            SRE_READY_DELAY_SECONDS="0",
        ),
    )
    assert proc.returncode != 0
    assert "3 attempts" in proc.stderr
    assert len(cli_calls(log)) == 3
    assert result_line(proc).startswith("SRE_RESULT core=failed")


def test_setup_succeeds_once_the_agent_settles(fake_bin: Path, log: Path) -> None:
    proc = run_hook(
        SETUP,
        fake_bin,
        log,
        enabled_env(
            SRE_AGENT_ID=AGENT_ID,
            FAKE_STATES="Provisioning Succeeded",
            SRE_READY_ATTEMPTS="4",
            SRE_READY_DELAY_SECONDS="0",
        ),
    )
    assert proc.returncode == 0, proc.stderr
    assert result_line(proc).startswith("SRE_RESULT core=ready")
    assert len(cli_calls(log)) == 2


def test_setup_reports_missing_azure_cli(fake_bin: Path, log: Path, bin_without_az: Path) -> None:
    proc = run_hook(
        SETUP,
        fake_bin,
        log,
        enabled_env(SRE_AGENT_ID=AGENT_ID),
        path=str(bin_without_az),
    )
    assert proc.returncode != 0
    assert "Azure CLI" in proc.stderr
    assert result_line(proc).startswith("SRE_RESULT core=failed")


def test_setup_converges_on_repeat_runs(fake_bin: Path, log: Path) -> None:
    env = enabled_env(SRE_AGENT_ID=AGENT_ID)
    first = run_hook(SETUP, fake_bin, log, env)
    second = run_hook(SETUP, fake_bin, log, env)
    assert first.returncode == second.returncode == 0
    assert result_line(first) == result_line(second)


@pytest.mark.parametrize(
    "env",
    [
        {"SRE_AGENT_ID": AGENT_ID},
        {"SRE_AGENT_ID": AGENT_ID, "FAKE_STATES": "Failed"},
        {},
    ],
    ids=["success", "failure", "no-agent"],
)
def test_setup_never_echoes_secrets(fake_bin: Path, log: Path, env: dict[str, str]) -> None:
    proc = run_hook(SETUP, fake_bin, log, enabled_env(**env))
    assert SENTINEL_SECRET not in proc.stdout
    assert SENTINEL_SECRET not in proc.stderr
