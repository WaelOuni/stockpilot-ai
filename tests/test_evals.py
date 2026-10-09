import asyncio
import json
from types import SimpleNamespace

import pytest

from evals.runners.scoring import accuracy, normalize, matches, recall, reciprocal_rank, grounded, regressions, load_cases
from evals.runners import run


def test_accuracy_normalization_and_numeric_comparison():
    assert accuracy([True, False, True]) == pytest.approx(2/3)
    assert accuracy([]) is None
    assert normalize("  Bosch   X100 ") == "bosch x100"
    assert matches("Bosch X100", "bosch x100")
    assert not matches("Bosch X100", "bosch x100", "exact")
    assert matches(150, 150.0)
    assert not matches(1, True)


def test_retrieval_scores_multiple_relevant_sources():
    assert recall(["a", "c", "b"], ["a", "b"], 1) == 0.5
    assert recall(["a", "c", "b"], ["a", "b"], 3) == 1
    assert reciprocal_rank(["c", "b", "a"], ["a", "b"]) == 0.5
    assert reciprocal_rank(["c"], ["a"]) == 0
    with pytest.raises(ValueError):
        recall(["a"], [], 3)


def test_grounding_excludes_product_from_nonretained_documents():
    assert grounded("bosch x100", [SimpleNamespace(content="Bosch X100\nFacts")])
    assert not grounded("Bosch S500", [SimpleNamespace(content="Bosch X100\nFacts")])
    assert grounded(None, []) is None


def test_regression_tolerance_and_missing_metrics():
    assert not regressions({"a": 0.92}, {"metrics": {"a": 0.95}}, 0.03)
    assert regressions({"a": 0.91}, {"metrics": {"a": 0.95}}, 0.03)
    assert regressions({}, {"metrics": {"a": 1.0}})
    with pytest.raises(ValueError):
        regressions({}, {"metrics": {}})
    with pytest.raises(ValueError):
        regressions({}, {"metrics": {"a": 1}}, float("nan"))


def test_invalid_datasets_rejected(tmp_path):
    file = tmp_path / "cases.json"
    for content in ([], [{"id": "a", "message": "hello", "expected": {}}],
                    [{"id": "a", "message": "hello", "expected": {"intent": "WRONG"}}]):
        file.write_text(json.dumps(content))
        with pytest.raises(ValueError):
            load_cases(file, "intent")


def test_golden_datasets_parse_and_sources_exist():
    for kind in run.DATASETS:
        cases = load_cases(run.ROOT / "evals" / "datasets" / f"{kind}_cases.json", kind)
        assert cases
        if kind == "intent":
            assert 30 <= len(cases) <= 50
        if kind == "rag":
            for case in cases:
                for source in case["expected_sources"]:
                    assert (run.ROOT / "data" / "products" / source).exists()


def test_deterministic_smoke_report_is_not_quality(tmp_path):
    output = tmp_path / "report.json"
    assert run.main(["--mode", "deterministic", "--output", str(output)]) == 0
    data = json.loads(output.read_text())
    assert data["quality_measurement"] is False
    assert data["failed_cases"] == 0
    assert data["total_cases"] >= 30


def test_regression_exit_code(monkeypatch, tmp_path):
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"mode": "local", "metrics": {"intent_accuracy": 1.0}}))

    async def local(selected, cases):
        return {"quality_measurement": True, "metrics": {"intent_accuracy": 0.9},
                "total_cases": 1, "failed_cases": 1, "confusion_matrix": {}, "cases": []}

    monkeypatch.setattr(run, "local_evaluate", local)
    assert run.main(["--mode", "local", "--compare-baseline", "--baseline", str(baseline)]) == 1
    assert run.main(["--mode", "deterministic", "--compare-baseline"]) == 2
    baseline.write_text(json.dumps({"mode": "local", "metrics": {"intent_accuracy": 0.9}}))
    assert run.main(["--mode", "local", "--compare-baseline", "--baseline", str(baseline)]) == 0


def test_fake_local_classification_reports_failed_fields():
    from stockpilot.application.dto.intent_result import IntentResult
    from unittest.mock import AsyncMock
    cases = {"intent": [{"id": "one", "message": "Commande-moi 3 Bosch X100",
                         "expected": {"intent": "ORDER", "quantity": 3}}]}
    classifier = SimpleNamespace(execute=AsyncMock(return_value=IntentResult(
        intent="STOCK_CHECK", quantity=None,
    )))
    report = asyncio.run(run.evaluate("local", ("intent",), cases, classifier=classifier))
    assert report["metrics"]["intent_accuracy"] == 0
    assert report["cases"][0]["failed_checks"] == ["intent", "quantity"]
    assert report["confusion_matrix"]["ORDER"]["STOCK_CHECK"] == 1


