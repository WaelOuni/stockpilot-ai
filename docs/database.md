# Databases and persistent conversations

[Back to README](../README.md) · [Getting started](getting-started.md) · [Order flow](order-flow.md)

All commands run from the project root in WSL/Linux. They are manual operational
procedures, not steps performed by the documentation refactor or real-eval CI.
Preserve existing volumes and private backups; never use `docker compose down -v`
to troubleshoot unknown data.

## Storage responsibilities

| Store | Contents |
|---|---|
| Mongo `stockpilot` | Products and atomically finalized orders |
| PostgreSQL `stockpilot` | Application RAG and default conversation checkpoints |
| PostgreSQL `stockpilot_eval` | Dedicated, repeatable product-document eval corpus |

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
# Optional: export POSTGRES_TEST_URI='postgresql://...'
.venv/bin/python -m pytest -q -m integration tests/test_postgres_checkpoint_integration.py
```

The test creates/drops only its own unique schema; the test role needs CREATE
permission on the database. Application startup needs official saver migration
permissions. Coordinate concurrent first-start migrations operationally.
The saver owns one async connection per process; pooling, history retention/
cleanup, backups and access controls are separate work. Persistence alone does
not serialize concurrent same-thread requests or atomically commit with Mongo.
