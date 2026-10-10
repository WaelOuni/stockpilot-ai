# Offline AI / RAG evaluations

Unit tests check deterministic code behavior with mocks. These evaluations
measure the current model and retrieval on small French golden datasets.
They are not an LLM-as-judge benchmark and use no cloud service.

## Layout and golden data

`datasets/intent_cases.json`: 40 historical messages, ten per original intent.
`datasets/rag_cases.json`: eight queries with relevant source filenames.
`datasets/recommendation_cases.json`: six expected products, including an
unsupported task expecting no recommendation.
`datasets/order_cases.json`: six extraction cases (default quantity, digits,
French number words, large quantity, zero and negative quantity).

`runners/scoring.py` contains objective scoring and validation;
`runners/run.py` executes the existing production use cases/adapters.
No API startup, Mongo connection, order finalization, stock mutation, checkpoint
or database ingestion is performed. The repository wrapper only searches and
captures the documents actually provided to recommendation.

## Commands (from project root, WSL/Linux)

Use the existing installed development environment:

```sh
source .venv/bin/activate
python scripts/run_evals.py --mode deterministic
python scripts/run_evals.py --mode deterministic --output eval-results.json
```

**Deterministic mode is only a fixture smoke run, NOT an AI quality measurement.**
It explicitly substitutes golden expectations as synthetic predictions to
exercise dataset loading, aggregation and reporting. Its displayed scores must
never become a model baseline; baseline comparison is prohibited in this mode.
The pytest framework tests independently exercise wrong predictions, partial
retrieval, ungrounded products and regressions.

Real quality measurement requires local Ollama (Qwen and nomic-embed-text)
and the existing PostgreSQL/pgvector database with ingested product documents:

```sh
docker compose up -d postgres
# WSL: only if Ollama runs on the Windows host:
source scripts/dev-env.sh
python scripts/run_evals.py --mode local --output eval-results.json
```

Override `OLLAMA_BASE_URL`, `OLLAMA_MODEL`, `OLLAMA_EMBEDDING_MODEL` and
`POSTGRES_URL` as usual. No ingestion is run automatically; see the existing
`scripts/ingest_products.py` for initial corpus setup. Use an eval database if
the live catalogue differs from the three golden documents.

Run a subset with `--dataset intent`, `rag`, `recommendation` or `order`.
The default `--suite historical` preserves the original 60 cases and their
baseline. `--suite enriched` appends fifteen intent and eight ORDER cases from
`intent_additional_cases.json` and `order_additional_cases.json`, plus five
ORDER_STATUS cases from `intent_cases_enriched.json` (88 total).
These cover varied out-of-domain advice, implicit tool needs, unknown
references, signed/zero quantities, absent quantities and price/voltage/dimension
traps, plus generic tools with power/voltage/diameter/material descriptions
that must not become named product references. Existing expectations and the
measured baseline are not replaced.

ORDER_STATUS fixtures distinguish querying an existing order from creating an
ORDER or checking STOCK_CHECK availability. They cover current/last-order
questions, an explicit UUID, a product model that must not become an order ID,
and an operation identifier that must not become an order ID. `order_id` is
annotated as a string or explicit `null`; only explicit order identifiers are
extracted. Conversation fallback and read-only database behavior are covered
by deterministic unit/integration tests, not these single-turn classifier
evaluations. No new local model quality measurement is claimed for these cases.

```sh
python scripts/run_evals.py --mode local --suite historical \
  --compare-baseline --tolerance 0 --output historical-results.json
python scripts/run_evals.py --mode local --suite enriched \
  --output enriched-results.json
```

The first command demands no decrease against any historical metric.
The enriched suite has different hashes/denominators and is deliberately
reported separately, not compared against the historical baseline.
The earlier prompt-only correction measured intent accuracy 100% and ORDER quantity
accuracy 100% on the historical suite, but product extraction fell to 96.7%
(the named saw in a plural stock question was omitted).
On the enriched suite intent accuracy was 98%, product extraction 96.9%,
and ORDER quantity accuracy 100%. The phone advice case remained incorrectly
PRODUCT_ADVICE. Consequently the strict historical comparison failed; the
baseline is intentionally unchanged. These results do not establish a hard
domain guarantee from the prompt alone. No deterministic domain filter or
numeric quantity parser was introduced: all new numeric/price/voltage/dimension
cases passed.

