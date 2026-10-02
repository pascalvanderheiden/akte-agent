"""Partial planning and review holds must prevent automatic parent closure."""

import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = yaml.safe_load((ROOT / ".github/workflows/close-parent-issues.yml").read_text())
SCRIPT = WORKFLOW["jobs"]["cascade"]["steps"][0]["run"]


@pytest.mark.parametrize("hold", ["to-spec", "to-ticket", "needs-review", "needs-human", "truncated", None])
def test_parent_closure_requires_complete_planning_and_review(tmp_path, hold):
    gh = tmp_path / "gh"
    gh.write_text(
        """#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ["GH_CALLS"], "a") as log:
    log.write(json.dumps(args) + "\\n")
if args[:2] == ["pr", "view"]:
    print("17" if "closingIssuesReferences" in args else "MERGED")
elif args[:2] == ["issue", "view"]:
    print("CLOSED")
elif args[:2] == ["issue", "close"]:
    pass
elif args[:2] == ["api", "graphql"]:
    if "number=16" in args:
        print("null")
    else:
        hold = os.environ["HOLD"]
        print(json.dumps({"number": 16, "state": "OPEN",
                          "labels": {"nodes": [{"name": hold}] if hold not in ("", "truncated") else []},
                          "subIssues": {"totalCount": 2 if hold == "truncated" else 1,
                                        "nodes": [{"number": 17, "state": "CLOSED"}]}}))
else:
    print("Unexpected request: " + repr(args), file=sys.stderr)
    sys.exit(1)
"""
    )
    gh.chmod(0o755)
    calls = tmp_path / "calls.jsonl"
    result = subprocess.run(
        ["bash", "-c", SCRIPT],
        env={
            **os.environ,
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "REPO": "synthetic/repo",
            "PR": "18",
            "HOLD": hold or "",
            "GH_CALLS": str(calls),
        },
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    requests = [json.loads(line) for line in calls.read_text().splitlines()]
    closed = [args for args in requests if args[:2] == ["issue", "close"]]
    assert len(closed) == (1 if hold is None else 0)
    if closed:
        assert closed[0][2] == "16"
