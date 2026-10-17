# V13.C1 Advice review

Baseline: `05f0d7852d1fa2de169024dcbc3fe281c6e80c7b` (`origin/main`).
Scope: changed Advice parsing/resolution, graph context, Mongo reads, API cards,
Vue rendering and the synthetic browser/evaluation fixtures. Review performed by
independent `review_advice` / `review_advice_final` agents with read-only synthetic
reproductions; FastAPI, Vue and general frontend security guidance was applied.

## Result

No unresolved blocking code/security finding in the final reviewed implementation.
The final independent targeted run passed 51 tests (V13.C1 + API), including the
last two-token name-resolution delta. This is a scoped engineering review, not a
claim of production Ollama quality or an external penetration test.

## Findings corrected with regression tests

| ID | Finding | Correction |
|---|---|---|
| R1 | `en stock` prevented Advice context inheritance | Recognize Advice availability constraints separately from explicit STOCK_CHECK. |
| R2 | `Max` and following name discriminants were stripped as an incomplete budget | Remove only complete budget suffixes; retain unknown product discriminants. |
| R3 | A partial name passed the use case but not deterministic classification | Recognize explicit named consultation and test the complete graph path. |
| R4 | `seulement un produit` still returned the default count | Respect explicit singleton grammar and count-only continuations. |
| R5 | A new reference SKU inherited an unrelated category | Prefer the new reference's category unless the user explicitly requests another category; retain an existing explicit comparison on budget changes. |
| R6 | Commas, `et` and `non disponible` became false name fragments | Strip only recognized complete trailing constraints/connectors. |
| R7 | Two discriminating words could not resolve a unique partial name | Add an Advice-only literal, bounded, unique-match extension; retain ORDER policy unchanged. |

## Security evidence

- `src/stockpilot/presentation/api/routes/assistant.py:22`: checkpoint key is the
  hash of the authenticated customer/thread pair. Existing JWT/HTTP protections
  were not modified.
- `src/stockpilot/application/workflows/nodes.py:125`: owner validation precedes
  context inheritance. New Advice state is bounded and cleared on other intents;
  Advice never supplies ORDER identities or quantities.
- `src/stockpilot/application/use_cases/catalogue_advice.py:190`: ambiguous
  contextual references require clarification. Exact current reads precede cards;
  unresolved identities are never replaced by generic alternatives.
- `src/stockpilot/application/use_cases/catalogue_advice.py:322`: the partial-name
  extension runs only after NOT_FOUND, protects explicit SKUs, limits candidates,
  escapes/verifies literal tokens and requires uniqueness.
- `src/stockpilot/infrastructure/persistence/mongodb/product_repository.py:219`:
  validate raw price/stock types before coercion, reject nonfinite/negative facts
  and duplicate exact SKUs. Advice performs no business writes.
- `frontend/src/api/client.ts:21`: additive comparison metadata is validated
  before rendering; five-card and duplicate-SKU protections remain.
- `frontend/src/components/ProductRecommendationCard.vue:19`: Vue text
  interpolation escapes catalogue/explanation strings; SKU links are encoded.
- `scripts/serve_advice_fixture.py:29`: fixture-only API creates no database or
  model-provider clients. The browser config refuses existing local servers,
  isolates dotenv loading and generates its own JWT key. No DEMO address is used.

Real-store tests use the existing dedicated-service identity guards, UUID Mongo
fixture databases and temporary PostgreSQL checkpoint schemas. Browser and eval
fixtures use only synthetic in-memory data. No DEMO files, stores, secrets,
volumes or services were accessed or modified by this work.

## Remaining limits

Availability is a read-only snapshot; prices have no stored currency and the model
contains no technical specifications. Only supported deterministic query grammar
and bounded literal references are accepted. Single-word partial names and broad
approximate search are deliberately unsupported. See [the functional contract](../catalogue-advice.md).