The subsequent correction adds a small positive domain guard in
`domain/services/supported_tooling.py`, used only when the LLM returns
PRODUCT_ADVICE. It recognizes supported tool categories and common French
drilling/fastening/cutting/sanding forms in the original message, not an
LLM-generated category. No signal changes that intent to UNKNOWN. ORDER and
STOCK_CHECK and ORDER_STATUS are never filtered. Word boundaries avoid accidental substring
matches; this is a lexical scope check, not a semantic guarantee (for example,
an unrelated request mentioning a cutting verb can still pass).

The product-name prompt contract now explicitly preserves concrete references
after plural category/quantity words and excludes generic specifications.
No deterministic product fallback was needed in the final measured run:
the 60 historical and 78 enriched cases each had zero failures. Intent,
product/category/quantity/budget extraction, recommendation and grounding
were 100%; Recall@1 was 87.5%, Recall@3 100%, MRR 1.0.
The historical comparison with tolerance zero passed against the unchanged
baseline. This is one measured model run, not a guarantee of future outputs;
no golden expectations or baseline scores were updated.
Classification and ORDER extraction run `ClassifyIntentUseCase` (including
application category normalization). Recommendation runs
`RecommendProductUseCase`, including its existing cosine-distance cutoff 0.45.
Production classification temperature remains 0. Production recommendation
does not explicitly set temperature: the evaluation preserves that behavior.
Models, hardware and Ollama versions can still change outputs.

## Metrics and interpretation

| Metric | Definition |
|---|---|
| `intent_accuracy` | Correct intent / annotated intent cases |
| `order_id_accuracy` | Correct extracted order identifier / annotated order-ID cases, including explicit `null` |
| `product_name_accuracy` | Correct canonical name / annotated product cases |
| `quantity_accuracy` | Correct quantity / annotated quantity cases |
| `category_accuracy` | Correct normalized category / annotated category cases |
| `budget_accuracy` | Correct budget / annotated budget cases |
| `order_intent_accuracy`, `order_product_name_accuracy`, `order_quantity_accuracy` | Same checks on extraction-only ORDER cases |
| `rag_recall_at_1`, `rag_recall_at_3` | Mean fraction of relevant sources retrieved in top K |
| `rag_mrr` | Mean reciprocal rank of the first relevant source; zero if absent |
| `recommendation_product_accuracy` | Expected product equals structured recommended product |
| `grounded_recommendation_rate` | Recommended canonical product is a retained context document's first-line name |

Missing annotations do not enter a metric's denominator; explicit `null` does.
Name matching uses Unicode NFKC, case folding and collapsed whitespace. Accents,
punctuation and model numbers are preserved. Numeric budget comparison has
an absolute tolerance of 0.000001. Set a case's
`"comparison": {"product_name": "exact"}` when strict equality is required.
Explicit order UUID fixtures use `"comparison": {"order_id": "exact"}` so the
identifier must be preserved exactly. Reports include the extracted `order_id`
and identify incorrect extraction as an `order_id` failed check.
Do not compare free-form answer or usage prose.

`expected_sources` is a **relevant set**, not a list of alternatives:
finding one of two relevant documents gives Recall@1 = 0.5, not 1.
Retrieval scores use the raw top three documents before the recommendation
distance filter. Grounding uses only the documents retained by that filter.
No recommendation means no grounding observation; check `metric_case_counts`
for coverage rather than interpreting missing recommendations as grounded.
This minimal grounding check evaluates only the structured product, not every
product or factual claim in the generated answer.

Only three source documents exist: Recall@3 is therefore a weak signal until
the corpus grows. Tiny datasets make scores noisy (one failure among six cases
changes a rate by 16.7 percentage points).

