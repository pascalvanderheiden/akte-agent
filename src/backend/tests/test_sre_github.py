"""Exercise GitHub setup through the real hook, with isolated CLI/HTTP processes."""

from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path

import pytest
import yaml

from tests.test_sre_hooks import (
    REPO_ROOT,
    SENTINEL_SECRET,
    SETUP,
    cli_calls,
    fake_bin,
    log,
    result_line,
    run_hook,
    setup_env,
)

# Re-export shared process-boundary fixtures without a second harness.
__all__ = ["fake_bin", "log"]

ENDPOINT = "https://synthetic-agent.example.invalid"
TOKEN = "SYNTHETIC_AZURE_BEARER"  # noqa: S105
URL = "https://github.com/pascalvanderheiden/akte-agent"
SHA = "a" * 40

FAKE_CURL = r"""import json, os, stat, sys
from pathlib import Path
args = sys.argv[1:]
assert "SRE_GITHUB_PAT" not in os.environ
assert "--location" not in args
assert args[0] == "--disable"
assert args[args.index("--proto") + 1] == "=https"
config = sys.stdin.read()
headers = [json.loads(line.split(" = ", 1)[1]) for line in config.splitlines() if line.startswith("header = ")]
url = args[args.index("--url") + 1]
method = args[args.index("--request") + 1]
expected = os.environ["FAKE_BEARER"] if url.startswith(os.environ["FAKE_ORIGIN"]) else os.environ.get("FAKE_PAT", "")
auth = [header for header in headers if header.startswith("Authorization:")]
assert auth == (["Authorization: Bearer " + expected] if expected else [])
for secret in (os.environ["FAKE_BEARER"], os.environ.get("FAKE_PAT", "")):
    assert not secret or secret not in "\n".join(args)
body = None
for line in config.splitlines():
    if line.startswith("data-binary = "):
        path = Path(json.loads(line.split(" = ", 1)[1])[1:])
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
        body = json.loads(path.read_text())
        if "pat" in body:
            assert body["pat"] == os.environ["FAKE_PAT"]
            body["pat"] = "<redacted>"
log = Path(os.environ["FAKE_LOG"])
calls = [json.loads(line) for line in log.read_text().splitlines()]
index = sum(call["cli"] == "curl" for call in calls)
with log.open("a") as stream:
    stream.write(json.dumps({"cli": "curl", "args": args, "method": method, "url": url, "body": body}) + "\n")
responses = json.loads(os.environ["FAKE_HTTP"])
assert index < len(responses), "Unexpected HTTP call"
item = responses[index]
assert method == item["method"], (method, item["method"])
assert url == item["url"], (url, item["url"])
output = Path(args[args.index("--output") + 1])
output.write_text(item.get("raw", json.dumps(item.get("body"))))
print(item.get("status", 200), end="")
if item.get("exit"):
    print(os.environ["FAKE_BEARER"] + os.environ.get("FAKE_PAT", ""), file=sys.stderr)
sys.exit(item.get("exit", 0))
"""


@pytest.fixture(autouse=True)
def http_boundary(fake_bin: Path) -> None:
    (fake_bin / "curl").write_text(f"#!{sys.executable}\n{FAKE_CURL}")


def response(path: str, body: object = None, *, method: str = "GET", status: int = 200, **extra: object) -> dict:
    return {
        "url": path if path.startswith("https://") else ENDPOINT + path,
        "method": method,
        "status": status,
        "body": body,
        **extra,
    }


def registration(url: str = URL, branch: str = "main", **props: object) -> dict:
    name = "github-" + hashlib.sha256(f"{url}\n{branch}".encode()).hexdigest()[:24]
    return {
        "name": name,
        "type": "CodeRepo",
        "properties": {"type": "GitHub", "url": url, "branch": branch, **props},
    }


