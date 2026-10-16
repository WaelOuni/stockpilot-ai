# Référence DEMO versionnée et workspace persistant

Le générateur et son hash vérifient le seed, pas les valeurs courantes de la base
métier. Les produits du seed sont modifiables par les API autorisées ; les
commandes et mouvements peuvent évoluer/être ajoutés. Le manifeste d’origine
reste immuable. Le lanceur utilise désormais des contrôles structurels et
l’identité des stores, sans exiger l’égalité de tous les champs métier au seed.
Une seule base physique `stockpilot_demo` est conservée.

Voir [le runbook courant](demo-runbook.md) et [le rapport de correction](archive/demo-lifecycle-validation.md).
Le validateur strict de seed reste utilisé lors de l’initialisation et pour les
anciens audits de référence. Les inventaires et consignes d’audit ci-dessous
sont des instantanés historiques V6/V7 ; ils ne restreignent pas l’utilisation
normale actuelle de la DEMO.

---

# Synthetic demo dataset: initialization and measured read verification

**Synthetic demo only — not commercial orders, customers, or inventory.**
Initialization of `stockpilot_demo`, four collections, six custom indexes, and
the reference document counts below was explicitly authorized and performed on
2026-10-10. No `DELETE`, `DROP`, replacement, write to the normal `stockpilot`
database, PostgreSQL write, or E2E reset was performed. Pre-existing V6 work is
preserved.

From the project root in WSL/Linux:

```sh
.venv/bin/python scripts/prepare_demo_dataset.py \
  --database stockpilot_demo \
  --dry-run

# Explicit authorized insert-only mode; an identical replay does not write.
.venv/bin/python scripts/prepare_demo_dataset.py \
  --database stockpilot_demo \
  --initialize

# Reads only; never starts the production lifespan/checkpointer.
.venv/bin/python scripts/verify_demo_dataset.py \
  --database stockpilot_demo \
  --iterations 20 \
  --report data/demo-read-report.json
```

The configured `MONGO_URI` must explicitly declare `replicaSet=rs0`; an explicit
database name in that URI must be absent or exactly `stockpilot_demo`. The CLI
also requires the exact `--database stockpilot_demo` target. It never prints the
URI. The dry run does not connect. Initialization and verification check the
actual connected database name and `hello` response: writable `rs0` primary,
logical sessions, and transaction-capable wire version, before any write.
Use an externally configured URI; do not put secrets in command lines or reports.

The default seed is `20261010`. It describes 500 synthetic products, 2,000
synthetic orders, 3,000 synthetic administrative stock adjustments and 100
fictional customer IDs. `--seed N` selects a different stable dataset. Product
SKUs/names and customer IDs are visibly synthetic; the generated order
transitions use existing `OrderStatus` and `OrderEventSource` values, and stock
adjustments use the existing `BACKOFFICE` source. Stable identifiers and a
SHA-256 manifest fingerprint make dry-run output reproducible. The default
dataset ID is `stockpilot-demo-v1-seed-20261010`; its verified fingerprint is:

```text
a7112fc09fb3afd2618dfd3bb7b8f5a2e3f83203b5e40d17e64083bdeee8d22c
```

Inventory starts from a seeded 45..140-unit in-memory opening balance per SKU, applies order
reservations and cancellation returns, then applies chronologically ordered
administrative adjustments. The fixture validator checks per-SKU arithmetic,
unique SKU/order/operation identities, references, legal status transitions,
event sources, increasing event timestamps and terminal-status agreement.
Persisted verification compares **every document and manifest**, including all
fields and BSON-normalized timestamps, to the deterministic fixture, not just
counts. The persisted `stock_movements` history describes
only the synthetic administrative adjustments: it is **not** a complete
inventory ledger, and must not be used to infer historical/global
conservation. Generated cancellation arithmetic is not proof that production
transactions avoid double compensation.

