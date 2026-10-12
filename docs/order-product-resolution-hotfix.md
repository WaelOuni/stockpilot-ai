# ORDER product resolution hotfix — review report

## Git safety — recovery audit 2026-10-12

Dedicated branch: `fix/order-product-resolution-recovery`, in
`/tmp/stockpilot-order-product-resolution-recovery`.
Base: latest fetched `origin/dev`, `1ec0a5bf8be8b1b2afc65421acc12d6c96a4db63`.
Original tested hotfix remains uncommitted and intact on `fix/order-product-resolution`
at `1689b0cc53cc2ccddec0dafdb1bc4c4693bbdb68` in its original worktree.

PR #15 is already MERGED, from `fix/hotfix-order` into `dev`. Its only commit
`57634574e67d5e0b06ca20364c41b2afb3bd10a5` contains 13 V11 network/backup files,
not ORDER, despite its title. This proves a commit from the wrong checkout/content;
the specific staging commands used cannot be inferred from Git history.
The original primary checkout is clean on `fix/hotfix-order`. V11 is preserved in
that commit, its merge on dev and the unchanged existing worktrees. There were two
worktrees before recovery and three afterwards; no duplicate branch exists.
`origin/main` remains `8576f3a0d2242fa2120f1f66b6afddc8f03e22af` (v0.1.1).

A private mode-0700 backup at `/tmp/stockpilot-order-recovery-private-v4yb_cpn`
contains the binary tracked patch, complete copies of all 39 changed/new files,
SHA-256 manifest and original status. Recovery checked patch applicability, applied
it to the new dev worktree, copied the six untracked files and verified all hashes.
No ORDER file overlaps the 13 intervening V11 files. All recovered source and test
files are byte-identical to the validated originals; this report alone was updated
for recovery. The source worktree status and file hashes remain unchanged.

No commit, staging, push, PR mutation, merge, tag or release publication occurred.
No V11 file is in the proposed diff against dev; V11 files already in dev are
inherited unchanged. No reset, clean, stash, force push or volume operation occurred.

## Recovery verification

- 373 targeted unit/API/workflow tests passed on the recovered dev-based tree.
- 60 Mongo integration tests passed on UUID fixture databases at 127.0.0.1:27037.
- 2 PostgreSQL checkpoint tests passed on UUID schemas of the temporary test database
  at 127.0.0.1:55437.
- 88 deterministic enriched evaluation cases passed (zero failures).
- git diff --check passed; added-content secret-signature scan found no matches.
- The 39-file inventory below matches the proposed changed and untracked paths.
- The earlier full unit result below belongs to the original validated hotfix tree;
  the complete suite was not rerun for recovery, only the relevant suites above.

## Confirmed causes and isolated reproduction

The original `MongoProductRepository.find_by_name` searches only `name`, using an
unescaped, unanchored regex and `find_one`. ORDER preparation calls that method through
CheckStock. PreparedOrder previously discarded the SKU, and finalization resolved the
name again. Those are code-proven failures, independent of model quality.

Executing the original repository from origin/dev against an in-memory catalogue gave:

| Input | Original behavior |
| --- | --- |
| DEMO-DRILL-0476 | NOT_FOUND despite stock 74 |
| SP11-SCR-0081 | NOT_FOUND despite stock 10 |
| Northline Tools 18V Brushless Drill 0476 | Correct product |
| Bosch D461 12V | NOT_FOUND for Bosch Jeu de tournevis D461 12V |
| Bosch | First plausible product selected arbitrarily |
| DEMO-DRILL-9999 | NOT_FOUND |
| Bosch Empty D000 | Product found, stock 0 |
| Élite Kit [D.4]+ (12V) | NOT_FOUND due to regex interpretation |
| Bosch DEMO06406657 | Correct product |

The classifier prompt explicitly removes generic category words from product_name.
This can encourage shortening; the specific Docker model response was not sampled.
Tests supply shortened and invented outputs as fixtures, without calling a live LLM.
Context resolution itself does not access the catalogue; it only handles bounded
same-owner continuations. ORDER and STOCK_CHECK originally shared the same name lookup.

## Architecture and resolution policy