def sequence(
    *, url: str = URL, branch: str = "main", existing: bool = False, pat: bool = False, discover: bool = True
) -> list[dict]:
    repo = registration(url, branch, cloneStatus="Ready", latestCommit=SHA)
    slug = url.removeprefix("https://github.com/")
    from urllib.parse import quote

    items = [response("/api/v2/github/domains", {"values": [] if pat else [{"name": "github_com"}]})]
    if pat:
        items += [
            response("/api/v2/github/domains/github_com", {}, method="PUT"),
            response("/api/v2/github/domains", {"values": [{"name": "github.com"}]}),
        ]
    if discover:
        items.append(response(f"https://api.github.com/repos/{slug}", {"default_branch": branch}))
    items += [
        response(
            f"https://api.github.com/repos/{slug}/branches/{quote(branch, safe='')}",
            {"name": branch, "commit": {"sha": SHA}},
        ),
        response("/api/v2/repos", {"value": [repo] if existing else []}),
    ]
    if not existing:
        items.append(response("/api/v2/repos/" + repo["name"], {}, method="PUT"))
    items.append(response("/api/v2/repos/" + repo["name"], copy.deepcopy(repo)))
    return items


def settings(items: list[dict], **overrides: str) -> dict[str, str]:
    return setup_env(
        **{
            "SRE_CONNECT_GITHUB": "true",
            "FAKE_ENDPOINT": json.dumps(ENDPOINT),
            "FAKE_TOKEN": json.dumps(TOKEN),
            "FAKE_BEARER": TOKEN,
            "FAKE_ORIGIN": ENDPOINT,
            "FAKE_PAT": SENTINEL_SECRET,
            "FAKE_HTTP": json.dumps(items),
            **overrides,
        }
    )


def check(proc, fake_bin: Path, state: str, exit_code: int = 0) -> None:
    assert proc.returncode == exit_code, proc.stdout + proc.stderr
    assert result_line(proc) == f"SRE_RESULT core=ready telemetry=disabled github={state}"
    assert TOKEN not in proc.stdout + proc.stderr
    assert list(fake_bin.parent.glob("sre-github-*")) == []


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("pat", [False, True])
def test_registration_readback_does_not_claim_fresh_access(
    existing: bool, pat: bool, fake_bin: Path, log: Path
) -> None:
    items = sequence(existing=existing, pat=pat)
    proc = run_hook(SETUP, fake_bin, log, settings(items))
    check(proc, fake_bin, "pending")
    assert "current GitHub commit verified" in proc.stderr
    assert "cached Ready clone does not prove" in proc.stderr
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    writes = [call for call in calls if call.get("method") == "PUT"]
    assert len(writes) == int(not existing) + int(pat)
    assert cli_calls(log, "az", ("account", "get-access-token"))[0][3:] == [
        "--resource",
        "https://azuresre.dev",
        "--subscription",
        setup_env()["AZURE_SUBSCRIPTION_ID"],
        "--query",
        "accessToken",
        "--output",
        "json",
        "--only-show-errors",
    ]
    assert SENTINEL_SECRET not in log.read_text()


def test_repeat_reuses_authorization_and_registration(fake_bin: Path, log: Path) -> None:
    items = sequence() + sequence(existing=True)
    for _ in range(2):
        check(run_hook(SETUP, fake_bin, log, settings(items)), fake_bin, "pending")
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert sum(call.get("method") == "PUT" for call in calls) == 1
    assert len(cli_calls(log, "az", ("resource", "show"))) == 4


@pytest.mark.parametrize("caller", ["user", "servicePrincipal"])
def test_explicit_target_branch_and_standalone_environment(caller: str, fake_bin: Path, log: Path) -> None:
    url, branch = "https://github.com/example/project", "release/test"
    env = settings(
        sequence(url=url, branch=branch, discover=False),
        SRE_GITHUB_REPOSITORY_URL=url,
        SRE_GITHUB_BRANCH=branch,
        FAKE_ACCOUNT=json.dumps(
            {
                "id": setup_env()["AZURE_SUBSCRIPTION_ID"],
                "tenantId": setup_env()["AZURE_TENANT_ID"],
                "user": {"type": caller},
            }
        ),
    )
    proc = run_hook(SETUP, fake_bin, log, env, args=("--environment", "demo"))
    check(proc, fake_bin, "pending")
    assert "release%2Ftest" in log.read_text()


@pytest.mark.parametrize("domains", [[], [{"name": "wrong_ghe_com"}]])
def test_missing_or_wrong_domain_without_pat_never_registers(domains: list, fake_bin: Path, log: Path) -> None:
    proc = run_hook(
        SETUP,
        fake_bin,
        log,
        settings([response("/api/v2/github/domains", {"values": domains})], SRE_GITHUB_PAT="", FAKE_PAT=""),
    )
    check(proc, fake_bin, "pending")
    assert "No SRE-side authorization" in proc.stderr
    assert len(cli_calls(log, "curl")) == 1


