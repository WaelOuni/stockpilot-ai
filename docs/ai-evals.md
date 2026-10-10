# AI evaluations

[Back to README](../README.md) · [Scoring reference](../evals/README.md) · [GitHub Actions](github-actions.md)

## What is measured

The offline eval framework uses small French golden datasets and objective
intent/extraction, retrieval and structured recommendation/grounding checks.
It is not an LLM-as-judge benchmark. Real mode uses local Ollama and pgvector;
no SaaS LLM is used. It does not start the API, connect Mongo, create orders,
change stock, write checkpoints, ingest documents or export business telemetry.
Run the CLI in a separate process, not inside the API.

[evals/README.md](../evals/README.md) is the single source for dataset layout,
normalization, annotated denominators, exact numeric/name comparisons,
Recall@K/MRR formulas, grounding coverage, JSON format, exit codes, adding cases
and correction history. [Database](database.md#dedicated-eval-database) owns corpus
setup; [RAG](rag.md) describes production retrieval/filter behavior.

## Commands

All commands run from the project root in the installed WSL/Linux environment:

```sh
source .venv/bin/activate
python -m pytest -q
python scripts/run_evals.py --mode deterministic --suite enriched
```

**Deterministic mode substitutes golden expectations as synthetic predictions.**
It exercises loading/aggregation/reporting, not AI quality; it cannot establish
or compare a model baseline. Mocked pytest independently checks wrong predictions,
partial retrieval, ungrounded recommendations and regressions.

Real measurements require pre-ingested PostgreSQL documents and downloaded
Qwen/nomic models; the runner never ingests automatically:

```sh
docker compose up -d postgres
# Only if Ollama runs on the Windows host:
source scripts/dev-env.sh
python scripts/run_evals.py --mode local
python scripts/run_evals.py --mode local --suite historical \
  --compare-baseline --tolerance 0 --output historical-results.json
python scripts/run_evals.py --mode local --suite enriched --output enriched-results.json
```

Override `POSTGRES_URL` to the dedicated eval DB and configure `OLLAMA_BASE_URL`,
`OLLAMA_MODEL`, `OLLAMA_EMBEDDING_MODEL` as needed. Restore the application
database environment after eval work. Keep real credentials out of shell
history/logs and reports outside Git.

Historical is the default **60-case** suite/reference. The enriched suite now
contains **88 cases**, including five ORDER_STATUS cases. Enriched data adds
intent/ORDER extraction cases and ORDER_STATUS classification/explicit-ID
regressions, remaining separate complementary validation. Earlier measured
enriched runs had 78 or 83 cases, before ORDER_STATUS additions.
Do not compare its different
hashes/denominators to the historical baseline.

## Measured reference, not a guarantee

The human-reviewed historical reference timestamp is
`2026-10-09T22:40:43.230440+00:00`, using local Qwen/pgvector:

| Historical measurement | Result |
|---|---|
| Cases / failed cases | 60 / 0 |
| Intent accuracy | 100% |
| Product-name and ORDER quantity extraction | 100% |
| Recall@1 / Recall@3 | 87.5% / 100% |
| Recommendation product accuracy / grounding | 100% / 100% |
| MRR | 1.0 |

Other implemented extraction rates were 100% on their annotated denominators.
Grounding covered five actual recommendations, budget only three cases;
only three source documents make Recall@3 weak. Grounding checks the structured
product against retained document names, not every factual claim in prose.
One measured run with tiny datasets does not guarantee future model behavior.
Classification temperature is 0; recommendation does not explicitly set it.
Model, hardware and Ollama changes can change outputs.

Earlier prompt-only runs showed a product-extraction regression and an
out-of-domain advice miss. The subsequent domain-guard/product-prompt correction
had zero failures in the historical and then-78-case enriched runs.
That regression is **historical run history**, not a verified current gate
failure. The baseline was not weakened or overwritten.

## Baseline policy and CI

The reviewed [evals/baseline.json](../evals/baseline.json) is never automatically
updated. Regression is a decrease **more than three absolute percentage points**
by default; tolerance zero is the stricter local comparison above. Missing
required metrics also regress. Stored dataset hashes/model configuration must
match; subsets cannot compare to the full-run reference.

Additional cases are validation, never automatic reference promotion. Promoting
a suite requires explicit reference-suite review, newly measured human-reviewed
scores/provenance/failures and matching CI changes. Never replace measured
scores with arbitrary targets or change goldens to hide incorrect answers.

PR/push CI checks mocked tests and deterministic framework behavior.
Real evals run only via the manual, trusted-dev self-hosted gate:
[GitHub Actions](github-actions.md), [runner setup](self-hosted-runner.md).
Service/schema/preflight failure yields no fabricated quality scores; individual
misses without regression do not by themselves fail a completed gate.

Reports contain model configuration, dataset hashes, timestamps and user-style
text. Treat them as private artifacts; do not feed them into business logs.
Multi-turn state transitions, transaction/idempotency and availability remain
the responsibility of deterministic tests, not extraction-only eval cases.