ResolveProductUseCase is an application component depending on ProductRepositoryPort.
ProductResolution returns RESOLVED, AMBIGUOUS or NOT_FOUND, with the actual domain
Product on success. Production preparation receives the resolver through the
composition root. The existing check-stock constructor remains compatible with fakes
and callers; Mongo's compatibility name lookup delegates to the same resolver.

Explicit SKU evidence in the user message precedes model hints. High-confidence SKU
syntax is a hyphenated reference containing a digit or written in uppercase, or a
reference following a `SKU` label. An unknown explicit SKU never falls back to names.
Direct catalogue SKU references are also supported. Case-insensitive SKU resolution
reads at most two documents and returns ambiguity on duplicates.

A full normalized user name has priority. Normalization folds case, ordinary Latin
accents and repeated whitespace. All terms in the user product phrase constrain catalogue selection; a shortened
model hint cannot discard model, voltage, variant or reference evidence; a trusted same-owner continuation may reuse the prior resolved SKU. Exact
names must be unique. Partial names require literal term matches and at least two
meaningful terms with a numeric identifier, or three meaningful terms. Multiple
candidates and candidate-limit exhaustion produce AMBIGUOUS; no candidate is selected
by position or stock. There is no fuzzy engine or external search.

Mongo regex literals are escaped, exact queries are anchored, and candidate reads
are capped at two for exact/SKU searches and 21 for partial searches. Reference and
message sizes are also bounded. Preparation reports unavailable/insufficient stock
from Mongo and performs no reservation.

## Identity, transactions and ownership

PreparedOrder and pending_order carry product_sku, product_document_id and the real product_name. The
canonical SKU also survives quantity continuations, confirmation and persistence.
Confirmation does not resolve the name again for new SKU-backed orders. The Mongo
transaction verifies SKU uniqueness and its equality with the prepared Mongo document
identity, then conditionally decrements using both SKU and _id with stock >= requested quantity, using the existing snapshot transaction and
majority commit. Order insertion and reservation still commit or roll back together.
The internal reserved_product_id remains saved for precise cancellation/restoration.

Replays compare SKU, documentary identity when supplied, quantity and customer. A changed display
name does not cause a second reservation or a false conflict. Different SKUs,
quantities or owners conflict for the same operation_id. Cancelled-order replay
recovers the persisted cancellation without creating or reserving again. Concurrent
confirmations reserve once. Deletion/recreation under the same SKU and a new ObjectId cannot cause substitution.
Quantity continuations retain the prepared documentary identity as well.

## Older persisted state

All added fields are optional on existing models/checkpoints; no data migration is
required. Old orders still deserialize. A pending checkpoint missing SKU or documentary identity is processed
in replay_only mode: if its operation already exists with the same business payload
and owner, the existing order is returned without writing stock. Otherwise the
workflow returns PRODUCT_IDENTITY_REQUIRED and asks for a new preparation with SKU.
An unconfirmed old name alone cannot prove identity after catalogue changes.

Existing low-level name-based APIs remain exact-and-unique for compatibility.
Compensation still uses reserved_product_id when present. Legacy cancellation without
that internal identity keeps conservative ambiguity checks and refuses names with
regex metacharacters rather than guessing a historical reservation target.

Real PostgreSQL tests reconnect separate graphs/connections to verify persistence
of product_sku/product_document_id and readable older checkpoints. No checkpoint schema or
serializer migration was introduced.

## Validation (2026-10-12)

- Full unit suite: 1578 passed, 63 integration tests deselected at collection.
- Mongo integration: 60 passed, including existing transaction/import tests and the
  new resolution/identity scenarios. Only UUID fixture databases on a new temporary
  Mongo replica set at 127.0.0.1:27037 were used.
- PostgreSQL checkpoint integration: 2 passed, using UUID schemas on a new temporary
  PostgreSQL at 127.0.0.1:55437.
- Deterministic enriched evaluations: 88 cases, zero failures. This is a fixture
  smoke check, not a measurement of live LLM accuracy.
- Targeted final confirmation-message/resolution tests: 42 passed; cancellation and
  related API/unit checks: 187 passed.
- pip check: no broken requirements. git diff --check: passed.

The suites cover both reported SKUs, complete/abbreviated names, similar products,
unknown SKUs, case/accents/regex characters, no stock, changing stock, renames,
replacement by another SKU, double/concurrent confirmation, same-operation replay,
separate clients and ownership conflicts, cancellations, old pending states,
STOCK_CHECK, ORDER_STATUS, and preparation without stock decrement.