This v1 fixture intentionally has a fixed synthetic timeline (orders through
2026-07-05; adjustments through 2026-11-11, including dates after execution).
All 500 products have positive final stock; the empty unavailable filter is
tested, but no persisted zero-stock boundary case is present. Stable fixture
UUIDs are v5; current stock-adjustment submission/operation-lookup endpoints
require v4. Thus these historical IDs are suitable for history display, **not**
for replaying a stock-adjustment mutation or that v4-only operation lookup.
Customer/order status assignment is deterministic, not representative traffic.
Do not silently rewrite this initialized fixture to change these characteristics.

## Authorized and verified schema

The initializer is limited to Mongo database
`stockpilot_demo` and these named collections:

| Collection | Verified documents | Custom indexes |
|---|---:|---|
| `products` | 500 | `products_sku_unique` (`sku` ascending, unique, simple collation) |
| `orders` | 2,000 | `orders_order_id_unique`; `orders_operation_id_unique` (unique partial string); `orders_customer_created_order_desc` |
| `stock_movements` | 3,000 | `stock_movements_operation_id_unique`; `stock_movements_product_history` |
| `demo_manifests` | 1 | Mongo's built-in unique `_id_` only |

Mongo's built-in `_id_` indexes are implicit: four built-in plus exactly six
custom indexes were verified. Approved definitions:

| Index | Keys | Options |
|---|---|---|
| `products_sku_unique` | `sku: 1` | unique; simple collation |
| `orders_order_id_unique` | `order_id: 1` | unique |
| `orders_operation_id_unique` | `operation_id: 1` | unique; partial `{"operation_id": {"$type": "string"}}` |
| `orders_customer_created_order_desc` | `customer_id: 1, created_at: -1, order_id: -1` | non-unique |
| `stock_movements_operation_id_unique` | `operation_id: 1` | unique |
| `stock_movements_product_history` | `product_sku: 1, occurred_at: -1, movement_id: -1` | non-unique |

These support identities and per-customer/per-product histories; they do **not**
cover every global dashboard/list sort. No additional performance index was
created. Product references use stable
Mongo `ObjectId` values and generated order `reserved_product_id` values refer
to those exact products. `demo_dataset_id` on generated documents and the
manifest identify synthetic records. The 100 fictional customer IDs are
embedded in orders (`demo-customer-001` through `demo-customer-100`); there is
no customer collection or customer PII.

### Insert-only/idempotent behavior

The writer refuses unapproved collections, collection options, incompatible
indexes, extra indexes, partial data, a mismatched seed/content/manifest, or
completed data missing an approved index. It accepts an empty target, the
exact complete reference fixture, or that fixture plus separately manifested
UUIDv4-owned test documents. Reference documents are still compared field by
field with the deterministic seed; test documents are classified separately
and never folded into its SHA-256 fingerprint. An exact replay only reads and
reports `replayed`.
After empty-target preflight, approved collection/index DDL is provisioned,
then all 5,501 documents (including the manifest) are inserted in one snapshot/
majority transaction, using batches of at most 500. The snapshot rechecks
contents before insertion. Content is verified again after commit.

Collection/index DDL is outside the data transaction: an infrastructure failure
can leave empty approved collections/indexes. They can be reused by an identical
retry. No automatic cleanup or repair is allowed. MongoDB's transaction helper
handles retryable transaction/commit errors; an unresolved commit error is
reported without printing connection details. Retry **the identical seed** to
verify contents; never delete or replace data to resolve ambiguity.

## Persisted integrity and HTTP correctness

`data/demo-read-report.json` records the actual read verification. It mounts
existing FastAPI routers with their real use cases and Mongo adapters in an
isolated application, without importing `stockpilot.main`, invoking production
lifespan, repository initialization, or opening PostgreSQL. Authentication uses
the real session login/cookie checks and customer JWT validation, with randomly
generated credentials held only in memory (not written or logged). A middleware
rejects all data mutations with 405; session login/logout only change memory.