@pytest.mark.parametrize("status", [401, 403, 404])
def test_failed_default_branch_discovery_requests_override(status: int, fake_bin: Path, log: Path) -> None:
    items = sequence()[:2]
    items[1]["status"] = status
    check(run_hook(SETUP, fake_bin, log, settings(items)), fake_bin, "pending")
    assert len(cli_calls(log, "curl")) == 2


@pytest.mark.parametrize(
    "changes, state, message",
    [
        ({"cloneStatus": "Ready", "latestCommit": "b" * 40}, "pending", "not verified"),
        ({"cloneStatus": "Pending", "latestCommit": None}, "pending", "not verified"),
        ({"cloneStatus": "Failed", "errorMessage": "token expired " + TOKEN}, "pending", "Renew expired"),
        ({"cloneStatus": "Failed", "errorMessage": "unknown failure " + TOKEN}, "failed", "unexpected clone failure"),
        ({"branch": "wrong"}, "failed", "does not match"),
        ({"url": "https://github.com/example/wrong"}, "failed", "does not match"),
        ({"cloneStatus": "Ready", "latestCommit": 5}, "failed", "Malformed"),
        ({"cloneStatus": "Invented"}, "failed", "Unknown"),
    ],
)
def test_clone_proof_and_auth_errors(changes: dict, state: str, message: str, fake_bin: Path, log: Path) -> None:
    items = sequence(existing=True)
    items[-1]["body"]["properties"].update(changes)
    if state == "pending" and changes.get("cloneStatus") != "Failed":
        items.append(items[-1])
    proc = run_hook(SETUP, fake_bin, log, settings(items))
    check(proc, fake_bin, state, int(state == "failed"))
    assert message in proc.stderr
    assert all(json.loads(line).get("method") != "PUT" for line in log.read_text().splitlines())


def test_unverifiable_branch_commit_cannot_make_cached_clone_ready(fake_bin: Path, log: Path) -> None:
    items = sequence(existing=True)
    items[2]["status"] = 404
    items.append(items[-1])
    proc = run_hook(SETUP, fake_bin, log, settings(items))
    check(proc, fake_bin, "pending")
    assert "not verified" in proc.stderr


@pytest.mark.parametrize(
    "status, state",
    [
        (401, "pending"),
        (403, "unavailable"),
        (501, "unavailable"),
        (400, "failed"),
        (404, "failed"),
        (500, "failed"),
        (302, "failed"),
    ],
)
def test_http_classification_no_permanent_retries(status: int, state: str, fake_bin: Path, log: Path) -> None:
    items = [response("/api/v2/github/domains", status=status, raw=TOKEN + SENTINEL_SECRET)]
    check(run_hook(SETUP, fake_bin, log, settings(items)), fake_bin, state, int(state == "failed"))
    assert len(cli_calls(log, "curl")) == 1


@pytest.mark.parametrize("status", [429, 502, 503, 504])
@pytest.mark.parametrize("recover", [True, False])
def test_bounded_http_readiness(status: int, recover: bool, fake_bin: Path, log: Path) -> None:
    transient = response("/api/v2/github/domains", status=status)
    items = [transient, *sequence()] if recover else [transient, transient]
    check(
        run_hook(SETUP, fake_bin, log, settings(items)), fake_bin, "pending" if recover else "failed", int(not recover)
    )


@pytest.mark.parametrize("exit_code", [6, 7, 28, 60])
def test_network_and_tls_failure_redaction(exit_code: int, fake_bin: Path, log: Path) -> None:
    item = response("/api/v2/github/domains", exit=exit_code)
    check(run_hook(SETUP, fake_bin, log, settings([item, item])), fake_bin, "failed", 1)
    assert len(cli_calls(log, "curl")) == (1 if exit_code == 60 else 2)


@pytest.mark.parametrize("raw", ["not json", "null", "{}", '{"values":[5]}', '{"values":[{"name":null}]}'])
def test_malformed_domains_fail(raw: str, fake_bin: Path, log: Path) -> None:
    items = [response("/api/v2/github/domains", raw=raw)]
    check(run_hook(SETUP, fake_bin, log, settings(items)), fake_bin, "failed", 1)


