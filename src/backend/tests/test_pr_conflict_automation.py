"""Exercise the real conflict-delegation script with a fake GitHub transport."""

import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = yaml.safe_load((REPO_ROOT / ".github/workflows/pr-resolve-conflicts.yml").read_text())
SCRIPT = WORKFLOW["jobs"]["resolve"]["steps"][0]["run"]
MARKER = "<!-- pr-resolve-conflicts: head base -->"


@pytest.fixture
def run_conflicts(tmp_path):
    gh = tmp_path / "gh"
    gh.write_text(
        """#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
fixture = json.loads(os.environ["GH_FIXTURE"])
with open(os.environ["GH_CALLS"], "a") as log:
    log.write(json.dumps(args) + "\\n")
if args[:2] == ["pr", "list"]:
    print("17")
elif args[:2] == ["api", "graphql"]:
    print(json.dumps(fixture["pr"]))
elif args[0] == "api" and args[1].endswith("/comments") and "-f" not in args:
    print(json.dumps(fixture["comment_pages"]))
elif args[0] == "api" and "-f" in args:
    if fixture.get("fail_write"):
        print("permission denied", file=sys.stderr)
        sys.exit(1)
    print("{}")
else:
    print("Unexpected request: " + repr(args), file=sys.stderr)
    sys.exit(1)
"""
    )
    gh.chmod(0o755)
    sleep = tmp_path / "sleep"
    sleep.write_text("#!/bin/sh\nexit 0\n")
    sleep.chmod(0o755)
    calls = tmp_path / "calls.jsonl"

    def run(
        *,
        state="OPEN",
        mergeable="CONFLICTING",
        comments=None,
        comment_pages=None,
        labels=None,
        authenticated=True,
        fail_write=False,
    ):
        fixture = {
            "pr": {
                "state": state,
                "mergeable": mergeable,
                "headRefOid": "head",
                "baseRefOid": "base",
                "baseRefName": "main",
                "labels": {"nodes": [{"name": name} for name in labels or []]},
            },
            "comment_pages": comment_pages if comment_pages is not None else [comments or []],
            "fail_write": fail_write,
        }
        result = subprocess.run(
            ["bash", "-c", SCRIPT],
            env={
                **os.environ,
                "PATH": f"{tmp_path}:{os.environ['PATH']}",
                "GH_TOKEN": "synthetic" if authenticated else "",
                "REPO": "synthetic/repo",
                "EVENT_PR": "",
                "DISPATCH_PR": "",
                "MAX_ROUNDS": "3",
                "GH_FIXTURE": json.dumps(fixture),
                "GH_CALLS": str(calls),
            },
            capture_output=True,
            text=True,
            timeout=15,
        )
        requests = [json.loads(line) for line in calls.read_text().splitlines()] if calls.exists() else []
        return result, requests

    return run


def writes(requests, endpoint):
    return [args for args in requests if args[0] == "api" and args[1].endswith(endpoint) and "-f" in args]


def test_conflict_delegates_to_copilot_on_same_branch(run_conflicts):
    result, requests = run_conflicts()
    assert result.returncode == 0, result.stderr
    comments = writes(requests, "/comments")
    assert len(comments) == 1
    body = comments[0][-1]
    assert "@copilot" in body
    assert "same branch" in body
    assert "Do not force-push or bypass review/CI" in body
    assert MARKER in body
    assert any("--slurp" in args and "--paginate" in args for args in requests)


@pytest.mark.parametrize("state,mergeable", [("MERGED", "CONFLICTING"), ("OPEN", "MERGEABLE"), ("OPEN", "UNKNOWN")])
def test_nonconflicts_do_not_delegate(run_conflicts, state, mergeable):
    result, requests = run_conflicts(state=state, mergeable=mergeable)
    assert result.returncode == 0, result.stderr
    assert not writes(requests, "/comments")


def test_duplicate_head_and_base_do_not_delegate(run_conflicts):
    result, requests = run_conflicts(comments=[{"body": MARKER}])
    assert result.returncode == 0, result.stderr
    assert not writes(requests, "/comments")


def test_duplicate_on_later_comment_page_does_not_delegate(run_conflicts):
    result, requests = run_conflicts(comment_pages=[[{"body": "old comment"}], [{"body": MARKER}]])
    assert result.returncode == 0, result.stderr
    assert not writes(requests, "/comments")


def test_new_base_commit_can_trigger_another_repair(run_conflicts):
    result, requests = run_conflicts(comments=[{"body": "<!-- pr-resolve-conflicts: head oldbase -->"}])
    assert result.returncode == 0, result.stderr
    assert len(writes(requests, "/comments")) == 1


def test_round_cap_requires_human(run_conflicts):
    result, requests = run_conflicts(
        comments=[{"body": f"<!-- pr-resolve-conflicts: head{i} oldbase -->"} for i in range(3)]
    )
    assert result.returncode == 0, result.stderr
    assert not writes(requests, "/comments")
    assert writes(requests, "/labels")[0][-1] == "labels[]=needs-human"


def test_needs_human_stops_automatic_repairs(run_conflicts):
    result, requests = run_conflicts(labels=["needs-human"])
    assert result.returncode == 0, result.stderr
    assert not writes(requests, "/comments")


def test_missing_pat_fails_without_github_token_fallback(run_conflicts):
    result, requests = run_conflicts(authenticated=False)
    assert result.returncode != 0
    assert "COPILOT_ASSIGN_TOKEN is required" in result.stdout
    assert requests == []


def test_delegation_failure_is_not_reported_as_success(run_conflicts):
    result, _ = run_conflicts(fail_write=True)
    assert result.returncode != 0
    assert "Delegated conflicts" not in result.stdout


def test_conflict_workflow_never_executes_pr_code():
    trigger = WORKFLOW[True]
    assert {"pull_request_target", "push", "workflow_run", "schedule", "workflow_dispatch"} == set(trigger)
    assert trigger["push"]["branches"] == ["main"]
    assert WORKFLOW["permissions"] == {"contents": "read", "pull-requests": "read"}
    assert len(WORKFLOW["jobs"]["resolve"]["steps"]) == 1
    assert "git checkout" not in SCRIPT
