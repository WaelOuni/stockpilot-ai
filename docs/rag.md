# Product-document RAG

This page describes the historical global adapter. The separate internal
[B2B RAG/conversation boundary](security/tenant-rag-conversations.md) uses explicit
[PostgreSQL migrations](postgresql-migrations.md); it does not migrate this corpus.

[Back to README](../README.md) · [Database](database.md) · [AI evals](ai-evals.md)

## Corpus and ingestion

The three public demo documents in [data/products](../data/products) are
ingested by [scripts/ingest_products.py](../scripts/ingest_products.py).
Each `.txt` file becomes a knowledge row with its filename as `source`.
The PostgreSQL `knowledge_chunks` table stores content/source and
`VECTOR(768)` embeddings from Ollama `nomic-embed-text`.

After the fresh-database migrations in [getting started](getting-started.md):

```sh
python scripts/ingest_products.py
```

Ingestion upserts by source filename, preserving row IDs. Sequence values may
advance even on conflict; contiguous IDs are not promised. It does not remove
stale corpus rows. The eval CI preflight rejects extra/mismatched documents.
Migration 001 adds `UNIQUE NULLS NOT DISTINCT (source)` and is not independently
repeatable; inspect existing schemas before applying it.

Before changing embedding models:

```sh
python scripts/check_embedding_dimension.py
```

Check dimension and plan a separate migration/re-ingestion. Equal dimensions
alone do not make embeddings from different models compatible. Do not silently
mix models or alter an existing populated table.

## Retrieval and recommendation

`RecommendProductUseCase` embeds the question, retrieves the raw top three
documents by cosine distance, then keeps only distances **<= 0.45**.
No retained documents returns the existing insufficient-information answer,
without generation.

Retained chunks are separated with source names in the context. The generation
prompt asks for structured `ProductRecommendation` JSON: canonical
`product_name` (or null) and a French answer using only the context, without
inventing prices/specifications or mentioning availability.

Stock is checked separately against Mongo inventory by the advice workflow.
Document retrieval is not a stock guarantee, and prompt grounding is not a
universal factual correctness guarantee.

## Classification scope

The structured classification prompt preserves concrete product references
after plural category/quantity words and excludes generic specifications.
Application category normalization remains active.

The classifier prompt gives inventory availability/remaining-stock questions
priority over tool advice, with contrastive examples preserving product
extraction and null quantity for unknown remaining stock. Budget/usage extraction
remains independent; no deterministic intent override is added.

The small positive guard in
[supported_tooling.py](../src/stockpilot/domain/services/supported_tooling.py)
is used only when the LLM returns PRODUCT_ADVICE. It recognizes supported
categories/common French drilling, fastening, cutting and sanding forms in
the **original message**, not an LLM-generated category. Missing signal changes
that intent to UNKNOWN. ORDER and STOCK_CHECK are never filtered.
Word boundaries avoid accidental substring matches, but an unrelated request
mentioning a cutting verb can still pass: this is lexical, not semantic.
No deterministic numeric parser or product fallback was introduced.

## Quality interpretation

Retrieval evaluation scores raw top-three results before the distance filter.
Grounding evaluates only the structured product against retained context
documents' first-line names, not all generated prose.

Recall@K uses a relevant set, not alternatives: one of two relevant sources gives
Recall@1 = 0.5. Only three documents exist, so Recall@3 is a weak signal.
Small annotated denominators make percentage changes noisy.

The human-reviewed historical 60-case reference had zero failures, Recall@1
87.5%, Recall@3 100% and recommendation/grounding 100%. These are measured run
results, not guarantees. See [evals/README.md](../evals/README.md) for formulas,
denominators, provenance, older correction history and regression policy.

RAG search logs/spans capture counts/distances, not queries/chunks;
[observability](observability.md) explains privacy and metric semantics.