@pytest.mark.parametrize(
    "error,state",
    [("AADSTS53003 policy", "unavailable"), ("Please run 'az login'", "unavailable"), ("Unexpected error", "failed")],
)
def test_token_unavailable_vs_unexpected(error: str, state: str, fake_bin: Path, log: Path) -> None:
    env = settings([], **{"FAKE_TOKEN_EXIT": "1", "FAKE_TOKEN_ERROR": error + TOKEN})
    check(run_hook(SETUP, fake_bin, log, env), fake_bin, state, int(state == "failed"))
    assert cli_calls(log, "curl") == []


@pytest.mark.parametrize(
    "key,value",
    [
        ("SRE_GITHUB_REPOSITORY_URL", "https://github.com/owner/repo?token=" + SENTINEL_SECRET),
        ("SRE_GITHUB_REPOSITORY_URL", "https://other.invalid/owner/repo"),
        ("SRE_GITHUB_BRANCH", "../bad"),
        ("SRE_GITHUB_BRANCH", "bad\nbranch"),
        ("SRE_GITHUB_ATTEMPTS", "0"),
        ("SRE_GITHUB_ATTEMPTS", "11"),
        ("SRE_GITHUB_DELAY_SECONDS", "31"),
        ("SRE_GITHUB_PAT", "bad\nsecret"),
    ],
)
def test_invalid_configuration_is_redacted(key: str, value: str, fake_bin: Path, log: Path) -> None:
    check(run_hook(SETUP, fake_bin, log, settings([], **{key: value})), fake_bin, "failed", 1)
    assert cli_calls(log, "curl") == []


def test_enterprise_pat_is_never_sent(fake_bin: Path, log: Path) -> None:
    env = settings(
        [response("/api/v2/github/domains", {"values": []})],
        SRE_GITHUB_REPOSITORY_URL="https://example.ghe.com/owner/repo",
    )
    proc = run_hook(SETUP, fake_bin, log, env)
    check(proc, fake_bin, "pending")
    assert "BYO GitHub App" in proc.stderr


def test_existing_other_repository_configuration_is_preserved(fake_bin: Path, log: Path) -> None:
    items = sequence()
    items[3]["body"]["value"] = [registration("https://github.com/example/other")]
    check(run_hook(SETUP, fake_bin, log, settings(items)), fake_bin, "pending")
    writes = [json.loads(line) for line in log.read_text().splitlines() if json.loads(line).get("method") == "PUT"]
    assert len(writes) == 1
    assert writes[0]["body"]["properties"]["url"] == URL


def test_github_failure_does_not_rewrite_telemetry_state(fake_bin: Path, log: Path) -> None:
    env = settings([response("/api/v2/github/domains", status=500)], SRE_CONNECT_TELEMETRY="true")
    proc = run_hook(SETUP, fake_bin, log, env)
    assert proc.returncode == 1
    assert result_line(proc) == "SRE_RESULT core=ready telemetry=pending github=failed"
    assert "SRE_TELEMETRY_RESULT app_insights=pending log_analytics=pending" in proc.stdout
    assert "Telemetry app-insights configured/read back" in proc.stdout
    assert "Telemetry log-analytics configured/read back" in proc.stdout
    assert len(cli_calls(log, "az", ("deployment", "group", "create"))) == 2


def test_pat_workflow_scope_and_entry_point() -> None:
    workflow = yaml.safe_load((REPO_ROOT / ".github/workflows/deploy.yml").read_text())
    steps = workflow["jobs"]["deploy"]["steps"]
    holders = [step for step in steps if "SRE_GITHUB_PAT" in json.dumps(step)]
    assert len(holders) == 1
    step = holders[0]
    assert step["name"] == "Set up SRE GitHub access"
    assert step["run"] == './hooks/sre-setup.sh --environment "$AZURE_ENV_NAME"'
    assert step["env"]["SRE_GITHUB_PAT"] == "${{ secrets.SRE_GITHUB_PAT }}"
    assert "SRE_GITHUB_PAT" not in json.dumps(workflow.get("env", {}))


