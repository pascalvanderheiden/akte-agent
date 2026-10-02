"""Verify dependency-aware assignment against the actual workflow script."""

import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = yaml.safe_load((ROOT / ".github/workflows/assign-copilot.yml").read_text())
SCRIPT = WORKFLOW["jobs"]["assign"]["steps"][0]["run"]


@pytest.fixture
def run_assignment(tmp_path):
    gh = tmp_path / "gh"
    gh.write_text(
        """#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
fixture = json.loads(os.environ["GH_FIXTURE"])
payload = json.load(sys.stdin) if "--input" in args else None
with open(os.environ["GH_CALLS"], "a") as log:
    log.write(json.dumps({"args": args, "payload": payload}) + "\\n")
if args[:2] == ["issue", "list"]:
    print("17")
elif args[0] == "api" and "blocked_by" in args[2]:
    print("\\n".join(str(item["number"]) for item in fixture["blockers"] if item["state"] != "closed"))
elif payload:
    if fixture["fail_write"]:
        sys.exit(1)
    print("copilot-swe-agent")
elif any("suggestedActors" in arg for arg in args):
    print("bot-id")
else:
    print(json.dumps(fixture["issue"]))
"""
    )
    gh.chmod(0o755)
    calls = tmp_path / "calls.jsonl"

    def run(
        *,
        blockers=None,
        state="OPEN",
        ready=True,
        assigned=False,
        sweep=False,
        fail_write=False,
        linked=True,
        complete=True,
    ):
        fixture = {
            "issue": {
                "id": "issue-id",
                "state": state,
                "labels": {"nodes": [{"name": "ticket"}] + ([{"name": "ready-for-agent"}] if ready else [])},
                "parent": {"number": 16, "labels": {"nodes": [] if complete else [{"name": "to-ticket"}]}}
                if linked
                else None,
                "assignedActors": {
                    "nodes": [{"id": "human-id", "login": "human"}]
                    + ([{"id": "bot-id", "login": "copilot-swe-agent"}] if assigned else [])
                },
            },
            "blockers": blockers or [],
            "fail_write": fail_write,
        }
        result = subprocess.run(
            ["bash", "-c", SCRIPT],
            env={
                **os.environ,
                "PATH": f"{tmp_path}:{os.environ['PATH']}",
                "GH_TOKEN": "synthetic",
                "REPO": "synthetic/repo",
                "ISSUE_NUMBER": "" if sweep else "17",
                "GH_FIXTURE": json.dumps(fixture),
                "GH_CALLS": str(calls),
            },
            capture_output=True,
            text=True,
            timeout=15,
        )
        requests = [json.loads(line) for line in calls.read_text().splitlines()]
        return result, requests

    return run


@pytest.mark.parametrize(
    "options",
    [
        {"blockers": [{"number": 16, "state": "open"}]},
        {"state": "CLOSED"},
        {"ready": False},
        {"assigned": True},
        {"linked": False},
        {"complete": False},
    ],
)
def test_ineligible_ticket_never_assigns(run_assignment, options):
    result, requests = run_assignment(**options)
    assert result.returncode == 0, result.stderr
    assert not any(request["payload"] for request in requests)


@pytest.mark.parametrize("sweep", [False, True])
def test_closed_blockers_release_ticket_preserving_assignees(run_assignment, sweep):
    result, requests = run_assignment(blockers=[{"number": 16, "state": "closed"}], sweep=sweep)
    assert result.returncode == 0, result.stderr
    mutations = [request["payload"] for request in requests if request["payload"]]
    assert len(mutations) == 1
    assert mutations[0]["variables"]["actorIds"] == ["human-id", "bot-id"]
    assert any("--paginate" in request["args"] for request in requests)


def test_assignment_write_failure_is_fatal(run_assignment):
    result, _ = run_assignment(fail_write=True)
    assert result.returncode != 0
    assert "Assigned Copilot" not in result.stdout


def test_merge_releases_tickets_and_does_not_gate_on_conflict_helper():
    assert "unlabeled" in WORKFLOW[True]["issues"]["types"]
    assert "to-ticket" in WORKFLOW["jobs"]["assign"]["if"]
    merge = yaml.safe_load((ROOT / ".github/workflows/pr-auto-merge.yml").read_text())
    release = merge["jobs"]["release-tickets"]
    assert release["needs"] == "merge"
    assert release["if"] == "needs.merge.outputs.merged == 'true'"
    assert "gh workflow run assign-copilot.yml" in release["steps"][0]["run"]
    script = merge["jobs"]["merge"]["steps"][-1]["run"]
    assert "Resolve PR merge conflicts" in script
    assert "actions/workflows/pr-resolve-conflicts.yml/dispatches" in script
    assert "CONFLICTING" in script


@pytest.mark.parametrize("mergeable", ["CONFLICTING", "UNKNOWN"])
def test_merge_gate_dispatches_conflicts_without_merging(tmp_path, mergeable):
    merge = yaml.safe_load((ROOT / ".github/workflows/pr-auto-merge.yml").read_text())
    script = merge["jobs"]["merge"]["steps"][-1]["run"].replace("${{ github.event.repository.default_branch }}", "main")
    gh = tmp_path / "gh"
    gh.write_text(
        """#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ["GH_CALLS"], "a") as log:
    log.write(json.dumps(args) + "\\n")
if args[:2] == ["pr", "view"]:
    print(json.dumps({"state": "OPEN", "isDraft": False, "headRefOid": "head",
                      "mergeable": os.environ["MERGEABLE"]}))
elif args[0] == "api" and args[1].endswith("/dispatches"):
    print("{}")
else:
    print("Unexpected request: " + repr(args), file=sys.stderr)
    sys.exit(1)
"""
    )
    gh.chmod(0o755)
    calls = tmp_path / "calls.jsonl"
    result = subprocess.run(
        ["bash", "-c", script],
        env={
            **os.environ,
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "REPO": "synthetic/repo",
            "PRS": "17",
            "GITHUB_OUTPUT": str(tmp_path / "output"),
            "MERGEABLE": mergeable,
            "GH_CALLS": str(calls),
        },
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    requests = [json.loads(line) for line in calls.read_text().splitlines()]
    dispatches = [args for args in requests if args[0] == "api"]
    assert len(dispatches) == (1 if mergeable == "CONFLICTING" else 0)
    assert not any(args[:2] == ["pr", "merge"] for args in requests)
    if dispatches:
        assert "ref=main" in dispatches[0]
        assert "inputs[pr_number]=17" in dispatches[0]