Actual Mongo command auditing recorded zero persistent write commands during
verification. The immutable reference documents matched before and after;
manifested test documents are reported separately. Catalogue and admin-order
pagination include both reference and explicitly owned test fixtures; customer
history remains scoped to its selected reference customer. Missing details and
foreign-customer detail returned 404; invalid pagination returned 422; the
out-of-range page was empty and the unavailable filter matched persisted stock.

The immutable reference baseline remains 500 products, 2,000 orders
(CREATED 200, PROCESSING 200, SHIPPED 200, DELIVERED 1,200, CANCELLED 200),
3,000 stock movements and **41,419 stock units**. After isolated transaction
checks, the earlier HTTP dashboard snapshot included the retained test fixtures:
552 products (544 available, 8 unavailable, **41,874 stock units**) and 2,017
orders (CREATED 214, PROCESSING 200, SHIPPED 200, DELIVERED 1,200,
CANCELLED 203). The four manifested test runs contain 52 marked products, 17
marked orders and 35 marked movements, with two failed harness attempts
preserved separately. No normal `stockpilot` documents are included. These are
the pre-runtime figures; the refreshed `data/demo-read-report.json` records
554 products, 2,018 orders, 3,039 movements and 41,882 stock units across six
test runs after V7 runtime validation. See the runbook for the V7 results.

## Bounded read performance (not production capacity)

Historical pre-runtime measurement on 2026-10-10 (552 products / 2,017 orders):
the table below predates the refreshed `data/demo-read-report.json`.
10 sequential timed requests per endpoint after one
warm-up, 100 timed requests total, **zero HTTP errors**. Percentiles use
nearest-rank samples. Measurements include in-process TestClient dispatch,
authentication, use cases, real local Mongo reads and serialization. They
exclude external HTTP/TLS transport, browser rendering, production deployment,
and concurrent mutation traffic; this is not a capacity/SLA claim. The data is
the 500/2,000/3,000 reference fixture plus the retained manifested test docs.

| Read endpoint | Requests | Errors | p50 ms | p95 ms |
|---|---:|---:|---:|---:|
| Catalogue | 10 | 0 | 1.861 | 2.002 |
| Catalogue filters | 10 | 0 | 2.089 | 2.469 |
| Unavailable filter | 10 | 0 | 1.767 | 2.363 |
| Product detail | 10 | 0 | 1.381 | 1.558 |
| Dashboard | 10 | 0 | 9.497 | 10.339 |
| Customer history | 10 | 0 | 2.090 | 2.388 |
| Customer detail | 10 | 0 | 1.675 | 2.081 |
| Admin order list | 10 | 0 | 3.906 | 4.411 |
| Admin order detail | 10 | 0 | 1.551 | 1.949 |
| Product stock history | 10 | 0 | 2.194 | 2.519 |

Read-only `explain("executionStats")` showed product detail using
`products_sku_unique` (one document/key); customer history using
`orders_customer_created_order_desc` (20 documents/keys); and stock history
using `stock_movements_product_history` (six documents/keys). Catalogue and
filtered catalogue use COLLSCAN + SORT (552 examined); admin orders use
COLLSCAN + SORT (2,017); dashboard recent adjustments use COLLSCAN + SORT
(3,035). The `{sku: 1}` unique index does not satisfy the repository's full
`{sku: 1, _id: 1}` sort. No profiler/global settings were enabled and no
unapproved index was added.

## Authorized isolated transaction verification

Live mutations were executed only against the separately marked UUIDv4 test
fixtures in `stockpilot_demo`, using the real Mongo transaction adapters. The
run manifest resides in the existing `demo_manifests` collection; no
collection/index/schema was added. Products, orders, movements, manifests and
operation IDs are retained; no cleanup is performed. The initial `--dry-run`
is read-only. **Writes require `--execute`**, which checks the actual
`stockpilot_demo` database, writable `rs0` primary, baseline fingerprint and
the exact existing six custom indexes before inserting test fixtures.

