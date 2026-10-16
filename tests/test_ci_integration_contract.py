"""Keep the required CI integration selection equal to every non-optional integration test."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def integration_command():
    ci = yaml.load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    steps = ci["jobs"]["integration-tests"]["steps"]
    runs = [step.get("run", "") for step in steps]
    pytest_runs = [run for run in runs if "-m pytest" in run]
    assert len(pytest_runs) == 1, "exactly one pytest invocation is expected in integration-tests"
    assert any("scripts/ci/check_targets.py" in run for run in runs[:runs.index(pytest_runs[0])]), (
        "target verification must run before the integration tests"
    )
    return shlex.split(pytest_runs[0])[3:]  # drop python -m pytest; keep the pytest arguments


def collected(expression, *paths):
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-o", "addopts=", "-p", "no:cacheprovider",
         "-m", expression, *paths],
        cwd=ROOT, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
    )
    assert result.returncode in (0, 5), result.stdout[-2000:]
    return {line for line in result.stdout.splitlines() if "::" in line}


def test_ci_selects_by_marker_not_by_file_name():
    command = integration_command()
    expression = command[command.index("-m") + 1]
    assert expression == "integration and not integration_optional"
    paths = command[command.index("-m") + 2:]
    assert paths == ["tests"], "a new test file must be picked up without editing the workflow"
    assert not any(token.endswith(".py") for token in command)


@pytest.mark.skipif(os.environ.get("STOCKPILOT_CONTRACT_NO_COLLECT") == "1", reason="collection disabled")
def test_every_integration_test_is_selected_unless_explicitly_optional():
    command = integration_command()
    expression = command[command.index("-m") + 1]
    paths = command[command.index("-m") + 2:]
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [
            pool.submit(collected, "integration", "tests"),
            pool.submit(collected, expression, *paths),
            pool.submit(collected, "integration and integration_optional", "tests"),
            pool.submit(collected, "integration_optional and not integration", "tests"),
        ]
        everything, selected, optional, orphan_optional = [future.result() for future in futures]
    assert orphan_optional == set(), "integration_optional must always be combined with integration"
    assert selected, "the required CI selection would run no integration test"
    assert selected | optional == everything
    assert selected.isdisjoint(optional)
    assert all(re.search(r"lifecycle", node) for node in optional), (
        "only the dedicated-service lifecycle tests may opt out of the required selection"
    )