No active database, catalogue, release image, tunnel or V11 maintenance was used or
modified. No live demo or official v0.1.1 Docker image was rebuilt or exercised.

## Residual limits

Intent and quantity extraction still depend on the existing classifier. Product
selection is deterministic once ORDER reaches preparation; this change does not
promise that every natural-language request will classify as ORDER. Unsupported
phrasing or insufficient product evidence can return NOT_FOUND or clarification.
Normalization covers ordinary Latin accents, not every Unicode transliteration.
Literal case-insensitive regex searches bound returned candidates but can scan the
catalogue; no new name-search index or engine was introduced. More than 20 partial
candidates are conservatively ambiguous. Historical checkpoints with no SKU or documentary identity require
re-preparation unless their operation already exists. Unverifiable historical
compensations require manual handling rather than guessed stock changes.

## Three blocking review findings — resolved

1. Preparation now records the private Mongo document identity alongside the canonical
   SKU. Atomic confirmation requires both; a replacement document is refused.
2. User evidence constrains all partial candidates before selection. Shortened LLM
   output cannot select an exact shorter name that contradicts explicit voltage or
   variant tokens. Every candidate remains subject to ambiguity checks.
3. Cancellation requires the persisted reserved_product_id and, when recorded, its
   agreement with product_document_id. Missing/invalid/deleted historical references
   return MANUAL_INTERVENTION_REQUIRED (HTTP 409). No name lookup, stock credit, order
   status change or cancellation event is committed. Already-cancelled replay remains
   readable. Independent name-only release paths refuse automatically as well.

Tests additionally simulate a lost successful commit response, unavailable initial
reconciliation and concurrent retries: the persisted operation is recovered once.
The former unit cancellation fixtures were updated to represent verified reservations;
legacy fixtures now explicitly expect manual intervention instead of guessed credits.

The identity guarantee is equality of SKU and Mongo _id. An administrator deliberately
recreating both the same SKU and the same _id cannot be detected without an immutable
creation generation or tombstone; that is outside the normal ObjectId recreation case.

## Files changed

- `docs/order-product-resolution-hotfix.md`
- `src/stockpilot/application/dto/prepared_order.py`
- `src/stockpilot/application/order_idempotency.py`
- `src/stockpilot/application/ports/order_transaction.py`
- `src/stockpilot/application/ports/product_repository.py`
- `src/stockpilot/application/stock_identity.py`
- `src/stockpilot/application/use_cases/check_stock.py`
- `src/stockpilot/application/use_cases/finalize_order.py`
- `src/stockpilot/application/use_cases/prepare_order.py`
- `src/stockpilot/application/use_cases/release_stock.py`
- `src/stockpilot/application/use_cases/resolve_context.py`
- `src/stockpilot/application/use_cases/resolve_product.py`
- `src/stockpilot/application/workflows/nodes.py`
- `src/stockpilot/application/workflows/state.py`
- `src/stockpilot/domain/entities/order.py`
- `src/stockpilot/domain/entities/product.py`
- `src/stockpilot/domain/enums/order_status.py`
- `src/stockpilot/infrastructure/persistence/mongodb/order_repository.py`
- `src/stockpilot/infrastructure/persistence/mongodb/order_transaction.py`
- `src/stockpilot/infrastructure/persistence/mongodb/product_repository.py`
- `src/stockpilot/main.py`
- `src/stockpilot/presentation/api/routes/backoffice_orders.py`
- `src/stockpilot/presentation/api/routes/orders.py`
- `tests/test_backoffice_mutations.py`
- `tests/test_cancel_order.py`
- `tests/test_cancelled_order_replay.py`
- `tests/test_customer_cancel_order.py`
- `tests/test_deterministic_product_resolution.py`
- `tests/test_metrics.py`
- `tests/test_order_conversation.py`
- `tests/test_order_events.py`
- `tests/test_order_product_identity.py`
- `tests/test_order_resolution_mongo_integration.py`
- `tests/test_order_resolution_safety_integration.py`
- `tests/test_order_status_workflow.py`
- `tests/test_postgres_checkpoint_integration.py`
- `tests/test_prepare_order.py`
- `tests/test_release_stock.py`
- `tests/test_reserve_stock.py`