## Reports, baseline and regression policy

Human output includes metric values, total/failed cases, a confusion matrix
(expected rows, predicted columns) and failed input/expected/actual checks,
without prompts. `--output` writes JSON:

```json
{
  "mode": "local",
  "quality_measurement": true,
  "status": "complete",
  "metrics": {"intent_accuracy": 0.95},
  "metric_case_counts": {"intent_accuracy": 40},
  "total_cases": 60,
  "failed_cases": 4,
  "dataset_hashes": {},
  "configuration": {"model": "qwen2.5:7b"},
  "confusion_matrix": {},
  "cases": [],
  "regressions": []
}
```

Numbers above are illustrative, not measured. The complete report also
contains timestamps, exact dataset content hashes and model configuration.
Reports can contain user-style text: store them locally, not in business logs.

`baseline.json` holds a reviewed local reference; if `metrics` is empty,
there is no measured baseline yet. To establish/update it, run the full local
evaluation, inspect failures, then copy the report's `metrics`, `dataset_hashes`
and `configuration` into the baseline with `"mode": "local"` and
`"tolerance": 0.03`. Do not replace it with arbitrary target scores.

The included reference is the human-approved local Qwen/pgvector measurement
of the **60 historical cases**, timestamp `2026-10-09T22:40:43.230440+00:00`:
zero failures, Recall@1 87.5%, MRR 1.0 and all other implemented rates 100%
on their small annotated denominators (grounding: five actual recommendations;
budget: only three cases). Dataset hashes, model configuration and tolerance
remain unchanged. The additional datasets stay separate; the earlier 78-case
enriched measured run remains complementary validation, not a measurement of
the current 88-case enriched suite.

Promoting the enriched suite requires an explicit reference-suite change,
a newly measured and human-reviewed baseline, and updating the CI suite in
the same change. CI never promotes a suite or updates the baseline itself.

```sh
python scripts/run_evals.py --mode local --compare-baseline \
  --output eval-results.json
# Optional: allow five percentage points instead of three.
python scripts/run_evals.py --mode local --compare-baseline --tolerance 0.05
```

Regression means a metric dropped **more than** three absolute percentage
points (default); an absent required metric is also a regression. Baselines
with stored hashes/configuration must match the dataset selection/content and
model configuration. A subset run cannot be compared to a full-run baseline.
Exit 0 = completed run (individual misses remain visible); exit 1 = regression;
exit 2 = invalid configuration, unavailable service or invalid model schema.
Service/schema failures produce an incomplete JSON report with no quality
scores, not an artificial zero or a fabricated success.

The script does not import `stockpilot.main` or initialize telemetry/log-file
exporters. OpenTelemetry remains uninitialized/no-op even when application
environment settings enable it; runs do not export to business dashboards.
Run this CLI in a separate process rather than inside a running API.

## Adding cases and running framework tests

Add a unique `id`, French `message` (or RAG `query`) and only useful objective
expectations:

```json
{
  "id": "order-three-bosch",
  "message": "Commande-moi 3 Bosch X100",
  "expected": {"intent": "ORDER", "product_name": "Bosch X100", "quantity": 3}
}
```

Use real `data/products` filenames for RAG sources and actual canonical names
for recommendation. Review source relevance manually. Do not change goldens
simply to make an incorrect model answer pass. Zero/negative quantities check
faithful extraction, not acceptance of an invalid order.

```sh
python -m pytest -q tests/test_evals.py
python -m pytest -q
```

No local model/database calls are part of standard pytest. Framework tests
cover normalization, numeric/exact comparison, annotated denominators,
Recall@K, MRR, grounding, validation, reporting and regression exit codes.

Future work may evaluate writing quality, global relevance and advanced
faithfulness with an optional LLM judge. It is not a primary metric here.
Multi-turn ORDER/ORDER_STATUS state transitions, read-only status lookup,
transaction/idempotency correctness and availability remain the responsibility
of deterministic tests.