`data/demo-transaction-report.json` contains the measured run, its baseline
before/after, test-document overhead, command audit and scenario outcomes.
Latest completed execution validated:

- Two concurrent reservations against one available unit yielded one order and
  stock zero; concurrent duplicate order and stock-adjustment operation IDs
  each produced one persisted transaction.
- Concurrent cancellation restored stock once and appended one cancellation
  event; concurrent order reserve plus stock adjustment conserved the expected
  final stock.
- Injected movement-insert failure rolled stock back. Two injected transient
  aborts were bounded by the adjustment transaction's three-attempt limit.
- A real commit followed by a simulated lost transaction response reconciled
  to one order/movement. Aborted transactions with simulated unknown results
  remained reported as unknown (not success) and left no order/movement or
  stock change.
- A simulated HTTP timeout dropped the response only after the real POST
  completed. UUID operation lookup and stock-history reread found exactly one
  movement; no automatic POST retry occurred.
- The guarded POST route completed 12/12 timed benchmark calls: p50 **7.310 ms**,
  p95 **10.119 ms**. These are sequential in-process TestClient/local-Mongo
  timings, not external HTTP capacity, concurrency throughput, or an SLA.
  Stable stock-history pagination returned 13 unique movements across three
  pages.

Two earlier harness attempts are retained and explicitly counted as failed
tooling runs in the report; they did not alter the reference baseline. One
duplicate-order probe initially used different operation IDs; a fault wrapper
also initially failed PyMongo's native ClientSession type check. Both harness
defects were corrected before the completed run. No business transaction code
was changed for these probes.

The real admin session expiry/relogin checks and app recomposition remain
separate from process/service restart. CSV import remains excluded because it
requires an unapproved receipt collection/index. PostgreSQL assistant/
checkpointer startup and writes remain excluded. Playwright/E2E remains
excluded because its normal environment setup resets/writes test data. No
shared Mongo restart, PostgreSQL access, E2E start/reset, or production
application startup occurred.

## Reproduction and confidence

Safe validation commands:

```sh
.venv/bin/python -m pytest -q \
  tests/test_demo_dataset.py tests/test_demo_persistence.py \
  tests/test_demo_read_verification.py tests/test_demo_transaction_verification.py
.venv/bin/python -m pytest -q  # default excludes integration tests
cd frontend
npm test
npm run build
npm run lint
```

With `MONGO_URI` supplied to the process by the local secret/environment
configuration (never as a command-line argument), reproduce the live checks:

```sh
.venv/bin/python scripts/verify_demo_transactions.py \
  --database stockpilot_demo --dry-run
.venv/bin/python scripts/verify_demo_transactions.py \
  --database stockpilot_demo --execute --iterations 12 \
  --report data/demo-transaction-report.json
.venv/bin/python scripts/verify_demo_dataset.py \
  --database stockpilot_demo --iterations 10 \
  --report data/demo-read-report.json
.venv/bin/python scripts/prepare_demo_dataset.py \
  --database stockpilot_demo --initialize
```

The transaction run adds 13 products, 5 orders and 17 stock movements per
completed execution (plus the run manifest); retained failed harness runs also
remain separately marked. `--iterations` is bounded to 10–20. The last
initialization replay validates reference and test records and does not insert,
delete, replace, or modify indexes when the fixture is already complete.

Backend ordinary suite, frontend unit/build/lint, schema/integrity, insertion/
replay and read HTTP verification are distinct evidence. Passing unit tests
does not establish live concurrency, no double compensation, real failure
recovery or production capacity.

This phase's full backend run passed **1,318 tests, 22 integration tests
deselected**; targeted demo integrity/verification tests passed **31/31**.
Prior unchanged frontend validation passed **355 tests in 19 files**, plus
typecheck/build and lint. No integration test, Playwright reset, PostgreSQL
write, commit or push is part of this workflow.

## Integrated local application (V7)