@pytest.mark.parametrize("response_index", [1, 2, 3, 5])
def test_malformed_branch_collection_and_readback(response_index: int, fake_bin: Path, log: Path) -> None:
    items = sequence()
    items[response_index]["raw"] = '{"bad":"' + TOKEN + '"}'
    check(run_hook(SETUP, fake_bin, log, settings(items)), fake_bin, "failed", 1)


@pytest.mark.parametrize("kind", ["duplicate", "collision", "pagination"])
def test_registration_preserves_conflicting_configuration(kind: str, fake_bin: Path, log: Path) -> None:
    items = sequence()
    repo = registration()
    if kind == "duplicate":
        other = copy.deepcopy(repo)
        other["name"] = "manually-added"
        items[3]["body"] = {"value": [repo, other]}
    elif kind == "collision":
        repo["properties"]["url"] = "https://github.com/example/different"
        items[3]["body"] = {"value": [repo]}
    else:
        items[3]["body"] = {"value": [], "nextLink": ENDPOINT + "/next"}
    check(run_hook(SETUP, fake_bin, log, settings(items)), fake_bin, "failed", 1)
    assert all(json.loads(line).get("method") != "PUT" for line in log.read_text().splitlines())


def test_existing_manual_name_is_reused(fake_bin: Path, log: Path) -> None:
    items = sequence(existing=True)
    items[3]["body"]["value"][0]["name"] = "operator-repo"
    items[-1]["body"]["name"] = "operator-repo"
    items[-1]["url"] = ENDPOINT + "/api/v2/repos/operator-repo"
    check(run_hook(SETUP, fake_bin, log, settings(items)), fake_bin, "pending")
    assert all(json.loads(line).get("method") != "PUT" for line in log.read_text().splitlines())


@pytest.mark.parametrize("raw", ['""', "null", "5", '"bad\\nvalue"'])
def test_malformed_token_never_reaches_http(raw: str, fake_bin: Path, log: Path) -> None:
    check(run_hook(SETUP, fake_bin, log, settings([], FAKE_TOKEN=raw)), fake_bin, "failed", 1)
    assert cli_calls(log, "curl") == []


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://unsafe.invalid",
        "https://user:secret@unsafe.invalid",
        "https://host.invalid/api",
        "https://host.invalid/?query",
        "https://host.invalid:443",
    ],
)
def test_unsafe_arm_endpoint_never_receives_token(endpoint: str, fake_bin: Path, log: Path) -> None:
    check(run_hook(SETUP, fake_bin, log, settings([], FAKE_ENDPOINT=json.dumps(endpoint))), fake_bin, "failed", 1)
    assert cli_calls(log, "az", ("account", "get-access-token")) == []
    assert cli_calls(log, "curl") == []


@pytest.mark.parametrize("switch", ["DEPLOY_SRE_AGENT", "SRE_CONNECT_GITHUB"])
def test_disabled_setup_has_no_auth_or_http_calls(switch: str, fake_bin: Path, log: Path) -> None:
    proc = run_hook(SETUP, fake_bin, log, settings([], **{switch: "false"}))
    assert proc.returncode == 0
    assert "github=disabled" in result_line(proc)
    assert cli_calls(log, "az", ("account", "get-access-token")) == []
    assert cli_calls(log, "curl") == []


def test_github_enabled_by_default(fake_bin: Path, log: Path) -> None:
    env = settings(sequence())
    del env["SRE_CONNECT_GITHUB"]
    check(run_hook(SETUP, fake_bin, log, env), fake_bin, "pending")
    assert cli_calls(log, "curl")


@pytest.mark.parametrize("status", [401, 403, 400])
def test_pat_failure_never_registers(status: int, fake_bin: Path, log: Path) -> None:
    items = sequence(pat=True)[:2]
    items[1]["status"] = status
    items[1]["raw"] = SENTINEL_SECRET + TOKEN
    state = {401: "pending", 403: "unavailable", 400: "failed"}[status]
    check(run_hook(SETUP, fake_bin, log, settings(items)), fake_bin, state, int(state == "failed"))
    assert len(cli_calls(log, "curl")) == 2


