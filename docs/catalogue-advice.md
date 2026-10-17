# Catalogue Advice (V13.C1)

Advice is read-only. Product facts come from the catalogue, never generated prose.

## Request path and boundaries

`POST /assistant` authenticates the customer and hashes `(customer_id, thread_id)`
into the checkpoint key. The graph runs confirmation handling, classification,
context resolution and the Advice node. Deterministic advice grammar protects
explicit advice requests from a classifier that proposes ORDER. Existing ORDER
confirmation, identity, quantity, transaction and idempotency logic is unchanged.

The Advice node passes current user text and authorized structured criteria to
`CatalogueAdviceUseCase`. It does not concatenate old and new budget sentences.
The use case selects filtered Mongo candidates or resolves a named identity,
then performs strict exact-SKU reads before constructing facts. Mongo rejects
malformed raw prices/stocks before legacy numeric conversion. A duplicate SKU,
lookup failure or unverifiable identity cannot become a recommendation.

The LLM may choose only the order of three deterministic fact sentences, once
per response. Invalid output or provider failure keeps the deterministic facts.
Mongo failure produces a controlled error with no RAG/LLM catalogue fallback.

## Behaviors

| Request | Result |
|---|---|
| `Je cherche une perceuse disponible à moins de 100 euros.` | At most 3 verified DRILL products, price `< 100`, stock `> 0`, ordered by price/SKU. The article `une` is not a count. |
| `Montre-moi un seul produit`, `Propose exactement une perceuse` | One result. Numeric counts and `deux` through `cinq` also apply, capped at 5. |
| `Conseille-moi sur la Perceuse Concurrence Synthétique V017.` | One targeted verified product, with no automatic alternatives. |
| `Conseille-moi sur Concurrence Synthétique.` | A unique bounded literal partial-name match; ambiguity asks for a full name or SKU. |
| `Compare cette perceuse avec deux autres.` | With a single authorized prior reference: reference + at most 2 alternatives, normally in its category. |
| `Compare le premier avec deux autres.` | First SKU in the immediately preceding verified list is the reference. |
| `Quelle est la moins chère entre ces trois perceuses ?` | Re-read the prior list and select the cheapest product still matching the criteria; no new candidates are added. |
| `Et si je monte le budget à 150 euros ?` | Replace the old ceiling with inclusive 150; preserve category, availability and result count. `moins de` explicitly requests an exclusive ceiling. |
| `Et si je descends à 80 € ?` | Replace the ceiling with inclusive 80. |
| `Montre-moi seulement deux produits.` | Replace presentation count while keeping relevant criteria. |
| `Et si je veux une visseuse plutôt ?` | Replace category; preserve budget and availability. |
| `Et sans plafond de budget ?`, `Et sans contrainte de disponibilité ?` | Remove the named constraint. |
| `Je cherche une scie.` | Independent search: earlier budget, availability and references do not carry over. |

A named reference uses the existing exact SKU/name resolver. Only read-only
Advice additionally permits two alphabetic name discriminants of at least three
characters, with literal token matching and at most 21 candidates. Saturation or
multiple matches asks for clarification. Single-token partial matches, edit-distance
search and unconstrained approximate search are not supported. ORDER retains its
stricter original resolver. Quote product names containing request/constraint words
and use the SKU when a phrase is ambiguous.

A comparison has at most 4 alternatives plus 1 reference. Explicit alternative
categories are supported when stated with a resolvable target. A new target
otherwise uses its own category, rather than an inherited search category. Its
reference stays visible when outside the budget/availability/category constraints,
with `matches_criteria=false` and an explicit explanation. Alternatives must satisfy
the criteria. Cards contain verified price differences, never performance claims.
An empty alternative set is stated explicitly as zero alternatives.

## Checkpoints and API compatibility

New optional state fields hold one bounded criteria dictionary and at most five
presented SKUs. A comparison/target stores its verified reference SKU. No product
facts or growing transcript are used as recommendation context. Old checkpoints
remain readable; authorized legacy advice text can reconstruct criteria, but does
not invent past selected SKUs. Missing or ambiguous references require clarification.

Advice context is scoped to its authenticated customer/thread and is retired by
unrelated intents. It never populates ORDER product/quantity selection. Advice also
retires an existing pending order through the existing confirmation mechanism.

Existing `advice_status`, `recommendations` and product fields are preserved.
Comparison cards add optional `role` (`reference`/`alternative`),
`matches_criteria` and `price_difference`. The frontend validates this metadata,
keeps the five-card bound, escapes text and encodes SKU links. It hides historical
Advice cards after a new turn while retaining the conversation text.

## Validation and limits

- Unit/API/graph tests: `tests/test_catalogue_advice*.py`.
- Real Mongo and PostgreSQL: `tests/test_catalogue_advice_mongo_integration.py`
  uses existing guarded UUID databases and temporary checkpoint schemas, with
  explicit `MONGO_TEST_URI` and `POSTGRES_TEST_URI` only.
- Deterministic enriched evals execute 16 real Advice graph steps on synthetic
  products and a hostile fake classifier; failures fail the CLI. These are not
  measurements of Ollama quality. The historical model baseline is unchanged.
- Browser: `cd frontend && npx playwright test --config playwright.advice-synthetic.config.ts`.
  This starts a fixture-only API and Vite on free local ports 8002/5174 with
  `reuseExistingServer=false`, an isolated dotenv directory and a fresh test JWT
  key. The fixture instantiates no Mongo/PostgreSQL/Ollama clients and exposes no
  business-write endpoint. Do not run the older `verify_advice_browser.py` against
  existing stores; it requires a separately verified persistent E2E catalogue.

Currency and technical specifications are absent from the product model. Advice
cannot establish technical suitability or convert currencies. Prices/stocks are
snapshots; no reservation occurs. Unsupported budget ranges/currencies and unsafe
references require clarification. Multi-product comparisons outside a prior list
require an explicit resolvable reference; broader recommendation/ranking is out of scope.
