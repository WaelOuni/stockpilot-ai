import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import yaml

from scripts import eval_ci


def test_workflows_have_separate_deterministic_and_manual_gates():
    workflows = eval_ci.ROOT / ".github" / "workflows"
    ci = yaml.load((workflows / "ci.yml").read_text(), Loader=yaml.BaseLoader)
    real = yaml.load((workflows / "ai-evals.yml").read_text(), Loader=yaml.BaseLoader)
    assert set(ci["on"]) == {"pull_request", "push", "workflow_call"}
    assert set(ci["on"]["push"]["branches"]) == {"main", "dev"}
    assert set(ci["on"]["pull_request"]["branches"]) == {"main", "dev"}
    assert ci["permissions"] == {"contents": "read"}
    backend = ci["jobs"]["backend-quality"]
    commands = [step.get("run", "") for step in backend["steps"]]
    assert "python -m pytest -q" in commands  # Includes offline eval framework tests.
    assert backend["name"] == "Unit Tests"
    framework = ci["jobs"]["eval-framework"]
    assert framework["name"] == "AI Eval Framework"
    assert "--mode deterministic --suite enriched" in framework["steps"][-1]["run"]
    assert not any("--mode local" in cmd for cmd in commands)
    assert ci["jobs"]["e2e"]["if"] == "${{ false }}"
    assert set(real["on"]) == {"workflow_dispatch"}
    job = real["jobs"]["real-evals"]
    assert job["if"] == "${{ github.repository == 'WaelOuni/stockpilot-ai' && github.ref == 'refs/heads/dev' }}"
    assert job["steps"][0]["with"] == {"ref": "dev", "persist-credentials": "false"}
    assert job["runs-on"] == ["self-hosted", "linux", "x64", "stockpilot-evals"]
    assert job["environment"] == "stockpilot-evals"
    commands = [step.get("run", "") for step in job["steps"]]
    assert commands.index("python scripts/eval_ci.py preflight --output eval-results.json") < next(
        i for i, cmd in enumerate(commands) if "--mode local" in cmd
    )
    assert any("--compare-baseline --output eval-results.json" in command for command in commands)
    artifact = job["steps"][-1]
    assert artifact["uses"] == "actions/upload-artifact@v4"
    assert artifact["with"]["path"] == "eval-results.json"
    assert artifact["if"] == "${{ always() }}"
    assert job["steps"][-2]["if"] == "${{ always() }}"
    assert not any(step.get("continue-on-error") for step in job["steps"])
    assert not any("baseline.json" in command for command in commands)


def test_preflight_failure_is_environment_failure_not_zero_scores(monkeypatch, tmp_path):
    monkeypatch.setattr(eval_ci, "check_environment", AsyncMock(side_effect=httpx.ConnectError("offline")))
    output = tmp_path / "report.json"
    output.write_text('{"status":"complete","metrics":{"intent_accuracy":1}}')
    assert eval_ci.preflight(output) == 2
    report = json.loads(output.read_text())
    assert report["status"] == "environment_failure"
    assert report["metrics"] == {}
    assert report["quality_measurement"] is False
    assert len(report["baseline_sha256"]) == 64


def test_successful_preflight_is_not_a_quality_measurement(monkeypatch, tmp_path):
    monkeypatch.setattr(eval_ci, "check_environment", AsyncMock())
    output = tmp_path / "report.json"
    assert eval_ci.preflight(output) == 0
    report = json.loads(output.read_text())
    assert report["status"] == "incomplete"
    assert report["metrics"] == {}


@pytest.mark.parametrize("status", ["environment_failure", "configuration_failure", "incomplete"])
def test_failure_summary_does_not_call_failure_a_regression(monkeypatch, tmp_path, status):
    report = tmp_path / "report.json"
    summary = tmp_path / "summary.md"
    report.write_text(json.dumps({"status": status, "metrics": {}}))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    assert eval_ci.summary(report) == 0
    text = summary.read_text()
    assert "not measured" in text
    assert "REGRESSION" not in text
    assert "Baseline SHA256" in text
    assert "qwen2.5:7b" in text
    assert "ci_provenance" in json.loads(report.read_text())


def test_regression_summary_includes_actual_comparison(monkeypatch, tmp_path):
    report = tmp_path / "report.json"
    summary = tmp_path / "summary.md"
    report.write_text(json.dumps({
        "status": "complete", "quality_measurement": True,
        "metrics": {"intent_accuracy": 0.9},
        "baseline_comparison": {"performed": True, "tolerance": 0.03},
        "regressions": [{"metric": "intent_accuracy", "baseline": 1, "actual": 0.9}],
        "total_cases": 60, "failed_cases": 4,
    }))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    before = (eval_ci.ROOT / "evals" / "baseline.json").read_bytes()
    eval_ci.summary(report)
    assert "REGRESSION" in summary.read_text()
    assert "90.00%" in summary.read_text()
    assert (eval_ci.ROOT / "evals" / "baseline.json").read_bytes() == before


def test_preflight_requires_explicit_database(monkeypatch):
    import asyncio
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    with pytest.raises(ValueError, match="dedicated eval database"):
        asyncio.run(eval_ci.check_environment(SimpleNamespace()))


def test_preflight_rejects_missing_model_without_touching_database(monkeypatch):
    import asyncio
    monkeypatch.setenv("POSTGRES_URL", "test-only-placeholder")
    settings = SimpleNamespace(
        ollama_base_url="http://local.test", ollama_model="qwen2.5:7b",
        ollama_embedding_model="nomic-embed-text",
    )
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"models": []})),
        **kwargs,
    ))
    with pytest.raises(ValueError, match="not installed"):
        asyncio.run(eval_ci.check_environment(settings))


def test_preflight_rejects_changed_corpus_before_embedding(monkeypatch):
    import asyncio
    from unittest.mock import MagicMock
    monkeypatch.setenv("POSTGRES_URL", "test-only-placeholder")
    settings = SimpleNamespace(
        ollama_base_url="http://local.test", ollama_model="qwen2.5:7b",
        ollama_embedding_model="nomic-embed-text", postgres_url="test-only-placeholder",
    )
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={
            "models": [{"name": "qwen2.5:7b"}, {"name": "nomic-embed-text:latest"}],
        })), **kwargs,
    ))
    connection = MagicMock()
    connection.__enter__.return_value.execute.return_value.fetchall.return_value = []
    monkeypatch.setattr(eval_ci.psycopg, "connect", lambda *args, **kwargs: connection)
    with pytest.raises(ValueError, match="corpus must exactly match"):
        asyncio.run(eval_ci.check_environment(settings))


def test_runner_configuration_failure_has_json_report(tmp_path):
    from evals.runners.run import main
    output = tmp_path / "report.json"
    assert main(["--compare-baseline", "--output", str(output)]) == 2
    assert json.loads(output.read_text())["status"] == "configuration_failure"
