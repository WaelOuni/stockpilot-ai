import argparse
import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
import psycopg

from evals.runners.run import DATASETS, ROOT, dataset_hashes
from evals.runners.scoring import load_cases
from stockpilot.infrastructure.config.settings import Settings
from stockpilot.infrastructure.embeddings.ollama_embedding_adapter import OllamaEmbeddingAdapter
from stockpilot.infrastructure.persistence.postgres.pgvector_knowledge_repository import PgVectorKnowledgeRepository


def provenance():
    baseline_path = ROOT / "evals" / "baseline.json"
    settings = Settings(otel_enabled=False)
    return {
        "baseline_sha256": hashlib.sha256(baseline_path.read_bytes()).hexdigest(),
        "dataset_hashes": dataset_hashes({
            kind: load_cases(ROOT / "evals" / "datasets" / f"{kind}_cases.json", kind)
            for kind in DATASETS
        }),
        "configuration": {
            "model": settings.ollama_model,
            "embedding_model": settings.ollama_embedding_model,
        },
    }


async def check_environment(settings):
    if not os.environ.get("POSTGRES_URL"):
        raise ValueError("POSTGRES_URL must explicitly select a dedicated eval database")
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(f"{settings.ollama_base_url}/api/tags")
        response.raise_for_status()
        models = {model["name"] for model in response.json()["models"]}
    for name in (settings.ollama_model, settings.ollama_embedding_model):
        canonical = name if ":" in name else f"{name}:latest"
        if canonical not in models:
            raise ValueError(f"Required local Ollama model is not installed: {name}")

    with psycopg.connect(settings.postgres_url, connect_timeout=5) as connection:
        rows = connection.execute("SELECT source, content FROM knowledge_chunks").fetchall()
    expected = {path.name: path.read_text(encoding="utf-8")
                for path in (ROOT / "data" / "products").glob("*.txt")}
    if len(rows) != len(expected) or dict(rows) != expected:
        raise ValueError("Eval corpus must exactly match data/products; use a dedicated pre-ingested database")
    vector = await OllamaEmbeddingAdapter(settings).embed("outil de perçage")
    await PgVectorKnowledgeRepository(settings.postgres_url).search(vector, limit=1)


def preflight(output):
    output.unlink(missing_ok=True)
    metadata = {}
    try:
        metadata = provenance()
        asyncio.run(check_environment(Settings(otel_enabled=False)))
    except (httpx.HTTPError, psycopg.Error, ValueError, OSError, KeyError, TypeError) as error:
        report = {
            **metadata, "mode": "local", "status": "environment_failure",
            "quality_measurement": False, "metrics": {}, "error_type": type(error).__name__,
        }
        output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        # Do not print database errors/URLs: they may contain credentials.
        print(f"::error::Environment failure ({type(error).__name__}): check eval DB/corpus, Ollama connectivity and installed models")
        return 2
    output.write_text(json.dumps({
        **metadata, "mode": "local", "status": "incomplete",
        "quality_measurement": False, "metrics": {},
        "note": "Preflight succeeded; real evaluation has not completed yet.",
    }, indent=2) + "\n", encoding="utf-8")
    print("Eval environment ready; no quality measurement performed by preflight")
    return 0


def summary(report_path):
    metadata = provenance()
    if report_path.exists():
        report = json.loads(report_path.read_text(encoding="utf-8"))
    else:
        report = {"status": "incomplete", "metrics": {}, "quality_measurement": False}
    status = report.get("status", "incomplete")
    measured = status == "complete" and report.get("quality_measurement") is True
    regressions = report.get("regressions", [])
    if measured:
        gate = "REGRESSION" if regressions else (
            "PASS" if report.get("baseline_comparison", {}).get("performed") else "Comparison not completed"
        )
    else:
        gate = "Environment failure" if status == "environment_failure" else "Incomplete/configuration failure"
    lines = ["## StockPilot AI Quality Gate", "", f"**Result: {gate}**", "",
             f"Baseline SHA256: `{metadata['baseline_sha256']}`", ""]
    configuration = report.get("configuration", metadata["configuration"])
    lines += [f"Model: `{configuration['model']}`",
              f"Embedding model: `{configuration['embedding_model']}`", "",
              "| Dataset | SHA256 |", "|---|---|"]
    for name, digest in report.get("dataset_hashes", metadata["dataset_hashes"]).items():
        lines.append(f"| {name} | `{digest}` |")
    lines += ["", "| Metric | Score |", "|---|---|"]
    for metric in ("intent_accuracy", "quantity_accuracy", "order_quantity_accuracy",
                   "rag_recall_at_1", "rag_recall_at_3", "recommendation_product_accuracy",
                   "grounded_recommendation_rate"):
        value = report.get("metrics", {}).get(metric) if measured else None
        lines.append(f"| {metric} | {value:.2%} |" if value is not None else f"| {metric} | not measured |")
    lines += ["", f"Baseline comparison: `{report.get('baseline_comparison', {})}`", ""]
    for regression in regressions:
        lines.append(f"- Regression `{regression['metric']}`: {regression['baseline']} -> {regression['actual']}")
    if measured:
        lines.append(f"\nFailed cases: {report['failed_cases']} / {report['total_cases']}")
    else:
        lines.append("\nNo AI quality scores are inferred from an environment/configuration failure.")
    text = "\n".join(lines) + "\n"
    destination = os.environ.get("GITHUB_STEP_SUMMARY")
    if destination:
        with Path(destination).open("a", encoding="utf-8") as stream:
            stream.write(text)
    else:
        print(text)
    report["ci_provenance"] = metadata
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    pre = sub.add_parser("preflight")
    pre.add_argument("--output", type=Path, default=Path("eval-results.json"))
    summarizer = sub.add_parser("summary")
    summarizer.add_argument("--report", type=Path, default=Path("eval-results.json"))
    args = parser.parse_args(argv)
    return preflight(args.output) if args.command == "preflight" else summary(args.report)


if __name__ == "__main__":
    raise SystemExit(main())