def test_pat_authorization_not_propagated(fake_bin: Path, log: Path) -> None:
    items = sequence(pat=True)[:3]
    items[2]["body"] = {"values": [{"name": "wrong_ghe_com"}]}
    check(run_hook(SETUP, fake_bin, log, settings(items)), fake_bin, "pending")
    assert len(cli_calls(log, "curl")) == 3


def test_missing_python_failure_preserves_core_and_telemetry(fake_bin: Path, log: Path) -> None:
    (fake_bin / "python3").unlink()
    proc = run_hook(SETUP, fake_bin, log, settings([]))
    check(proc, fake_bin, "failed", 1)
    assert "python3" in proc.stderr


def test_invalid_workflow_github_switch_fails_before_persisting(fake_bin: Path, log: Path) -> None:
    workflow = yaml.safe_load((REPO_ROOT / ".github/workflows/deploy.yml").read_text())
    step = next(
        step for step in workflow["jobs"]["deploy"]["steps"] if step.get("name") == "Configure SRE Agent options"
    )
    proc = run_hook(
        SETUP,
        fake_bin,
        log,
        {"SRE_CONNECT_GITHUB_INPUT": "invalid"},
        command=step["run"],
    )
    assert proc.returncode != 0
    assert cli_calls(log) == []


@pytest.mark.parametrize("telemetry", ["disabled", "configured", "pending", "restricted", "failed"])
@pytest.mark.parametrize("github", ["disabled", "configured", "pending", "restricted", "failed"])
def test_combined_optional_integrations(telemetry: str, github: str, fake_bin: Path, log: Path) -> None:
    items = []
    if github == "configured":
        items = sequence()
    elif github != "disabled":
        items = [response("/api/v2/github/domains", status={"pending": 401, "restricted": 403, "failed": 500}[github])]
    env = settings(
        items,
        SRE_CONNECT_TELEMETRY="false" if telemetry == "disabled" else "true",
        SRE_CONNECT_GITHUB="false" if github == "disabled" else "true",
        SRE_READY_ATTEMPTS="2",
        SRE_READY_DELAY_SECONDS="0",
    )
    if telemetry in ("pending", "restricted", "failed"):
        code = {
            "pending": "AuthorizationFailed",
            "restricted": "RequestDisallowedByPolicy",
            "failed": "InvalidTemplate",
        }[telemetry]
        env["FAKE_DEPLOY_APP_ERRORS"] = json.dumps([f"ERROR: ({code})"])
    proc = run_hook(SETUP, fake_bin, log, env, args=("--environment", "demo"))
    ts = {
        "disabled": "disabled",
        "configured": "pending",
        "pending": "pending",
        "restricted": "unavailable",
        "failed": "failed",
    }[telemetry]
    gs = {
        "disabled": "disabled",
        "configured": "pending",
        "pending": "pending",
        "restricted": "unavailable",
        "failed": "failed",
    }[github]
    assert proc.returncode == int("failed" in (telemetry, github)), proc.stdout + proc.stderr
    assert result_line(proc) == f"SRE_RESULT core=ready telemetry={ts} github={gs}"
    law = "disabled" if telemetry == "disabled" else "pending"
    assert f"SRE_TELEMETRY_RESULT app_insights={ts} log_analytics={law}" in proc.stdout
    calls = cli_calls(log)
    deployments = cli_calls(log, "az", ("deployment", "group", "create"))
    assert len(deployments) == (0 if telemetry == "disabled" else 2)
    assert len(cli_calls(log, "curl")) == len(items)
    if telemetry != "disabled":
        assert "Telemetry log-analytics configured/read back" in proc.stdout
    assert len(cli_calls(log, "az", ("account", "get-access-token"))) == int(github != "disabled")
    assert cli_calls(log, "azd", ("env", "set")) == []
    assert all("delete" not in call and "login" not in call and "provision" not in call for call in calls)
    assert TOKEN not in proc.stdout + proc.stderr + log.read_text()
    assert SENTINEL_SECRET not in log.read_text()
    assert list(fake_bin.parent.glob("sre-github-*")) == []
    assert list(fake_bin.parent.glob("tmp.*")) == []


