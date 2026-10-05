"""Backend CI must use the same locked dependencies for lint and tests."""

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize(
    ("workflow_name", "job_name"),
    [("ci.yml", "backend-lint-test"), ("ci-cd.yml", "lint"), ("ci-cd.yml", "test")],
)
def test_backend_ci_uses_locked_dev_environment(workflow_name, job_name):
    workflow = yaml.safe_load((REPO_ROOT / ".github/workflows" / workflow_name).read_text())
    job = workflow["jobs"][job_name]
    steps = job["steps"]
    setup = next((step for step in steps if step.get("uses", "").startswith("astral-sh/setup-uv@")), None)
    assert setup is not None, "Backend CI must install uv before syncing the lockfile"
    assert setup["with"]["enable-cache"] is True
    assert setup["with"]["cache-dependency-glob"] == "src/backend/uv.lock"

    commands = [step["run"] for step in steps if "run" in step]
    assert any("uv sync --locked --extra dev" in command for command in commands)
    assert not any("pip install" in command for command in commands)
    tools = ("ruff ", "mypy ", "pytest ")
    for command in commands:
        for line in command.splitlines():
            if any(tool in line for tool in tools):
                assert line.strip().startswith("uv run --no-sync "), line
