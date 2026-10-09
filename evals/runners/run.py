import argparse
import asyncio
import json
import hashlib
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from evals.runners.scoring import accuracy, grounded, load_cases, matches, recall, reciprocal_rank, regressions


ROOT = Path(__file__).resolve().parents[2]
DATASETS = ("intent", "rag", "recommendation", "order")


def dataset_hashes(cases):
    return {
        kind: hashlib.sha256(json.dumps(values, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        for kind, values in cases.items()
    }


class CapturingKnowledgeRepository:
    def __init__(self, repository):
        self.repository = repository
        self.documents = []

    async def search(self, embedding, limit=3):
        self.documents = await self.repository.search(embedding=embedding, limit=limit)
        return self.documents


async def evaluate(mode, selected, cases, classifier=None, embedding=None, repository=None, recommender=None):
    from stockpilot.application.use_cases.recommend_product import MAX_COSINE_DISTANCE
    scores = defaultdict(list)
    results = []
    confusion = {intent: {predicted: 0 for predicted in ("PRODUCT_ADVICE", "STOCK_CHECK", "ORDER", "UNKNOWN")}
                 for intent in ("PRODUCT_ADVICE", "STOCK_CHECK", "ORDER", "UNKNOWN")}
    for kind in selected:
        for case in cases[kind]:
            failures = []
            if kind in ("intent", "order"):
                actual = (
                    dict(case["expected"]) if mode == "deterministic"
                    else (await classifier.execute(case["message"])).model_dump(mode="json")
                )
                for field, expected in case["expected"].items():
                    correct = matches(expected, actual.get(field), case.get("comparison", {}).get(field, "normalized"))
                    if field in ("intent", "product_name", "quantity", "category", "budget"):
                        scores[("order_" if kind == "order" else "") + field + "_accuracy"].append(correct)
                    if not correct:
                        failures.append(field)
                if kind == "intent":
                    confusion[case["expected"]["intent"]][actual["intent"]] += 1
            elif kind == "rag":
                if mode == "deterministic":
                    sources = list(case["expected_sources"])
                else:
                    vector = await embedding.embed(case["query"])
                    documents = await repository.search(vector, limit=3)
                    sources = [document.source for document in documents]
                actual = {"sources": sources}
                for k in (1, 3):
                    scores[f"rag_recall_at_{k}"].append(recall(sources, case["expected_sources"], k))
                scores["rag_mrr"].append(reciprocal_rank(sources, case["expected_sources"]))
                if recall(sources, case["expected_sources"], 3) < 1:
                    failures.append("retrieval")
            else:
                if mode == "deterministic":
                    product = case["expected_product"]
                    documents = [SimpleNamespace(content=product or "", distance=0)]
                else:
                    recommendation = await recommender.execute(case["message"])
                    product = recommendation.product_name
                    documents = [doc for doc in repository.documents if doc.distance <= MAX_COSINE_DISTANCE]
                correct = matches(case["expected_product"], product)
                scores["recommendation_product_accuracy"].append(correct)
                support = grounded(product, documents)
                if support is not None:
                    scores["grounded_recommendation_rate"].append(support)
                actual = {"product_name": product, "grounded": support}
                if not correct:
                    failures.append("recommendation_product")
                if support is False:
                    failures.append("grounding")
            results.append({
                "id": case["id"], "dataset": kind, "input": case.get("message", case.get("query")),
                "expected": case.get("expected", case.get("expected_sources", case.get("expected_product"))),
                "actual": actual, "failed_checks": failures,
            })
    return {
        "mode": mode, "quality_measurement": mode == "local", "status": "complete",
        "dataset_hashes": dataset_hashes({kind: cases[kind] for kind in selected}),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "metrics": {key: accuracy(values) for key, values in scores.items()},
        "metric_case_counts": {key: len(values) for key, values in scores.items()},
        "confusion_matrix": confusion, "total_cases": len(results),
        "failed_cases": sum(bool(result["failed_checks"]) for result in results),
        "cases": results,
    }


async def local_evaluate(selected, cases):
    from stockpilot.infrastructure.config.settings import Settings
    from stockpilot.infrastructure.llm.ollama_adapter import OllamaAdapter
    from stockpilot.infrastructure.embeddings.ollama_embedding_adapter import OllamaEmbeddingAdapter
    from stockpilot.infrastructure.persistence.postgres.pgvector_knowledge_repository import PgVectorKnowledgeRepository
    from stockpilot.application.use_cases.classify_intent import ClassifyIntentUseCase
    from stockpilot.application.use_cases.recommend_product import RecommendProductUseCase
    settings = Settings(otel_enabled=False)
    llm = OllamaAdapter(settings)
    embedding = OllamaEmbeddingAdapter(settings)
    repository = CapturingKnowledgeRepository(PgVectorKnowledgeRepository(settings.postgres_url))
    report = await evaluate(
        "local", selected, cases, ClassifyIntentUseCase(llm), embedding, repository,
        RecommendProductUseCase(embedding, repository, llm),
    )
    report["configuration"] = {
        "model": settings.ollama_model, "embedding_model": settings.ollama_embedding_model,
        "classification_temperature": 0, "recommendation_temperature": "production default",
    }
    return report


def main(argv=None):
    import httpx
    import psycopg
    from pydantic import ValidationError
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("deterministic", "local"), default="deterministic")
    parser.add_argument("--dataset", choices=DATASETS + ("all",), default="all")
    parser.add_argument("--suite", choices=("historical", "enriched"), default="historical")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--compare-baseline", action="store_true")
    parser.add_argument("--baseline", type=Path, default=ROOT / "evals" / "baseline.json")
    parser.add_argument("--tolerance", type=float)
    args = parser.parse_args(argv)
    selected = DATASETS if args.dataset == "all" else (args.dataset,)
    try:
        baseline = None
        if args.compare_baseline:
            if args.mode != "local":
                raise ValueError("Baseline comparison requires a real local quality measurement")
            baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
            if not isinstance(baseline, dict) or baseline.get("mode") != "local":
                raise ValueError("Baseline must be a local quality measurement")
            tolerance = args.tolerance if args.tolerance is not None else baseline.get("tolerance", 0.03)
            regressions({}, baseline, tolerance)
        cases = {kind: load_cases(ROOT / "evals" / "datasets" / f"{kind}_cases.json", kind) for kind in selected}
        if args.suite == "enriched":
            for kind in selected:
                extra = ROOT / "evals" / "datasets" / f"{kind}_additional_cases.json"
                if extra.exists():
                    additions = load_cases(extra, kind)
                    if {case["id"] for case in cases[kind]} & {case["id"] for case in additions}:
                        raise ValueError(f"Duplicate case id across {kind} datasets")
                    cases[kind] += additions
        if baseline is not None and baseline.get("dataset_hashes") and baseline["dataset_hashes"] != dataset_hashes(cases):
            raise ValueError("Dataset selection/content differs from baseline; establish a new reference")
        report = asyncio.run(local_evaluate(selected, cases) if args.mode == "local"
                             else evaluate("deterministic", selected, cases))
        report["regressions"] = []
        report["baseline_comparison"] = {"performed": False}
        if baseline is not None:
            if baseline.get("configuration") and baseline["configuration"] != report.get("configuration"):
                raise ValueError("Model configuration differs from baseline; establish a new reference")
            report["regressions"] = regressions(report["metrics"], baseline, tolerance)
            report["baseline_comparison"] = {"performed": True, "tolerance": tolerance}
    except (httpx.HTTPError, psycopg.Error, ValidationError) as error:
        report = {"mode": args.mode, "status": "incomplete", "quality_measurement": False,
                  "error_type": type(error).__name__, "metrics": {}}
        if args.output:
            args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"Evaluation incomplete: {type(error).__name__}; check local services or model output")
        return 2
    except (ValueError, OSError) as error:
        if args.output:
            args.output.write_text(json.dumps({
                "mode": args.mode, "status": "configuration_failure",
                "quality_measurement": False, "metrics": {}, "error_type": type(error).__name__,
            }, indent=2) + "\n", encoding="utf-8")
        print(f"Evaluation configuration failed: {error}")
        return 2
    print(f"StockPilot AI Evaluation ({args.mode})")
    if not report["quality_measurement"]:
        print("Fixture smoke run only: NOT an AI quality score.")
    for name, value in report["metrics"].items():
        print(f"{name:40} {value:.1%}" if value is not None else f"{name}: n/a")
    print(f"Total cases: {report['total_cases']}; failed: {report['failed_cases']}")
    print("Confusion matrix (expected rows, predicted columns):", json.dumps(report["confusion_matrix"]))
    for case in report["cases"]:
        if case["failed_checks"]:
            print(json.dumps(case, ensure_ascii=False))
    if args.output:
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for regression in report["regressions"]:
        print("REGRESSION:", regression)
    return 1 if report["regressions"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