def test_annotated_denominators_and_missing_grounding_coverage():
    cases = {"intent": [
        {"id": "one", "message": "Bonjour", "expected": {"intent": "UNKNOWN"}},
        {"id": "two", "message": "Une perceuse", "expected": {
            "intent": "PRODUCT_ADVICE", "product_name": None, "budget": 150,
        }},
    ]}
    report = asyncio.run(run.evaluate("deterministic", ("intent",), cases))
    assert report["metric_case_counts"] == {
        "intent_accuracy": 2, "product_name_accuracy": 1, "budget_accuracy": 1,
    }
    assert "quantity_accuracy" not in report["metrics"]
    cases = {"recommendation": [{"id": "none", "message": "Unsupported",
                                 "expected_product": None}]}
    report = asyncio.run(run.evaluate("deterministic", ("recommendation",), cases))
    assert "grounded_recommendation_rate" not in report["metrics"]


def test_local_service_error_produces_incomplete_report(monkeypatch, tmp_path):
    import httpx
    async def unavailable(selected, cases):
        raise httpx.ConnectError("not connected")
    monkeypatch.setattr(run, "local_evaluate", unavailable)
    file = tmp_path / "report.json"
    assert run.main(["--mode", "local", "--output", str(file)]) == 2
    report = json.loads(file.read_text())
    assert report["status"] == "incomplete"
    assert report["metrics"] == {}


def test_invalid_quantity_and_comparison_rejected(tmp_path):
    file = tmp_path / "bad.json"
    for updates in ({"expected": {"intent": "ORDER", "quantity": True}},
                    {"comparison": ["exact"]},
                    {"expected": {"intent": "ORDER", "category": "INVENTED"}}):
        file.write_text(json.dumps([{
            "id": "a", "message": "Commande", "expected": {"intent": "ORDER"}, **updates,
        }]))
        with pytest.raises(ValueError):
            load_cases(file, "order")


def test_local_recommendation_checks_only_retained_context():
    from unittest.mock import AsyncMock
    repository = SimpleNamespace(documents=[
        SimpleNamespace(content="Bosch X100\nDetails", distance=0.8),
        SimpleNamespace(content="Makita D200\nDetails", distance=0.3),
    ])
    recommender = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(
        product_name="Bosch X100",
    )))
    cases = {"recommendation": [{"id": "one", "message": "Perceuse",
                                 "expected_product": "Bosch X100"}]}
    report = asyncio.run(run.evaluate(
        "local", ("recommendation",), cases, repository=repository, recommender=recommender,
    ))
    assert report["metrics"]["recommendation_product_accuracy"] == 1
    assert report["metrics"]["grounded_recommendation_rate"] == 0
    assert report["cases"][0]["failed_checks"] == ["grounding"]


def test_repository_capture_delegates_read_only_search():
    from unittest.mock import AsyncMock
    repository = SimpleNamespace(search=AsyncMock(return_value=["document"]))
    capture = run.CapturingKnowledgeRepository(repository)
    assert asyncio.run(capture.search([0.1, 0.2], limit=1)) == ["document"]
    assert capture.documents == ["document"]
    repository.search.assert_awaited_once_with(embedding=[0.1, 0.2], limit=1)


def test_changed_datasets_rejected_before_local_calls(monkeypatch, tmp_path):
    from unittest.mock import AsyncMock
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({
        "mode": "local", "metrics": {"intent_accuracy": 1},
        "dataset_hashes": {"intent": "not-the-current-hash"},
    }))
    local = AsyncMock()
    monkeypatch.setattr(run, "local_evaluate", local)
    assert run.main(["--mode", "local", "--compare-baseline", "--baseline", str(baseline)]) == 2
    local.assert_not_called()


def test_measured_baseline_matches_current_goldens():
    baseline = json.loads((run.ROOT / "evals" / "baseline.json").read_text())
    cases = {kind: load_cases(run.ROOT / "evals" / "datasets" / f"{kind}_cases.json", kind)
             for kind in run.DATASETS}
    assert baseline["dataset_hashes"] == run.dataset_hashes(cases)
    assert regressions(baseline["metrics"], baseline) == []


def test_enriched_suite_preserves_historical_cases(tmp_path):
    output = tmp_path / "enriched.json"
    assert run.main(["--suite", "enriched", "--output", str(output)]) == 0
    report = json.loads(output.read_text())
    assert report["total_cases"] == 83
    assert len({(case["dataset"], case["id"]) for case in report["cases"]}) == 83
    for kind in ("intent", "order"):
        additions = load_cases(run.ROOT / "evals" / "datasets" / f"{kind}_additional_cases.json", kind)
        historical = load_cases(run.ROOT / "evals" / "datasets" / f"{kind}_cases.json", kind)
        assert len([case for case in report["cases"] if case["dataset"] == kind]) == len(additions) + len(historical)
    assert run.main(["--mode", "local", "--suite", "enriched", "--compare-baseline"]) == 2