def test_combined_rerun_retains_sources_without_redeploying_core(fake_bin: Path, log: Path) -> None:
    env = settings(sequence(pat=True) + sequence(existing=True), SRE_CONNECT_TELEMETRY="true")
    for _ in range(2):
        proc = run_hook(SETUP, fake_bin, log, env, args=("--environment", "demo"))
        assert proc.returncode == 0, proc.stderr
        assert result_line(proc) == "SRE_RESULT core=ready telemetry=pending github=pending"
        assert "SRE_TELEMETRY_RESULT app_insights=pending log_analytics=pending" in proc.stdout
    deployments = cli_calls(log, "az", ("deployment", "group", "create"))
    assert len(deployments) == 4
    assert all("source=app-insights" in call or "source=log-analytics" in call for call in deployments)
    names = [call[call.index("--name") + 1] for call in deployments]
    assert names[:2] == names[2:]
    writes = [json.loads(line) for line in log.read_text().splitlines() if json.loads(line).get("method") == "PUT"]
    assert len(writes) == 2  # One domain, one repository; neither duplicated on rerun.
    assert cli_calls(log, "az", ("resource", "create")) == []
    assert cli_calls(log, "az", ("deployment", "sub")) == []
    assert cli_calls(log, "azd", ("provision",)) == []
    assert cli_calls(log, "azd", ("env", "set")) == []
    assert TOKEN not in log.read_text() and SENTINEL_SECRET not in log.read_text()
    assert list(fake_bin.parent.glob("sre-github-*")) == []
    assert list(fake_bin.parent.glob("tmp.*")) == []


@pytest.mark.parametrize(
    "telemetry,github", [("true", "true"), ("true", "false"), ("false", "true"), ("false", "false")]
)
def test_workflow_passes_independent_optional_flags(telemetry: str, github: str, fake_bin: Path, log: Path) -> None:
    workflow = yaml.safe_load((REPO_ROOT / ".github/workflows/deploy.yml").read_text())
    inputs = workflow[True]["workflow_dispatch"]["inputs"]
    assert inputs["sre_connect_telemetry"]["default"] is True
    assert inputs["sre_connect_github"]["default"] is True
    step = next(
        step for step in workflow["jobs"]["deploy"]["steps"] if step.get("name") == "Configure SRE Agent options"
    )
    proc = run_hook(
        SETUP,
        fake_bin,
        log,
        {
            "DEPLOY_SRE_AGENT_INPUT": "true",
            "SRE_CONNECT_TELEMETRY_INPUT": telemetry,
            "SRE_CONNECT_GITHUB_INPUT": github,
            "SRE_GITHUB_REPOSITORY_URL_INPUT": URL,
            "SRE_GITHUB_BRANCH_INPUT": "release/test",
            "SRE_LOCATION_INPUT": "",
            "SRE_AGENT_NAME_INPUT": "",
        },
        command=step["run"],
    )
    assert proc.returncode == 0, proc.stderr
    calls = cli_calls(log, "azd", ("env", "set"))
    assert ["azd", "env", "set", "SRE_CONNECT_TELEMETRY", telemetry] in calls
    assert ["azd", "env", "set", "SRE_CONNECT_GITHUB", github] in calls
    assert ["azd", "env", "set", "SRE_GITHUB_BRANCH", "release/test"] in calls
    assert SENTINEL_SECRET not in log.read_text()


def test_workflow_pat_step_runs_both_integrations_without_core_provision(fake_bin: Path, log: Path) -> None:
    workflow = yaml.safe_load((REPO_ROOT / ".github/workflows/deploy.yml").read_text())
    steps = workflow["jobs"]["deploy"]["steps"]
    step = next(step for step in steps if step.get("name") == "Set up SRE GitHub access")
    env = settings(sequence(pat=True), SRE_CONNECT_TELEMETRY="true")
    proc = run_hook(SETUP, fake_bin, log, env, command=step["run"])
    assert proc.returncode == 0, proc.stderr
    assert result_line(proc) == "SRE_RESULT core=ready telemetry=pending github=pending"
    assert len(cli_calls(log, "az", ("deployment", "group", "create"))) == 2
    assert cli_calls(log, "az", ("deployment", "sub")) == []
    assert cli_calls(log, "azd", ("provision",)) == []
    assert cli_calls(log, "azd", ("env", "set")) == []
    assert TOKEN not in proc.stdout + proc.stderr + log.read_text()
    assert SENTINEL_SECRET not in log.read_text()