The guarded launcher serves the real FastAPI application and Vue UI over a
same-origin development proxy; it does not generate or reinitialize the Mongo
fixture. Run from WSL/Linux without sourcing the DEV environment or exporting
database variables; the launcher selects the dedicated stores before preflight.
See [demo-runbook.md](demo-runbook.md) for private overrides.

```sh
bash scripts/run-demo.sh
```

Do not put database credentials in shell history, this document, a Vite
`VITE_*` variable, or a command argument. The launcher rejects Mongo URIs that
name another database, any Mongo target other than `stockpilot_demo`, and
PostgreSQL/checkpoint URIs that do not explicitly select `stockpilot_demo`.
Before it starts either server, it verifies the existing Mongo replica-set
primary, exact approved collections/indexes, immutable reference hash/content,
and separately manifested test fixtures. It also connects to PostgreSQL in a
read-only transaction and requires the existing, current checkpoint schema and
RAG `knowledge_chunks` schema; it reports the existing RAG row count without
reading corpus contents. Preflight never creates a database or runs migrations.
After preflight, the existing application startup requests Mongo indexes and
calls the official checkpointer `setup()` against the verified current schema;
startup is not a strictly read-only operation. No RAG seed, E2E reset or
fallback to `stockpilot` is part of the launcher.

The FastAPI server binds only `127.0.0.1:8003`; Vite binds only
`127.0.0.1:5175`, uses strict port selection, and proxies `/api` to that API.
Vite receives only a small allowlist of environment values and uses an empty
temporary env directory, so server credentials are not exposed to the browser.
The administrator username is entered at the terminal and the password is
prompted with hidden input; only a salted PBKDF2 verifier is passed to FastAPI.
The development-only local HTTP session cookie is host-only and the trusted
Origin is exactly `http://127.0.0.1:5175`. Ctrl+C stops only the two process
groups launched by the script. Do not expose either loopback service to a
network.

The real application includes business mutation routes. Use them only against
the isolated synthetic `stockpilot_demo` data. Do not run the normal seed,
dataset initializer, E2E reset, index-preparation, migration, or ingestion
scripts as part of this walkthrough. The existing Mongo startup index requests
are accepted only after the launcher verifies the exact already-approved
definitions. If any check fails, no application process starts.

V7 subsequently received explicit authorization to create **only**
PostgreSQL `stockpilot_demo` and its required existing schemas. The existing
Docker PostgreSQL server was identified in read-only probes and its private
credentials resolved without changing normal configuration. The dedicated
database, vector/knowledge migrations and official LangGraph setup are now
verified. `knowledge_chunks` remains empty: no RAG ingestion or reliable advice
evaluation is claimed. The actual guarded FastAPI/Vue launcher and separate
no-reset demo browser now run successfully; see [demo-runbook.md](demo-runbook.md)
for actual launch, shutdown, validation commands and scope. The exclusions in
earlier sections describe that earlier verifier phase, not this authorized V7
runtime continuation.

### Verifier walkthrough and side effects

The offline dry run and read verifier do not mutate business data. The isolated
transaction verifier with `--execute` writes retained test fixtures; it is not
a read-only check and requires separate authorization.
Read the separate baseline/test-fixture totals and dashboard numbers in their
reports. The verifiers intentionally start no listening network server or
browser UI. For an integrated browser/assistant walkthrough, use only the
guarded V7 launcher above; never launch `stockpilot.main` directly with normal
database settings.

Files for this workflow: `src/stockpilot/demo_dataset.py` (deterministic fixture),
`src/stockpilot/demo_persistence.py` (connection/schema/content guards and writer),
`scripts/prepare_demo_dataset.py` (offline/explicit initialize CLI),
`scripts/verify_demo_dataset.py` (read-only router/adapters verifier),
`scripts/verify_demo_transactions.py` (explicitly gated isolated transaction
runner), the demo tests, this document, and both JSON reports (synthetic data
only; no credentials/URI).
