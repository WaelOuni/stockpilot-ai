# Databases and persistent conversations

[Back to README](../README.md) · [Getting started](getting-started.md) · [Order flow](order-flow.md) · [Synthetic demo dataset](demo-dataset.md)

All commands run from the project root in WSL/Linux. They are manual operational
procedures, not steps performed by the documentation refactor or real-eval CI.
Preserve existing volumes and private backups; never use `docker compose down -v`
to troubleshoot unknown data.

## Storage responsibilities

| Store | Contents |
|---|---|
| Mongo `stockpilot` | Products, atomically finalized orders and administrative stock movements (V4 activation gated) |
| PostgreSQL `stockpilot` | Application RAG and default conversation checkpoints |
| PostgreSQL `stockpilot_eval` | Dedicated, repeatable product-document eval corpus |

Order documents include an embedded `events` array (`status`, UTC ISO 8601
`occurred_at`, `source`). Creation inserts CREATED/SYSTEM once; status updates
atomically append with the status change, and cancellation appends in the stock
transaction. Events need no new index or collection. Legacy documents without the
field remain readable as an empty history; no historical data is backfilled.
See [embedded order events](order-flow.md#embedded-order-events) for write and
replay guarantees.

Compose uses PostgreSQL 16/pgvector and MongoDB 8. Mongo is a **single-node
replica set `rs0`**, required for multi-document transactions, not high availability.
PostgreSQL/RAG/Ollama are unaffected by the Mongo replica-set conversion.
The demo database ports are not loopback-restricted; use a trusted local firewall.
Public Compose credentials are demo-only, never production credentials.

## Application RAG schema

[Getting started](getting-started.md#fresh-application-rag-database) contains
the fresh `stockpilot` migration commands.
[000](../migrations/000_create_knowledge_chunks.sql) creates the vector
extension/table; [001](../migrations/001_knowledge_chunks_source_unique.sql)
adds source uniqueness. Apply 001 once, not blindly on an existing migrated DB.

The initial schema contains only the columns used by the knowledge repository:

| Column | PostgreSQL type | Constraint |
|---|---|---|
| `id` | `BIGSERIAL` (`BIGINT` with sequence) | Primary key |
| `content` | `TEXT` | Not null |
| `source` | `TEXT` | Nullable; unique after 001, including at most one null |
| `embedding` | `VECTOR(768)` | Not null |

000 uses `IF NOT EXISTS` for the extension and table, not schema repair.
001 owns only `knowledge_chunks_source_unique`, with
`UNIQUE NULLS NOT DISTINCT (source)`.

Inspect constraints and duplicates first. There is no general migration-history
framework; run sequentially, not concurrently. Embeddings are 768-dimensional;
model changes need an explicit migration/corpus plan.

## Dedicated eval database

`stockpilot_eval` needs neither application orders nor checkpoint tables.
It uses the existing Compose PostgreSQL service, not another container.
Prefer the versioned initializer even when the database already exists:

```sh
docker compose up -d postgres
python scripts/init_eval_db.py
```

[init_eval_db.py](../scripts/init_eval_db.py) uses Compose `psql` as the
local demo administrator. It creates `stockpilot_eval` only if absent, applies
000 (extension/table), and applies 001 only if the named source constraint is
absent. It checks final columns/dimension, primary key and the existing
constraint definition. Incompatible schemas fail explicitly, without destructive
repair. No database is dropped and **`stockpilot` is never modified**.
The script does not delete stale corpus rows or replace a migration framework.
001 itself remains a one-time SQL migration.

For a **virgin eval database only**, the equivalent SQL operations are:

```sh
docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U stockpilot -d postgres \
  -c 'CREATE DATABASE stockpilot_eval OWNER stockpilot;'
docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U stockpilot -d stockpilot_eval \
  < migrations/000_create_knowledge_chunks.sql
docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U stockpilot -d stockpilot_eval \
  < migrations/001_knowledge_chunks_source_unique.sql
```

Prefer the initializer if the database may already exist. Populate using the
existing Ollama environment and a **temporary** database override:

```sh
# WSL + Windows-host Ollama, if applicable:
source scripts/dev-env.sh
export POSTGRES_URL='postgresql://stockpilot:stockpilot@localhost:5432/stockpilot_eval'
python scripts/ingest_products.py
# Repeat updates the same three sources instead of inserting duplicates:
python scripts/ingest_products.py
docker compose exec -T postgres psql -U stockpilot -d stockpilot_eval \
  -c 'SELECT id, source FROM knowledge_chunks ORDER BY id;'
python scripts/run_evals.py --mode local --suite historical
```

Use a separate shell, or restore/unset `POSTGRES_URL` afterwards so the API
continues using `stockpilot`. Upserts preserve IDs but sequence values may advance
on conflict. CI checks exact `data/products` contents and rejects stale/extra rows.

The public local-demo URL above is suitable for GitHub environment secret
`EVAL_POSTGRES_URL` **only if the self-hosted runner reaches this same PostgreSQL
on localhost:5432**. Other networks require their reachable address and dedicated
credentials. Ingestion/schema setup needs writes; provision a separate read-only
eval account where possible. Keep real credentials out of shell history/logs.

## Mongo replica-set migration and recovery

Before converting an existing standalone instance, back up while the old
container is still running:

```sh
docker exec stockpilot-mongodb mongodump --archive=/tmp/stockpilot-before-rs.archive
docker cp stockpilot-mongodb:/tmp/stockpilot-before-rs.archive ./stockpilot-before-rs.archive
```

These `/tmp` paths are **inside the Mongo container**. The copied archive contains
application data: keep it private and outside version control.
Recreate **only the container**, preserving its named volume:

```sh
docker compose up -d --force-recreate --wait mongodb
docker exec stockpilot-mongodb mongosh --quiet --eval 'rs.status()'
docker exec stockpilot-mongodb mongosh --quiet --eval 'db.hello().isWritablePrimary'
```

For a new environment use `docker compose up -d --wait`. The healthcheck runs
[docker/init-replica-set.js](../docker/init-replica-set.js), initializes `rs0`
only when uninitialized and waits for PRIMARY. Existing products/orders are
not deleted or backfilled. It does not reconfigure an unexpected replica set.

On failure, preserve the volume and inspect the actual error:

```sh
docker compose logs mongodb
docker inspect stockpilot-mongodb --format '{{json .State.Health}}'
docker exec stockpilot-mongodb mongosh --quiet --file /opt/stockpilot/init-replica-set.js
```

Do not delete the volume or run `down -v`. A normal standalone data directory
can be reused with `--replSet`; failure does not imply erasure is needed.
If data/configuration cannot be reused, keep the original volume and verified
backup, obtain approval before provisioning a separate volume, and restore
there using `mongorestore`. There is no automatic destructive migration.

The following **explicit manual recovery alternative** is not required for normal
migration. It leaves the original container/volume untouched and uses port 27018:

```sh
docker volume create stockpilot_mongodb_rs0_recovery
docker run -d --name stockpilot-mongodb-recovery --hostname mongodb \
  -p 127.0.0.1:27018:27017 \
  -v stockpilot_mongodb_rs0_recovery:/data/db \
  mongo:8 mongod --replSet rs0 --bind_ip_all
docker cp docker/init-replica-set.js stockpilot-mongodb-recovery:/tmp/init-replica-set.js
docker exec stockpilot-mongodb-recovery mongosh --quiet --file /tmp/init-replica-set.js
docker exec stockpilot-mongodb-recovery mongosh --quiet --eval 'rs.status()'
docker cp ./stockpilot-before-rs.archive stockpilot-mongodb-recovery:/tmp/backup.archive
docker exec stockpilot-mongodb-recovery mongorestore --archive=/tmp/backup.archive
```

Wait for PRIMARY; rerun initialization/status commands if election is in progress.
Point `MONGO_URI` to port `27018`, retaining `replicaSet=rs0` and
`directConnection=true`. Inspect recovered data before deciding anything about
the original volume. Do not restore into a populated recovery DB or use `--drop`.

## WSL connection and Docker discovery

The replica set advertises `mongodb:27017`, resolvable inside Compose. The WSL
application defaults to:

```text
mongodb://localhost:27017/?replicaSet=rs0&directConnection=true
```

`directConnection=true` targets the Docker-published port instead of discovering
an unresolvable Docker hostname, supporting sessions/transactions on the single
primary. Override through `MONGO_URI`; if localhost forwarding is unavailable,
replace only the host with a reachable Docker-host address, keeping both
parameters. Compose clients can use `mongodb://mongodb:27017/?replicaSet=rs0`.
`docker exec ... mongosh` connects directly inside the Mongo container.
This is not a production failover topology.

After Mongo is healthy:

```sh
export MONGO_URI='mongodb://localhost:27017/?replicaSet=rs0&directConnection=true'
.venv/bin/python -m uvicorn stockpilot.main:app --reload
```

### Order indexes

`MongoOrderRepository.initialize()` is the single index initialization point,
called by the FastAPI lifespan before serving requests. It creates the following
named indexes idempotently; Mongo's built-in `_id_` index remains unchanged.

| Index | Keys | Purpose |
|---|---|---|
| `orders_order_id_unique` | `order_id ASC`, unique | Exact order lookup, ORDER_STATUS, lifecycle and cancellation |
| `orders_operation_id_unique` | `operation_id ASC`, unique, partial | Creation idempotency, transactional rechecks and reconciliation |
| `orders_customer_created_order_desc` | `customer_id ASC, created_at DESC, order_id DESC` | Customer history with its exact descending sort and pagination |

The operation index retains its existing filter
`{"operation_id": {"$type": "string"}}`: missing/null/non-string legacy IDs are
excluded, not removed. The shared operation lookup explicitly uses
`{"operation_id": {"$eq": operation_id, "$type": "string"}}` so the planner can
prove that this partial index applies. No forced query hint is needed.

Order IDs are UUID strings generated by the creation use cases, and identify a
single order. The full unique order index enforces that invariant. The local
pre-creation audit found 7 orders, no duplicate string order IDs and no missing,
non-string or empty order IDs. Three legacy orders had no string operation ID.
These observations are **not** a validation of another deployment's data.

Before first startup with these indexes on an existing database, inspect its
indexes and identifier compatibility without modifying documents:

```sh
.venv/bin/python - <<'PY'
from pymongo import MongoClient
from stockpilot.infrastructure.config.settings import Settings

settings = Settings()
with MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=5000) as client:
    orders = client[settings.mongo_database]["orders"]
    print("Indexes:", list(orders.list_indexes()))
    for field in ("order_id", "operation_id"):
        duplicates = list(orders.aggregate([
            {"$match": {field: {"$type": "string"}}},
            {"$group": {"_id": "$" + field, "count": {"$sum": 1}}},
            {"$match": {"count": {"$gt": 1}}},
            {"$count": "groups"},
        ]))
        print(field, "duplicate string groups:", duplicates)
        print(field, "missing/non-string:", orders.count_documents(
            {field: {"$not": {"$type": "string"}}}))
        print(field, "empty:", orders.count_documents({field: ""}))
PY
```

Stop and investigate invalid order IDs or duplicates before enabling the unique
index. A full single-field unique index permits at most one null/missing key;
multiple null/missing order IDs can therefore fail its creation too. Non-string
values and arrays require separate manual review, not an automatic conversion.
Missing operation IDs remain compatible, but duplicate string operation IDs
(including empty strings) violate the existing unique partial index.

Initialization never drops/replaces existing indexes, rewrites IDs or deletes
documents. Duplicate-key, incompatible-index and connection errors propagate and
fail startup explicitly; reconciliation is a separate manual decision. A failed
initialization may leave an earlier successfully created index in place.

The conditional updates filter by `order_id` and the expected `status`.
The unique order lookup already selects at most one order, so neither a status
index nor an `(order_id, status)` index adds useful selectivity. Cancellation
uses the same lookup/update inside its existing transaction; the separate
product-stock query and transaction semantics are unchanged. No isolated
`created_at` or `customer_id` index is needed: the history compound index covers
that access pattern. No index on `status` is added.

For read-only plan verification, use Mongo's `explain` command with
`verbosity="executionStats"` on the exact filters above and the history sort.
An explained `findAndModify` does **not** execute its update. Check the winning
plan's index name, examined keys/documents and absence of blocking `SORT` for
history. Explain does not validate transaction atomicity or retries; the real
Mongo transaction tests cover those. The compound index preserves the existing
ISO-string timestamp ordering and offset-pagination limitations.

New assistant orders persist `customer_id`; old documents without the field
deserialize as `None`, remain unowned and are not consultable by any customer.
No migration/backfill is performed; history queries filter by the authenticated
customer directly in Mongo. Idempotency still uses the global operation index
and rejects a different owner on replay.
Privileged lifecycle/cancellation updates preserve the stored owner.

## Public operational product catalogue

Optional `q`, `category`, `available` filters are combined in Mongo before
skip/limit; `count` remains page size. `q` is trimmed and limited to 100
characters, searched literally (regex metacharacters escaped) in SKU or name
using Mongo `$regex` with `i`. Blank text means no text filter. Category is exact
ProductCategory equality; unknown categories/invalid booleans/oversized q return
422. Availability maps to `{"stock": {"$gt": 0}}` or `{"stock": {"$lte": 0}}`.
No scoring, accent folding, fuzzy matching, synonyms or RAG is involved.

Read-only explain audit on the local three-product dataset found only `_id_` on
products. Unfiltered, text, category, availability and combined queries all use
COLLSCAN plus stable SKU/_id sort, examining 3 documents and 0 index keys.
No performance index was added by that audit: this dataset gives no measured need, and unanchored
case-insensitive substring queries are not served efficiently by ordinary
B-tree indexes. Re-evaluate category/sort indexes and pagination at larger scale,
using representative explain results rather than adding speculative indexes.
The audit did not modify development products or indexes.

`GET /products?limit=20&offset=0` reads Mongo products, sorted by SKU ascending
with `_id` as an internal tie-breaker, before applying skip/limit. Limit is
1..100 and offset is nonnegative; invalid pagination returns 422.
After filtering and offset, Mongo fetches `limit + 1` documents in one query,
returns only `limit` items and reports whether the extra document exists as
`has_more`. No total count query is performed.
`GET /products/{sku}` uses exact case-sensitive SKU equality, not the assistant's
canonical-name regex. SKU is the existing domain identifier, not a new Mongo ID.
The local audit found three products, all with distinct string SKUs. No data
migration or speculative product index was added. A duplicate SKU detail lookup
fails explicitly instead of returning an arbitrary product.

Both endpoints are public and read-only. List response is
`{"items": [...], "limit": 20, "offset": 0, "count": 3, "has_more": false}`;
`count` is exactly the returned page size, not the total. Each item and detail
contains only `sku`, `product_name` (Mongo `name`), `category`, `price`, `stock`.
Mongo `_id` and any extra fields are not exposed. Missing SKU returns 404;
technical failures use the existing sanitized 500 middleware. Empty list is 200.
There is no description/currency in Mongo's current operational product model;
the catalogue does not extract them from RAG or invent them.

```sh
curl --fail-with-body 'http://localhost:8000/products?limit=20&offset=0'
# Use an actual SKU returned by the list, URL-encoded if necessary:
curl --fail-with-body 'http://localhost:8000/products/E2E-BOSCH-X100'
```

The E2E SKU example exists only in the isolated E2E database/server, not
necessarily the normal development API. Public reads, stock reservation,
order confirmation, transactions and RAG remain unchanged.

## Product metadata administration and SKU activation (V4.1)

`GET /backoffice/products` and `GET /backoffice/products/{sku}` reuse catalogue
reads, filters and authoritative `has_more`. Reads follow existing admin
session-or-technical-token conventions. `POST /backoffice/products` creates
only `sku`, `name`, `category`, `price`, with server-owned `stock=0`;
`PATCH /backoffice/products/{sku}` accepts a nonempty subset of `name`,
`category`, `price` only. Blank names/SKUs, null metadata, unknown categories,
nonfinite/negative prices and extra fields are rejected. SKU is immutable:
even resubmitting the same SKU in PATCH is forbidden. No DELETE, disable or
stock field is accepted by these metadata endpoints. V4 adjustments use the
separate transaction endpoint below.

Mutations require a valid admin cookie, exactly the trusted Origin and the
existing session-bound CSRF token. Any explicit Authorization is refused.
Creation returns 201; edit 200, missing 404, duplicate SKU 409, technical
failure sanitized 500, missing required index 503. Responses contain persisted
public product values, never internal Mongo/order references.

Every metadata update is one `find_one_and_update` with a whitelisted `$set`
and the post-update document. It does not replace/upsert/delete a product or
touch stock, SKU, `_id` or unknown fields. Concurrent stock increments/decrements
are preserved. Existing orders keep their native `reserved_product_id` and
snapshot product name. Cancellation with that reference remains safe after
rename. Pending confirmations still resolve their prepared name and can fail
after a rename; legacy cancellation identity limitations also remain.
No aliases, name migration, checkpoint rewrite or workflow semantic change.

### Required exact unique SKU index

The core invariant requires a **full unique single ascending `sku` index with
simple (case-sensitive) collation** (`products_sku_unique` when prepared here).
A compound, partial, sparse, nonunique or case-insensitive index is insufficient.
Both create and metadata edit inspect actual indexes before any write and fail
closed if this prerequisite is absent. Reads remain usable.

The preflight is not the concurrency guarantee: Mongo's unique constraint
arbitrates inserts, and authoritative index creation rejects duplicates racing
the preflight. Competing creates yield one persisted product and duplicate
conflicts, not find-then-insert successes. Administrative removal of the index
must not race writers; keep it provisioned and restrict index-management privileges.
No speculative search/category/stock index is added.

There is **no startup product index initialization**. The opt-in command defaults
to read-only, requires an explicit database target and validates any database in
the URI against it. It checks every SKU for invalid/missing/nonstring/blank values
and counts duplicate exact strings without printing product data. Unsafe data
blocks preparation; no data is deleted, merged, renamed or repaired silently.
Conflicting existing indexes/build failures are refused, never dropped.

```sh
# Read-only inspection; does not create an index or mutate documents:
.venv/bin/python scripts/prepare_product_sku_index.py --database stockpilot

# OPERATOR PROCEDURE ONLY, after separate authorization and backup/data review.
# NOT executed on the normal database during V4.1 implementation:
.venv/bin/python scripts/prepare_product_sku_index.py \
  --database stockpilot --apply --confirm-database stockpilot
```

**Historical V4.1 activation was blocked.** Read-only preflight on
10 October 2026 returned `invalid=0, duplicates=0`, but no qualifying unique
SKU index. The operator procedure above still requires authorization; no normal
database migration/index creation/write was executed. Code is provisioned and
verified only on the authorized `stockpilot_e2e` stores. The existing isolated
initializer prepares just this index after its exact Mongo/PostgreSQL guards and
seed; production startup never calls it. A later separately authorized SKU
activation is now confirmed active by V6 read-only preflight; V6 did not create
or modify that index. Only the additional import receipt prerequisite remains
unprovisioned on normal stockpilot.

## Administrative stock movements (V4)

`stock_movements` is a dedicated collection, not an order event log. Each
committed adjustment stores `movement_id`, stable canonical UUIDv4 `operation_id`,
`product_sku`, signed `delta`, `stock_before`, `stock_after`, trimmed `reason`,
server UTC BSON datetime `occurred_at` and server-selected `source=BACKOFFICE`.
Internal `product_id` retains the native BSON products `_id`; neither reference
nor movement `_id` is returned by the API. Timestamps use Mongo millisecond
precision so replay returns the exact persisted result.

The adapter resolves an exact, simple-collation SKU without ambiguity, then
conditionally `$inc`s **that document's `_id`** and inserts the movement in one
snapshot/majority Mongo transaction. Stored stock must be an integer, remain
nonnegative, and not overflow BSON int64. Insert/validation failures roll back
the stock change. Order reservations and cancellations continue using their
existing identity/transactions; no order or OrderEvent is written by adjustment.
The history is not tamper-proof audit, actor identity or event sourcing.

### Required operation index and explicit provisioning

Every adjustment requires a full unique single ascending `operation_id` index
with simple collation, no sparse/partial/TTL qualification. Missing index
(including an absent collection) fails closed with 503 before any write.
`stock_movements_operation_id_unique` enforces concurrent global operation
uniqueness. Explicit provisioning also creates `stock_movements_product_history`
on `product_sku ASC, occurred_at DESC, movement_id DESC` for stable history pages.
No stock movement index or collection is created at normal application startup.
Keep index-management privileges restricted; removal must not race writers.

```sh
# Read-only by default; never creates a collection/index or changes documents:
.venv/bin/python scripts/prepare_stock_movement_indexes.py --database stockpilot

# OPERATOR PROCEDURE ONLY: requires separate authorization and data review.
# NOT executed against normal stockpilot during V4 implementation:
.venv/bin/python scripts/prepare_stock_movement_indexes.py \
  --database stockpilot --apply --confirm-database stockpilot
```

The explicit target must agree with any URI database; apply requires an exact
confirmation. Preflight counts invalid/missing/nonstring/blank operation IDs and
duplicate exact strings without printing records. Unsafe data or conflicting
indexes fail without merge, deletion, replacement or silent repair. A compatible
operation index is reused. An index-build conflict may leave earlier successful
indexes in place; Mongo arbitrates races after preflight.

**Normal activation was explicitly authorized and verified on 10 October 2026.**
The normal `stockpilot` database now has the full unique simple-collation
`stock_movements_operation_id_unique` index and stable
`stock_movements_product_history` index. The operation created the empty
`stock_movements` collection; it contained zero movements at activation.
The existing three products, nine orders, their full BSON hashes and all
pre-existing indexes were verified unchanged. The application prerequisite
guard passes. No further normal database writes or index changes are part of
the V5 dashboard work. E2E provisioning remains separate and is authorized only
after exact Mongo/PostgreSQL `stockpilot_e2e` checks and the advisory lock.
Initializer cleanup adds only the named `stock_movements` collection to the
existing named products/orders cleanup; no DROP or wildcard cleanup.
Opt-in transaction tests use previously absent strict UUID databases and clean
documents by `_id`, retaining empty collections/indexes.

## Read-only administrative dashboard (V5)

`GET /backoffice/dashboard` requires a valid HttpOnly admin session cookie only;
public access, customer JWTs and technical Bearer credentials are rejected before
the use case reads Mongo. The existing back-office response middleware sets
`Cache-Control: no-store`. No mutation or startup index initialization occurs.

The response combines independent, bounded reads rather than claiming a global
snapshot: a Mongo `$facet` counts all orders and groups the five known lifecycle
statuses (ownerless legacy orders are included), one `$group` calculates product
availability and summed stock units, and two stable queries return at most five
orders (`created_at DESC, order_id DESC`) and five adjustments
(`occurred_at DESC, movement_id DESC`). Recent-order projections exclude owners,
events and operation/internal IDs; movement projections expose only SKU, delta,
date and reason. Recent adjustments are not a substitute for product history.

Available products have stock `> 0`; unavailable products have stock `<= 0`.
Stock units are a non-monetary sum across heterogeneous products; no low-stock
threshold or currency conversion is inferred. Missing/non-numeric product stock
does not satisfy either comparison and is ignored by Mongo's numeric sum.
Concurrent writes can make the independent totals differ slightly in time.

## CREATE_ONLY product CSV imports (V6)

`/admin/products/import` previews a file without writes, then explicitly confirms
one small atomic batch. Existing SKU/name/category/price/stock and all orders,
snapshots and movements are untouched. New products have server-owned stock zero.
Preview is **not** a SKU reservation: catalogue races refuse the entire batch.

### CSV and HTTP contract

The body is raw CSV bytes, not multipart or JSON. `Content-Type: text/csv;
charset=utf-8` is a convention, not a security check; extensions/MIME are never
trusted. No multipart dependency is needed. Both POST routes require a valid
admin session, exact single Origin and session CSRF **before** reading the body
or Mongo. Customer JWTs and technical Authorization are rejected, even with a
cookie. All responses are no-store.

| Endpoint | Request / result |
|---|---|
| `POST /backoffice/products/import/preview` | Raw file; typed counts, issues, valid products/physical starting lines, SHA-256 fingerprint and signed binding |
| `POST /backoffice/products/import/execute` | Same raw bytes; `X-Import-Operation-Id` canonical UUIDv4, `X-Import-Preview` returned binding, `X-Import-Confirm: CREATE_ONLY` |
| `GET /backoffice/products/import/batches/{operation_id}` | Session-cookie-only; committed original operation/fingerprint/created count/SKU list, or 404 (not evidence of rollback) |

UTF-8 is strict, with optional leading BOM. The exact quote-aware header must
contain `sku`, `name`, `category`, `price` exactly once, in any order; no extras
(including stock), missing or repeated columns. A single comma or semicolon
separator is selected only by that exact header. Quotes must start a field,
embedded quotes must be doubled, and nothing may follow a closing quote except
separator/newline. Quoted separators and multiline fields are accepted.
Empty files/header-only files fail; blank physical records are invalid, while
the final line terminator does not introduce an empty record.

```csv
sku,name,category,price
EXAMPLE-DRILL,"Perceuse, exemple",DRILL,99.5
EXAMPLE-SAW,Scie exemple,SAW,49
```

Categories are the existing exact enum: DRILL, SAW, SANDER, SCREWDRIVER, OTHER.
SKU and name must not be blank and retain their exact identity (no trimming,
case folding or normalization). Price uses existing Python float/finite
nonnegative metadata validation, not a new precision/monetary model.
Every internally repeated SKU line is invalid. Existing SKU lookups are one
grouped `$in` query with simple collation, not N+1 queries.

`PRODUCT_IMPORT_MAX_BYTES` defaults to 2 MiB (configurable up to 16 MiB);
`PRODUCT_IMPORT_MAX_PRODUCTS` defaults to 500 (configurable 1..500). Size is
checked against declared Content-Length and during bounded streaming before
full buffering/parsing. Fields are limited to 4096 characters, detailed issues
to 100 (`issues_truncated` reports truncation), and preview products to 500.
Fatal file issues use line 0; record issues/valid products use the physical
starting line (including multiline quoted records). Analysed counts exclude the
header, invalid counts include fatal file errors, and existing conflicts are
separate from valid/invalid records. A blocking issue prevents approval.
Internally repeated rows may also be existing-SKU conflicts; those two blocking
counts can overlap.

Bindings are HMAC-SHA256, tied to the exact byte fingerprint and current session
CSRF, with a 30-minute expiry and process-local random key. Execute reparses
and validates instead of trusting submitted preview data. Restart/expiry requires
a new preview for a new batch; a persisted same-ID/same-byte replay returns the
original receipt even if its old preview expired. Different bytes with that
operation ID return 409. Receipt GET remains usable across restarts/login.

### Atomicity, ambiguity and activation

`MongoProductCsvImport` rechecks operation and all SKUs in one snapshot/majority,
primary transaction, then inserts products plus a `product_imports` receipt.
There is no update/upsert/delete path. The existing full unique simple SKU index
also arbitrates concurrent V4.1 creates. Any insertion/receipt/SKU failure rolls
back all products; known conflicts return 409. Only known-uncommitted transient
transactions retry, at most three attempts. Unknown commit takes priority even
with a transient label: reconcile the receipt using primary/majority, never
blindly reapply. An unproven outcome returns 503. A later GET 404 does not prove
failure; preserve the original ID and seek manual operational resolution.
The receipt exposes no Mongo IDs and stores no uploaded file or credentials.

Every new batch fails closed with 503 unless both the existing SKU prerequisite
and a **full unique single `operation_id ASC`, simple, non-sparse/non-partial/
non-TTL** receipt index exist. No import index/collection is created at startup.
The explicit opt-in script counts invalid/repeated operation IDs without printing
records, never drops/replaces indexes or repairs data, and validates the URI target:

```sh
# Read-only; safe inspection, no collection/index/document creation:
.venv/bin/python scripts/prepare_product_import_index.py --database stockpilot

# OPERATOR PROCEDURE ONLY, after separate authorization and backup/data review.
# NOT executed against normal stockpilot during V6 implementation:
.venv/bin/python scripts/prepare_product_import_index.py \
  --database stockpilot --apply --confirm-database stockpilot
```

Normal read-only inspection on 10 October 2026 confirms the existing SKU index
is now active, but import receipts have `invalid=0, duplicates=0` and no required
operation index: **normal V6 activation remains blocked**, while V4 SKU/stock
activation is preserved. No normal writes/index changes were performed for V6.
Only the guarded `stockpilot_e2e` initializer provisions/cleans the named
`product_imports` collection after existing exact Mongo/Postgres guards and PG
advisory lock. Opt-in real tests use the existing previously absent strict UUID
fixture, retain empty collections/indexes and clean named documents by `_id`,
never drop a database.

## Persistent LangGraph conversations

Startup uses the official `langgraph-checkpoint-postgres==3.1.2`
`AsyncPostgresSaver`. FastAPI lifespan opens its separate connection, calls
official idempotent `setup()` migrations, compiles the graph and closes on
shutdown. Initialization logs exclude conversation contents/credentials.
Connection/setup failure fails startup; there is no MemorySaver fallback.

By default the saver uses `POSTGRES_URL` (also used by RAG).
`LANGGRAPH_CHECKPOINT_DB_URI` overrides only checkpoint storage.
Both accept ordinary psycopg PostgreSQL URIs. RAG `knowledge_chunks` is unchanged.
The official saver owns `public.checkpoint_migrations`, `checkpoints`,
`checkpoint_blobs`, `checkpoint_writes`; do not manually alter these internal
tables. The serializer explicitly allows the application's Intent and
ProductCategory enums plus standard supported types; pickle fallback is disabled.

The HTTP body remains `{"message":"...","thread_id":"..."}` and `/assistant`
requires a valid HS256 JWT with nonblank `customer_id` and numeric `exp` claims.
The same customer/thread pair resumes after restart or on another API instance
using the same DB. Its checkpoint key is a versioned SHA-256 of the unambiguous
JSON pair; different customers sharing a raw thread remain isolated.
Old raw-thread checkpoints are not resumed or migrated. `X-Customer-Id` and
body fields cannot override the verified JWT identity. Saved `pending_order`,
owner, quantity and `operation_id` are restored; confirmation never regenerates
the identifier.
Old in-memory conversations cannot be recovered retrospectively.

```sh
docker compose up -d postgres
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m uvicorn stockpilot.main:app --reload
```

Inspect tables without reading conversation contents:

```sh
docker exec stockpilot-postgres psql -U stockpilot -d stockpilot \
  -c "SELECT table_schema, table_name FROM information_schema.tables WHERE table_schema = 'public' AND table_name LIKE 'checkpoint%' ORDER BY table_name;"
```

### Checkpoint validation

Manual restart test with a fresh unique thread and adequate demo stock:

```sh
curl -X POST http://localhost:8000/assistant -H "Authorization: Bearer $CUSTOMER_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"message":"Commande-moi 2 Bosch X100","thread_id":"persistent-order-1"}'
```

Stop the API with Ctrl+C and restart with the same DB configuration, then:

```sh
curl -X POST http://localhost:8000/assistant -H "Authorization: Bearer $CUSTOMER_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"message":"Oui, je confirme","thread_id":"persistent-order-1"}'
```

Confirmation uses the original product, quantity and operation ID and finalizes
the Mongo transaction. Do not reset either database volume between requests.

Unit tests inject MemorySaver via `build_graph(checkpointer=...)` or mock startup.
The opt-in PostgreSQL test closes graph A's connection, opens graph B's, restores
all conversation fields, checks thread isolation and confirms using a fake
finalizer without Mongo/Ollama:

```sh
# Required: POSTGRES_TEST_URI must name a dedicated test database such as the
# docker/compose.ci.yml service (database stockpilot_ci); there is no default.
.venv/bin/python -m pytest -q -m integration tests/test_postgres_checkpoint_integration.py
```

The test creates/drops only its own unique schema; the test role needs CREATE
permission on the database. The fixture refuses application endpoints, databases
not named `stockpilot_ci`/`stockpilot_test*`, and any database carrying a DEMO
`demo_instance` marker, before issuing CREATE or DROP. Application startup needs official saver migration
permissions. Coordinate concurrent first-start migrations operationally.
The saver owns one async connection per process; pooling, history retention/
cleanup, backups and access controls are separate work. Persistence alone does
not serialize concurrent same-thread requests or atomically commit with Mongo.
