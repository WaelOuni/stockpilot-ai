# StockPilot AI

StockPilot is a local AI assistant for tool advice, stock lookup and multi-turn
orders. It combines structured intent extraction, product-document RAG and
explicit order confirmation. It is a development/demo backend, not a
production-ready commerce service.

## Quickstart (fresh demo environment)

Prerequisites: Python **3.12**, Docker Desktop with the Ubuntu WSL integration
enabled (or compatible Docker Engine/Compose), and local Ollama. Run the Python
commands from the project root in WSL. Model downloads need disk space and
Qwen inference needs adequate RAM; CPU inference can be slow.

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'

# Start Ollama in a separate terminal if it is not already running.
ollama serve
# In another terminal connected to that same Ollama instance:
ollama pull qwen2.5:7b
ollama pull nomic-embed-text
```

On Windows, Ollama can run on the Windows host. From WSL,
`source scripts/dev-env.sh` selects the host using its current gateway rather
than a hardcoded IP. Check connectivity with `curl "$OLLAMA_BASE_URL/api/tags"`.
Keep Ollama restricted to your trusted local environment.

```sh
cp .env.example .env
# Review .env first. Settings reads environment variables, not .env automatically.
set -a
source .env
set +a
# Only when Ollama runs on the Windows host:
source scripts/dev-env.sh

docker compose up -d --wait
```

Compose uses public **demo-only** PostgreSQL credentials. Mongo/PostgreSQL ports
are published beyond loopback in the current Compose file: use a trusted local
machine/firewall, never expose this stack to the Internet. Existing volumes are
preserved. Do not use `down -v` or the seed steps on valuable data.

### Initialize RAG on a fresh database

The following is for a **new demo database**; it does not drop tables/data.
Nomic embeddings use 768 dimensions. If changing the embedding model, check
`python scripts/check_embedding_dimension.py` and design a separate migration;
do not silently mix embedding models or alter an existing populated table.

```sh
docker compose exec -T postgres psql -v ON_ERROR_STOP=1 \
  -U stockpilot -d stockpilot < migrations/000_create_knowledge_chunks.sql

# Apply once to a newly created table (not an already migrated database).
docker compose exec -T postgres psql -v ON_ERROR_STOP=1 \
  -U stockpilot -d stockpilot < migrations/001_knowledge_chunks_source_unique.sql

python scripts/ingest_products.py
```

The uniqueness migration must not be reapplied blindly: inspect existing
constraints and duplicates before migrating an existing database. Ingestion
upserts the three public demo documents by source filename.

### Dedicated AI eval database

`stockpilot` is the application database (RAG and conversation checkpoints).
`stockpilot_eval` is a separate product-document corpus for repeatable AI evals;
it does not need application orders or checkpoints. Both use the existing
Compose PostgreSQL 16/pgvector service.

```sh
docker compose up -d postgres
python scripts/init_eval_db.py
```

The versioned initializer uses Compose `psql` as the local demo administrator.
It creates `stockpilot_eval` only if absent, applies
`migrations/000_create_knowledge_chunks.sql` (extension/table), then applies
`001_knowledge_chunks_source_unique.sql` only if its named constraint is absent.
It checks the final columns/dimension, primary key and existing source
constraint; incompatible schemas cause an explicit failure, not destructive
repair. 001 remains unchanged and is not independently repeatable. There is no
general migration-history framework; run migrations sequentially, not concurrently.
Neither database is dropped, and `stockpilot` is never modified by this script.

For a virgin eval database, the SQL operations are:

```sh
docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U stockpilot -d postgres \
  -c 'CREATE DATABASE stockpilot_eval OWNER stockpilot;'
docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U stockpilot -d stockpilot_eval \
  < migrations/000_create_knowledge_chunks.sql
docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U stockpilot -d stockpilot_eval \
  < migrations/001_knowledge_chunks_source_unique.sql
```

Prefer the initializer when the database may already exist. Populate it with
the existing Ollama environment and temporary database override:

```sh
# WSL + Windows-host Ollama, if applicable:
source scripts/dev-env.sh
export POSTGRES_URL='postgresql://stockpilot:stockpilot@localhost:5432/stockpilot_eval'
python scripts/ingest_products.py
# Repeat: updates the same three source rows instead of inserting duplicates.
python scripts/ingest_products.py
docker compose exec -T postgres psql -U stockpilot -d stockpilot_eval \
  -c 'SELECT id, source FROM knowledge_chunks ORDER BY id;'
python scripts/run_evals.py --mode local --suite historical
```

Use a separate shell, or restore/unset `POSTGRES_URL` afterwards so the API
continues to use `stockpilot`. Source upserts preserve existing row IDs but
sequence values may advance even on conflict; contiguous IDs are not promised.
The initializer does not remove stale corpus rows; the CI preflight checks exact
corpus contents and rejects extras.

GitHub environment secret **EVAL_POSTGRES_URL** can use the public local-demo
URL `postgresql://stockpilot:stockpilot@localhost:5432/stockpilot_eval` **only if
the self-hosted runner can reach this same PostgreSQL through localhost:5432**.
For a different host/container/network, configure its reachable address and
dedicated credentials instead. Ingestion/schema setup requires writes; the
evaluation runner should use a separately provisioned read-only account where
possible. Never use demo credentials in production.

### Seed Mongo and start the API

**`scripts/seed_products.py` is demo-only: it deletes all products in the local
`stockpilot.products` collection before inserting fixtures.** It uses the local
Mongo address directly, not `MONGO_URI`; do not run it against real inventory.
Seeding is optional when demo products already exist.

```sh
python scripts/seed_products.py
python -m uvicorn stockpilot.main:app --reload
```

Startup creates the order idempotency index and PostgreSQL checkpoint tables.
Mongo must be a healthy replica set. PostgreSQL startup errors are explicit;
there is no in-memory checkpoint fallback.

| Endpoint | Contract |
|---|---|
| `GET /health` | Process liveness, not dependency readiness |
| `POST /chat` | `{"message":"..."}`; direct LLM generation |
| `POST /classify` | `{"message":"..."}`; structured intent/extraction |
| `POST /assistant` | `{"message":"...","thread_id":"..."}`; stock/advice/ORDER workflow |

```sh
curl http://localhost:8000/health
curl -X POST http://localhost:8000/assistant \
  -H 'Content-Type: application/json' \
  -d '{"message":"Quel outil pour couper des panneaux de bois ?","thread_id":"demo-advice"}'
```

For ORDER, keep the same `thread_id` across preparation and explicit confirmation.
Confirmation may change demo stock and creates an order atomically in Mongo.
Repeated matching operation IDs are idempotent. RAG retrieves document chunks,
filters cosine distance and grounds structured product recommendations; stock
is checked separately.

### Architecture and validation

```text
src/stockpilot/
  domain/          entities, enums, deterministic services
  application/     DTOs, ports, use cases, LangGraph workflows
  infrastructure/  Ollama, configuration, Mongo/PostgreSQL adapters
  presentation/    FastAPI routes and request schemas
  main.py          composition root and resource lifespan
```

Stack: FastAPI/Pydantic, LangGraph, Ollama/Qwen, PostgreSQL/pgvector, Mongo replica
set, OpenTelemetry, Alloy/Loki, Tempo, Prometheus/Blackbox and Grafana.
Detailed transactions, checkpoints and observability are described below.

```sh
python -m pytest -q
python scripts/run_evals.py --mode deterministic --suite enriched
python scripts/run_evals.py --mode local --suite historical \
  --compare-baseline --tolerance 0 --output historical-results.json
python scripts/run_evals.py --mode local --suite enriched --output enriched-results.json
```

Historical 60-case baseline is human-reviewed; additional datasets are separate
validation, never an automatic reference promotion. Generated results, logs,
`.env`, IDE state and Mongo backups must stay outside Git.

To export traces/metrics, restart the API with `OTEL_ENABLED=true` and
`OTEL_EXPORTER=otlp`; the example config targets localhost port 4318.
Grafana is at localhost:3000, with provisioned overview/SLO dashboards.
Logs go to stdout and local rotating JSONL. Blackbox independently probes
`/health`. See the observability sections for local credentials and limitations.

PR CI runs mocked pytest plus deterministic eval-framework checks; pushes to
`main` and `dev` run the same jobs. `main` is the stable/demo branch and `dev`
is the development branch. Real evals are manual on an isolated self-hosted runner,
not a prerequisite dependent on local Ollama for every PR. After pushing,
configure `stockpilot-evals` environment: **dev only**, required independent
approval, secret `EVAL_POSTGRES_URL`, isolated runner, and a dedicated pre-ingested
eval DB/read-only account where possible. The workflow's repository/dev guard,
explicit dev checkout and disabled token persistence must remain enabled.

Current limits include probabilistic model extraction, small eval datasets,
lexical advice-domain checks, no authentication/payment/frontend, and
single-node demo databases. Dependency version ranges are not a full lockfile.
This stack is not hardened for production or public network access.

## Offline AI / RAG quality evaluations

Golden French datasets, objective extraction/retrieval/recommendation metrics,
local Ollama/pgvector runs and baseline regression checks are documented in
[evals/README.md](evals/README.md). Run `python scripts/run_evals.py --mode local`
for real quality measurement, or `--mode deterministic` for a fixture smoke run
only. These evaluations are separate from the fast, mocked pytest suite and do
not create orders, change stock or export business telemetry.

### GitHub Actions quality gates

`.github/workflows/ci.yml` runs on PRs and pushes to `main` and `dev`:
**Unit Tests** runs standard pytest except the eval framework tests;
**AI Eval Framework** runs those tests plus the enriched deterministic smoke
run. No test is duplicated between jobs, and neither needs Ollama/PostgreSQL.
The smoke run checks the framework, not AI quality. GitHub branch protection
must explicitly require these checks if merges should be blocked.

`.github/workflows/ai-evals.yml` is separate and **manual only**
(`workflow_dispatch`): **Real AI Quality Gate** runs the historical 60 cases
with `--mode local --compare-baseline --output eval-results.json`.
It uses a dedicated Linux x64 self-hosted runner with label `stockpilot-evals`,
Python 3.12, local Ollama with `qwen2.5:7b` and `nomic-embed-text:latest`, and an
already ingested PostgreSQL/pgvector eval database matching `data/products`
exactly. Database ingestion/model downloads are deliberately not performed by
the gate; never point it at production or a mutable live corpus.

After the first push, register the runner and configure the GitHub environment
**stockpilot-evals** with secret **EVAL_POSTGRES_URL** and optionally variable
**EVAL_OLLAMA_BASE_URL** (default `http://localhost:11434`). Use a read-only
database account. Apply environment reviewer/trusted-branch restrictions:
the manual job executes repository Python on self-hosted infrastructure and
must not run untrusted PR code. Provision models/corpus using the existing
ingestion procedure outside CI. No SaaS LLM is used.

To run: **Actions -> StockPilot Real AI Evals -> Run workflow**, select
`dev`, then approve the environment if required. The job only runs in
`WaelOuni/stockpilot-ai` on `refs/heads/dev`, explicitly checks out `dev`,
and does not persist Git checkout credentials. Configure the environment to
allow only `dev`, require independent approval, and protect workflow changes:
a YAML condition cannot prevent someone authorized to change the workflow
from removing that condition. There is no schedule
or PR trigger for real evals. Without the self-hosted runner the job queues;
it does not silently fall back to a synthetic or GitHub-hosted model run.
Hosted runners are not assumed to provide enough predictable CPU/RAM/time for
Qwen or repeated multi-GB model downloads.

Preflight checks connectivity, installed models, exact corpus content and
embedding/vector search compatibility. Failure is explicitly **environment
failure**, with no fabricated 0% quality score. Real runs fail for a regression
beyond the baseline tolerance (currently 3 percentage points), incompatible
configuration, incomplete evaluation or execution failure. Individual misses
without a regression do not by themselves fail the gate.

The JSON report is uploaded even on gate failure as
`stockpilot-ai-eval-results-<run_id>-<attempt>` (14-day retention).
The Step Summary includes metrics, regressions, dataset hashes, baseline
SHA256 and model names. Failed preflight reports contain metadata but no scores;
infrastructure failures before Python/report generation may leave no artifact.

**The CI never updates `evals/baseline.json`.** Baseline changes require human
review of model/corpus provenance, failures and measured scores in a separate
change. The current known product-extraction regression may legitimately make
the real gate fail; do not weaken or overwrite the reference to hide it.

Local equivalents:

```sh
python -m pytest -q
python scripts/run_evals.py --mode deterministic --suite enriched
# Explicit eval database URL; keep credentials out of shell history/logs.
python scripts/eval_ci.py preflight --output eval-results.json
python scripts/run_evals.py --mode local --compare-baseline --output eval-results.json
python scripts/eval_ci.py summary --report eval-results.json
```

The workflow YAML and helper behavior can be checked locally with
`python -m pytest -q tests/test_evals.py tests/test_eval_ci.py`.
GitHub dispatch, runner registration, environment permissions, artifact uploads
and actual Actions execution must be validated after the first GitHub push.

## External API availability (Blackbox Exporter)

HTTP traffic-based availability measures non-5xx requests among requests
actually observed by the API. If the process stops, no requests/metrics may be
exported, so that ratio becomes absent rather than detecting downtime.
The external Blackbox probe independently sends GET `/health` every 10 seconds,
without Ollama or user traffic.

Target: **`http://host.docker.internal:8000/health`**. This hostname resolves
and reaches the current loopback-bound WSL Uvicorn from Docker Desktop in the
verified local environment; no dynamic WSL IP or `extra_hosts` is needed.
On other installations, first check the container-to-host path:

```sh
docker compose exec -T prometheus wget -S -O - -T 5 \
  http://host.docker.internal:8000/health
```

If the hostname is missing on native Docker Engine, an `extra_hosts` entry
`host.docker.internal:host-gateway` on `blackbox-exporter` may be necessary.
It fixes DNS, not WSL forwarding/firewall issues. On a host without Docker
Desktop loopback forwarding, bind Uvicorn to a reachable interface (for example
`--host 0.0.0.0`) only after considering network exposure/firewall restrictions.
Do not hardcode an ephemeral WSL address.

```sh
# From the project directory in WSL:
docker compose up -d blackbox-exporter prometheus grafana
# Compose may leave an already-running Prometheus unchanged:
docker compose restart prometheus

# In the API's terminal (keep its usual environment settings):
.venv/bin/python -m uvicorn stockpilot.main:app --reload
```

Exporter version is pinned to `prom/blackbox-exporter:v0.26.0`.
`docker/blackbox.yml` configures IPv4 GET, a 5-second timeout and no redirects:
only direct 2xx responses succeed. Connection failure, timeout and non-2xx
(including 5xx) produce `probe_success = 0`. Prometheus scrapes `/probe` on
`blackbox-exporter:9115` with an 8-second scrape timeout. Neither service
publishes a new host port. No Alertmanager or notifications are configured.

Query internally (Prometheus is not host-exposed):

```sh
docker compose exec -T prometheus wget -qO- \
  'http://localhost:9090/api/v1/query?query=probe_success%7Bjob%3D%22stockpilot-blackbox%22%7D'
docker compose exec -T prometheus wget -qO- \
  'http://localhost:9090/api/v1/rules?type=alert'
docker compose exec -T prometheus wget -qO- \
  'http://localhost:9090/api/v1/alerts'
```

Alternatively use Grafana **Explore -> Prometheus** with
`probe_success{job="stockpilot-blackbox"}` or open
**Dashboards -> StockPilot -> StockPilot SLO**. New stat panels show
**StockPilot Reachability** (`UP`/`DOWN`, absent = `UNKNOWN`) and probe duration.
Prometheus alert state is queryable in Grafana Explore via
`ALERTS{alertname=~"StockPilotUnavailable|BlackboxProbeMissing"}`.
These are Prometheus rules, not duplicated Grafana-managed alerts.

| Alert | Expression | Hold | Severity |
|---|---|---|---|
| `StockPilotUnavailable` | `probe_success{job="stockpilot-blackbox"} == 0` | 1m | critical |
| `BlackboxProbeMissing` | `absent(probe_success{job="stockpilot-blackbox"})` | 2m | warning |

Manual UP/DOWN/recovery test:

1. Start the API and verify `probe_success = 1`; both alerts should be inactive.
2. Stop Uvicorn using Ctrl+C in its terminal, including the reload supervisor.
3. Verify `probe_success = 0`. After one continuous minute plus scrape/evaluation
   delay (10-second intervals), `StockPilotUnavailable` becomes firing.
4. Restart the same Uvicorn command with the same environment. Verify
   `probe_success = 1` and the alert resolves on the next evaluation.

To test missing monitoring independently, stop only the exporter with
`docker compose stop blackbox-exporter`. The failed scrape removes the probe
series; after 2 minutes the missing-probe alert fires. Restart it with
`docker compose up -d blackbox-exporter`. This condition is not proof of API
downtime. A missing probe can briefly be pending during initial startup.

Validate without stopping the API or launching services in pytest:

```sh
.venv/bin/python -m pytest -q tests/test_slo_configuration.py tests/test_health.py
docker compose config --quiet
docker compose run --rm --no-deps --entrypoint promtool prometheus \
  check config /etc/prometheus/prometheus.yml
docker compose run --rm --no-deps -v "$PWD/docker:/rules:ro" \
  --entrypoint promtool prometheus test rules /rules/prometheus-rules-tests.yml
```

The rule fixtures cover UP, sustained DOWN, resolution and a missing series
with their hold periods. `/health` is a liveness endpoint returning
`{"status":"UP"}`; it does not test Mongo/PostgreSQL/Ollama readiness or validate
the body in Blackbox. This probe observes the Docker-to-host network path,
not public Internet availability. If Prometheus itself is down, neither alert
can evaluate; monitoring that requires a separate monitoring system.

MongoDB runs as a single-node replica set (`rs0`) because multi-document
transactions require a replica set. This is a local development configuration,
not a high-availability deployment. PostgreSQL, RAG and Ollama are unchanged.

## Start or migrate the local environment (WSL)

Run these commands from the project directory. Before converting an existing
standalone instance, back up its data while the old container is still running:

```sh
docker exec stockpilot-mongodb mongodump --archive=/tmp/stockpilot-before-rs.archive
docker cp stockpilot-mongodb:/tmp/stockpilot-before-rs.archive ./stockpilot-before-rs.archive
```

The archive contains application data: keep it private and outside version control.
Then recreate **only the container**, keeping its named volume:

```sh
docker compose up -d --force-recreate --wait mongodb
docker exec stockpilot-mongodb mongosh --quiet --eval 'rs.status()'
docker exec stockpilot-mongodb mongosh --quiet --eval 'db.hello().isWritablePrimary'
```

For a new environment, use `docker compose up -d --wait`. The Mongo healthcheck
runs `docker/init-replica-set.js`, initializes `rs0` only when not initialized,
and waits for PRIMARY. Existing products and orders are not deleted or backfilled.
It does not reconfigure an unexpected existing replica set.

If startup fails, preserve the volume and inspect the actual error:

```sh
docker compose logs mongodb
docker inspect stockpilot-mongodb --format '{{json .State.Health}}'
docker exec stockpilot-mongodb mongosh --quiet --file /opt/stockpilot/init-replica-set.js
```

Do not run `docker compose down -v` or delete the volume. A normal standalone
data directory can be reused with `--replSet`; failure does not imply it needs
erasing. If the data directory/configuration cannot be reused, keep the original
volume and the verified backup, obtain approval before provisioning a separate
volume, and restore the archive into the new instance with `mongorestore`.
There is no automatic destructive migration.

If a separate clean recovery instance is necessary, these commands are an
**explicit manual alternative**, not required for the normal migration. They
leave the original container/volume untouched and use a different port:

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

Wait for PRIMARY (rerun the initialization/status commands if election is still
in progress). Then point `MONGO_URI` at port `27018`, keeping `replicaSet=rs0` and
`directConnection=true`. Inspect the recovered data before any decision about
the original volume. Do not restore into an already populated recovery database
or use `--drop`.

## WSL connection and Docker discovery

The replica set advertises `mongodb:27017`, resolvable on the Compose network.
The application runs outside Docker in WSL, so its default URI is:

```text
mongodb://localhost:27017/?replicaSet=rs0&directConnection=true
```

`directConnection=true` targets the Docker-published port directly rather than
discovering an unresolvable Docker hostname. This supports sessions/transactions
on the single primary. The URI remains overrideable via `MONGO_URI`. If localhost
forwarding is unavailable in your WSL/Docker setup, replace only `localhost` with
the reachable Docker-host address, keeping both query parameters.
Clients inside Compose can use
`mongodb://mongodb:27017/?replicaSet=rs0`.
`docker exec ... mongosh` still connects directly to localhost inside the Mongo
container. Do not use this direct-connection topology as a production failover
configuration.

Start the application after Mongo is healthy:

```sh
export MONGO_URI='mongodb://localhost:27017/?replicaSet=rs0&directConnection=true'
.venv/bin/python -m uvicorn stockpilot.main:app --reload
```

FastAPI lifespan initializes the unique partial `orders_operation_id_unique`
index once at startup. The partial filter
`{"operation_id": {"$type": "string"}}` excludes historical orders without an
operation identifier. Duplicate string identifiers cause startup to fail and
require manual reconciliation. No historical order is removed.

## Atomicity and idempotency

Each valid preparation generates an `operation_id` UUID, saved in `pending_order`
and reused on confirmation. `operation_id` identifies the business operation;
`order_id` identifies the persisted order.

`FinalizeOrderUseCase` constructs the order and delegates to `OrderTransactionPort`.
The infrastructure adapter owns all PyMongo sessions. The domain and application
do not import PyMongo.

Confirmation checks for an existing matching operation before starting a session.
For a new operation, `ClientSession.with_transaction()`:

1. Checks idempotency again inside each transaction attempt.
2. Reserves using a single conditional `find_one_and_update`: `stock >= quantity`
   and `$inc: {"stock": -quantity}`.
3. Inserts the order with the same session.
4. Commits both writes together with majority write concern.

Insufficient stock aborts without an order. Insertion failure rolls back the
decrement. A unique-key loser aborts before retrieving the winning order.
A repeated matching operation returns the original order without another
decrement; different product/quantity raises `IdempotencyConflictError`.

**Atomicity** prevents partial stock/order writes. **Idempotency** prevents a
replayed business operation from becoming a second order. Both are necessary.
Concurrent same-operation transactions are protected by write-conflict retries,
the in-transaction lookup, and the unique index.

PyMongo retries `TransientTransactionError` and `UnknownTransactionCommitResult`
using its recommended `with_transaction()` implementation. No application-level
transaction retry loop is added. If retries end with an uncertain commit,
the adapter reconciles by `operation_id`: an existing matching order is success;
an unresolved outcome returns `ORDER_OUTCOME_UNKNOWN`, not a claim of rollback.
Manual reconciliation is then necessary. No stock compensation is performed.

`ReleaseStockUseCase`, `ReserveStockUseCase`, and standalone `CreateOrderUseCase`
are retained as independently tested utilities, but are not wired into ORDER
confirmation. There is **no nontransactional fallback**. `COMPENSATION_FAILED`
is retained for compatibility but no longer emitted by this flow.

## Tests

The standard suite uses fakes/mocks and never contacts Mongo:

```sh
.venv/bin/python -m pytest -q
```

Optional replica-set tests are excluded by default. After starting Mongo:

```sh
export MONGO_TEST_URI='mongodb://localhost:27017/?replicaSet=rs0&directConnection=true'
.venv/bin/python -m pytest -q -m integration
```

Integration tests use a unique temporary database, validate commit, rollback and
concurrent confirmations, then drop **only their own generated database**.
They never modify `stockpilot` or historical data.

## Persistent LangGraph conversations

Application startup now uses the official `langgraph-checkpoint-postgres==3.1.2`
`AsyncPostgresSaver`, compatible with the installed LangGraph 1.2.14 and
langgraph-checkpoint 4.2.0. The graph is compiled inside FastAPI lifespan, not at
module import. Startup opens the checkpoint connection, calls the official
idempotent `setup()` migrations once, and logs initialization without conversation
contents or database credentials. Shutdown closes the connection. PostgreSQL
connection/setup failures fail startup: there is no silent MemorySaver fallback.

By default checkpoints use `POSTGRES_URL`, the same PostgreSQL database as RAG,
but a separate connection owned by the saver. Set `LANGGRAPH_CHECKPOINT_DB_URI`
to override only checkpoint storage. Both accept normal psycopg PostgreSQL URIs.
The RAG repository and its `knowledge_chunks` table are unchanged.

The official saver creates these internal tables in the default `public` schema:
`checkpoint_migrations`, `checkpoints`, `checkpoint_blobs`, `checkpoint_writes`.
They are not RAG/business tables; do not modify their structure manually.
The serializer explicitly allows the application's Intent and ProductCategory
enums, alongside standard supported checkpoint types; pickle fallback is disabled.

The HTTP contract remains `{ "message": "...", "thread_id": "..." }`. Keep the
same `thread_id` to resume a conversation after restart or through another API
instance using the same checkpoint database. Different identifiers remain
isolated. The saved `pending_order`, quantity and `operation_id` are restored;
confirmation never regenerates the operation identifier. In-memory conversations
from before this change cannot be recovered retrospectively.

Start PostgreSQL and the API (Mongo must already be configured as above):

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

Manual restart test, with a fresh unique thread identifier:

```sh
curl -X POST http://localhost:8000/assistant \
  -H 'Content-Type: application/json' \
  -d '{"message":"Commande-moi 2 Bosch X100","thread_id":"persistent-order-1"}'
```

With adequate stock, the answer requests confirmation. Stop the API with Ctrl-C
and relaunch it using the same database configuration. Then:

```sh
curl -X POST http://localhost:8000/assistant \
  -H 'Content-Type: application/json' \
  -d '{"message":"Oui, je confirme","thread_id":"persistent-order-1"}'
```

The confirmation must use the original product, quantity and operation identifier
and finalize the Mongo order transaction. Do not reset Mongo/PostgreSQL volumes
between requests.

Unit tests still inject MemorySaver directly through `build_graph(checkpointer=...)`
or mock the startup context; PostgreSQL is not required by the standard suite.
The opt-in PostgreSQL test closes graph A's connection, opens graph B's connection,
restores every conversation field, verifies thread isolation and confirms through
a fake finalizer without Mongo/Ollama:

```sh
# Optional: export POSTGRES_TEST_URI='postgresql://...'
.venv/bin/python -m pytest -q -m integration tests/test_postgres_checkpoint_integration.py
```

The test creates and drops only its own uniquely named temporary schema. Its role
requires CREATE permission on the test database. Production startup needs the
permissions necessary for the official saver migrations.

## Observability

Application logs use standard-library logging and JSON lines, with UTC timestamp,
level, logger, event and contextual fields. No telemetry service is required.
Set `LOG_LEVEL=DEBUG` (default `INFO`) before starting the API to change verbosity.
The configuration applies to the `stockpilot` logger; Uvicorn/third-party log
configuration remains separate.

Correlation identifiers have distinct roles:

- `request_id`: UUID generated for each HTTP request, returned in `X-Request-ID`.
- `thread_id`: conversation identifier supplied to `/assistant`.
- `operation_id`: stable logical order operation, including across restarts.
- `order_id`: persisted Mongo order identifier.

Async context uses ContextVar, reset at the HTTP boundary. Events include HTTP
and assistant start/completion/failure, detected intent, LLM classification and
generation timing, RAG search result count/distance, stock checks, order
confirmation/replay/transaction/finalization, conflicts and unknown outcomes.
Checkpoint logs cover initialization, startup failure and closure only.
Durations use `perf_counter()` and are expressed in milliseconds.

Example (illustrative identifiers):

```json
{"timestamp":"2026-10-09T21:40:00+00:00","level":"INFO","logger":"stockpilot.application.workflows.nodes","event":"order_finalized","request_id":"request-uuid","thread_id":"thread-1","operation_id":"operation-uuid","order_id":"order-uuid","product_name":"Bosch X100","quantity":2,"order_status":"CONFIRMED","duration_ms":143.2}
```

Bodies, user messages, prompts, LLM responses and RAG chunks are not logged.
Application JSON stacktraces include exception type and frame location/function,
not exception text, source lines or local values that could contain sensitive data.
Unexpected HTTP failures return a generic 500 body and the request identifier.
Product names and correlation identifiers are retained for diagnostics; treat
logs as application data. Retention, redaction policy and forwarding to Loki,
Datadog or ELK are not configured here. Third-party loggers require their own
privacy review/configuration.

## OpenTelemetry

Set `OTEL_ENABLED=true` before starting the API to enable tracing. It is disabled
by default, including for standard unit tests. `OTEL_SERVICE_NAME` defaults to
`stockpilot-ai`; `OTEL_EXPORTER=console` prints spans locally and `otlp` sends
HTTP/protobuf to the optional local tracing stack. No external service is required
when tracing is disabled or console export is selected.

```sh
export OTEL_ENABLED=true
export OTEL_SERVICE_NAME=stockpilot-ai
export OTEL_EXPORTER=console
.venv/bin/python -m uvicorn stockpilot.main:app --reload
```

A single process-local provider is initialized before official FastAPI
instrumentation builds the ASGI stack. It exports with a batch processor, flushes
at lifespan shutdown and shuts down at process exit. Restart the process to change
tracing configuration. Resource attributes include service name and version.

FastAPI creates the server span (`POST /assistant`). Ollama httpx clients are
instrumented explicitly, without globally instrumenting unrelated clients.
Manual spans cover `stockpilot.llm.classify`, `stockpilot.llm.generate`,
`stockpilot.rag.search`, `stockpilot.stock.check`, `stockpilot.order.prepare`,
`stockpilot.order.finalize` and `stockpilot.mongo.order_transaction`.
No Mongo/Psycopg auto-instrumentation or internal-checkpoint spans are enabled.

Illustrative hierarchy:

```text
POST /assistant                         trace_id=<32 hex characters>
  stockpilot.order.finalize             operation_id=..., quantity=2
    stockpilot.mongo.order_transaction
```

The server span contains `stockpilot.request_id`, `stockpilot.thread_id`,
and operation/order identifiers when known. Finalization adds product, quantity
and final order status; LLM spans add model and detected intent; RAG adds result
count and top distance. `request_id` identifies an application HTTP request;
`trace_id` is generated by OpenTelemetry (or propagated from the caller) and
identifies the distributed trace. Neither thread nor operation ID replaces it.
Structured log events emitted with an active span include standard hex `trace_id`
(32 characters) and `span_id` (16 characters).

No bodies/prompts/responses/chunks are attached. Application exception events are
redacted, and the console export boundary removes exception text/stacktraces,
status descriptions and URL credentials/query parameters, including from automatic
instrumentation. Product names and business IDs remain trace data and need an
appropriate retention/access policy. Do not enable optional HTTP header capture
for sensitive headers. URL paths should never contain credentials.

Tests use an official in-memory exporter and provider; no collector is required:

```sh
.venv/bin/python -m pytest -q tests/test_telemetry.py
```

Sampling policy and production deployment are outside this change.
Synchronous PyMongo continues to block the async event
loop; tracing measures that latency but does not change execution.

## Local tracing stack

```text
StockPilot API (WSL)
  -> HTTP/protobuf localhost:4318/v1/traces
  -> OpenTelemetry Collector
  -> OTLP gRPC tempo:4317 (Docker network)
  -> Tempo local storage
  <- Grafana Tempo datasource
```

Pinned development images: Collector 0.123.0, Tempo 2.7.2, Grafana 11.6.0.
The application uses `opentelemetry-exporter-otlp-proto-http==1.45.1`, matching
its existing API/SDK. It does not install the Python gRPC exporter.

From WSL, start only the tracing services:

```sh
docker compose up -d otel-collector tempo grafana
docker compose ps otel-collector tempo grafana
docker compose logs --tail=30 otel-collector tempo grafana
```

Only Collector HTTP `127.0.0.1:4318` and Grafana `127.0.0.1:3000` are published.
Collector gRPC, its health endpoint 13133, and Tempo HTTP 3200/OTLP ports are
internal. The existing Compose network resolves `tempo` for Collector and Grafana.
Grafana has a healthcheck using its installed wget. Collector/Tempo images do not
provide a reliable in-image curl/wget probe, so no shell-based Docker healthcheck
is invented for them. `depends_on` orders startup, not full readiness; allow
startup/election time and check readiness explicitly:

```sh
docker compose exec -T grafana wget -qO- http://tempo:3200/ready
docker compose exec -T grafana wget -qO- http://otel-collector:13133/
```

Then start StockPilot in WSL, not inside the Compose network:

```sh
export OTEL_ENABLED=true
export OTEL_EXPORTER=otlp
export OTEL_SERVICE_NAME=stockpilot-ai
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
.venv/bin/python -m uvicorn stockpilot.main:app --reload
```

The endpoint setting is a **base URL**; the application appends `/v1/traces`.
Use localhost, not `otel-collector`, from WSL. If Docker-host forwarding differs
on your machine, use a host address reachable from WSL. Keep localhost binding
unless you intentionally change your security boundary.
`OTEL_ENABLED=false` disables tracing irrespective of exporter selection.
`OTEL_EXPORTER=console` remains available and is the default. Changing exporter
or endpoint requires restarting the API process. Both exporters use the same
privacy filter and BatchSpanProcessor. No prompts, messages, chunks or responses
are sent to Collector/Tempo.

Send a request (requires the normal Mongo/PostgreSQL/Ollama services for this
assistant flow), or validate OTLP alone with the synthetic script:

```sh
curl -X POST http://localhost:8000/assistant \
  -H 'Content-Type: application/json' \
  -d '{"message":"Est-ce que Bosch X100 est disponible ?","thread_id":"tracing-demo-1"}'
.venv/bin/python scripts/check_otlp.py
```

The script exports one synthetic span and fails if the Collector does not
acknowledge it. It prints the trace ID for lookup. Collector acceptance is not
itself proof of downstream storage. Batch export/search visibility can take a
few seconds; check Collector logs if an export fails.

Open **http://localhost:3000**, log in with local initial credentials
**admin / admin**, and follow Grafana's initial password-change prompt.
The Tempo datasource is provisioned automatically with UID `stockpilot-tempo`,
using `http://tempo:3200`. In **Explore -> Tempo**, use the TraceQL query editor:

```traceql
{ resource.service.name = "stockpilot-ai" }
{ span.stockpilot.thread_id = "tracing-demo-1" }
{ span.stockpilot.operation_id = "your-operation-id" }
```

Select a recent time range and run the query. Alternatively select trace-ID lookup
and paste the 32-character hex `trace_id` from application logs or the script.
Inspect the server span and its LLM/RAG/ORDER children. The optional Loki
datasource is described below; no metrics generator is configured.

Tempo uses a named `tempo_data` volume for WAL/blocks and 24-hour retention.
Grafana uses `grafana_data` for its database/settings. To stop only this stack:

```sh
docker compose stop otel-collector tempo grafana
```

Do not use `down -v`; stopping/recreating containers preserves the named volumes.
This is a local, unauthenticated OTLP pipeline without TLS or high availability.
Grafana has authentication but development initial credentials; its password
persists in the volume after change. Loopback-published ports are not suitable
for Internet exposure. Review image updates, authentication, TLS, backups,
retention and sampling before any production deployment. Collector export queues
are memory-backed, so an outage/restart can lose unexported traces; trace export
does not guarantee durable delivery and must not gate business operations.

## Local logs stack

```text
StockPilot in WSL -> stdout JSON + logs/stockpilot.jsonl
  -> read-only Docker bind mount -> Grafana Alloy -> Loki -> Grafana
                                                       <-> Tempo via trace_id
```

Grafana Alloy v1.8.3 is used instead of Promtail. Loki 3.4.2 uses local TSDB/
filesystem storage with a named `loki_data` volume and seven-day retention.
Alloy positions survive container recreation in `alloy_data`. No Loki or Alloy
port is published; Grafana remains loopback-only on port 3000.

The application logs to stdout and, by default, `logs/stockpilot.jsonl` relative
to the working directory. Run Uvicorn from the project root so the file matches
the Compose mount. `LOG_FILE` overrides the destination; `LOG_FILE=''` disables
file logging. If using a different path, adjust the bind mount too.
The stdlib RotatingFileHandler keeps a 10 MiB active file plus three backups,
approximately 40 MiB total. All records are original JSON objects, one per line.
Real logs/backups are ignored by Git; only `logs/.gitkeep` is tracked.
Logging configuration is idempotent, and file-open failures surface at startup.

```sh
docker compose up -d loki alloy tempo otel-collector grafana
export OTEL_ENABLED=true
export OTEL_EXPORTER=otlp
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
.venv/bin/python -m uvicorn stockpilot.main:app --reload
```

Generate an assistant request using a fresh thread or emit a synthetic log/span:

```sh
.venv/bin/python scripts/check_logs.py
docker compose logs --tail=30 alloy loki
docker compose exec -T grafana wget -qO- http://loki:3100/ready
```

Allow startup and ingestion a few seconds. Grafana datasource provisioning is
applied at startup; rerun `docker compose up -d --force-recreate grafana` after
changing provisioning files. Neither Loki nor Alloy depends on an invented
curl-based healthcheck; their startup/readiness can be checked through logs and
the Loki endpoint above using Grafana's installed wget.

Open http://localhost:3000, then **Explore -> Loki**, select a recent time range.
The labels are only `service="stockpilot-ai"`, `environment="local"` and `level`.
Filename is dropped. Loki also derives a low-cardinality `service_name` label
from `service`. Logger/event and all high-cardinality identifiers stay in
the original JSON line, not in indexed stream labels. Examples:

```logql
{service="stockpilot-ai"}
{service="stockpilot-ai", level="ERROR"}
{service="stockpilot-ai"} | json | operation_id="your-operation-id"
{service="stockpilot-ai"} | json | thread_id="your-thread-id"
{service="stockpilot-ai"} | json | trace_id="your-32-character-trace-id"
{service="stockpilot-ai"} | json | event="logs_pipeline_check"
```

Critical errors have `level="CRITICAL"` rather than `ERROR`; include both if
needed using `{service="stockpilot-ai", level=~"ERROR|CRITICAL"}`.

**Logs -> trace:** expand a log line containing `trace_id`; its derived field
offers **View Trace**, an internal link to the provisioned Tempo datasource.
The regexp matches the JSON's 32-character lowercase hex trace ID.

**Trace -> logs:** select a span in Tempo and use the logs link. The provisioned
`tracesToLogsV2` maps resource `service.name` to Loki's `service` and filters
the parsed JSON by the span's trace ID, with a +/- one-minute window.
Requests without active tracing have no trace link. Retention/sampling or exporter
failures may leave logs without a stored trace. IDs are deliberately not labels:
each new request would otherwise create a new Loki stream and excessive cardinality.

No new sensitive payloads are collected: the existing JSON privacy policy remains.
Logs contain product names and business identifiers and must be treated as private.
Uvicorn/third-party logs are not written into this application JSON file.
Alloy reads the active `*.jsonl` files; rotated `.jsonl.1` backups are not rediscovered
and duplicated. Its tailer follows normal rename/create rotation, but this is not
an exactly-once durable pipeline. Long outages plus multiple rotations can lose
unread logs; replay after position loss can duplicate records.
RotatingFileHandler is not multi-process safe: this local setup targets one API
worker. Avoid simultaneous processes writing/rotating the same file; for multiple
workers use separate files or an appropriate centralized logging strategy.
Alloy's service label is fixed to stockpilot-ai; update it and trace service.name
together if renaming the service. This is dev-only, without Loki TLS/auth.

Stop services without deleting volumes or local logs:

```sh
docker compose stop alloy loki otel-collector tempo grafana
```

## Metrics

Logs describe detailed events; traces describe a request's path; metrics describe
aggregated trends. StockPilot uses only OpenTelemetry metrics:

```text
WSL API -> OTLP HTTP /v1/metrics -> Collector contrib -> :8889
  <- Prometheus scrape (Docker network) <- Grafana
```

No new Python package is needed: the installed HTTP/protobuf exporter includes
OTLPMetricExporter. One MeterProvider shares the tracing resource and exports
every 10 seconds (`OTEL_METRIC_EXPORT_INTERVAL_MS`, positive integer).
Console mode exports metrics locally; disabled mode records nothing.
Prometheus and Collector metrics port 8889 are not published. Prometheus keeps
seven days in its named `prometheus_data` volume.

The Collector uses contrib 0.123.0 rather than core 0.123.0 because the Prometheus
exporter is not in the core image. Its metrics pipeline is OTLP -> batch ->
Prometheus exporter. Existing traces/logs pipelines are unchanged.

Durations are consistently **seconds**, unlike existing log `duration_ms`.
Counter names are exported with `_total`; duration histograms with
`_seconds_bucket`, `_seconds_sum`, `_seconds_count`.

| OTel instrument | Attributes |
| --- | --- |
| stockpilot.http.requests / http.request.duration | http.request.method, http.route, http.response.status_code |
| stockpilot.llm.calls / llm.duration / llm.errors | model, operation=classify/generate, outcome=success/error |
| stockpilot.rag.searches / rag.duration | outcome=results/empty/error |
| stockpilot.rag.empty_results | outcome=empty |
| stockpilot.stock.checks | outcome=available/unavailable/not_found |
| stockpilot.orders.prepared | outcome=pending_confirmation/insufficient_stock/invalid |
| stockpilot.orders.finalized | outcome=confirmed/replay |
| stockpilot.orders.failed | reason=reservation_failed/creation_failed/outcome_unknown/idempotency_conflict |
| stockpilot.mongo.order_transactions / mongo.order_transaction.duration | outcome=committed/aborted/error |

RAG empty means no document passed the existing relevance filter, not merely
zero raw database results. Transaction metrics aggregate driver retries into one
adapter call and exclude precheck replays. Finalization counters count successful
attempts, distinguishing recovered replays, not unique orders globally.
Prepared `invalid` includes missing/unknown products or invalid quantity.
No business IDs, trace IDs, product names, prompts or queries are metric
attributes. A shared attribute whitelist rejects accidental additions.
HTTP routes use templates or `unmatched`, never raw paths. Official FastAPI/httpx
metrics are disabled to avoid duplicate HTTP measurements and uncontrolled URL
attributes; their trace instrumentation remains active.

```sh
docker compose up -d loki alloy tempo otel-collector prometheus grafana
export OTEL_ENABLED=true
export OTEL_EXPORTER=otlp
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
.venv/bin/python -m uvicorn stockpilot.main:app --reload
```

In http://localhost:3000 choose **Dashboards -> StockPilot -> StockPilot Overview**.
Its nine panels show HTTP rate/p95/5xx ratio, LLM rate/p95, ORDER finalizations/
failures, Mongo p95, and RAG empty ratio. Prometheus is provisioned automatically.
The dashboard and datasource are applied when Grafana is created/restarted.

To generate synthetic data without Mongo/Ollama:

```sh
OTEL_ENABLED=true OTEL_EXPORTER=otlp .venv/bin/python scripts/check_metrics.py
```

This creates explicitly synthetic series, including model `synthetic`, and should
only be run in dev. Allow export (10s), Collector batching and scrape (10s) delay.
Rate/p95 panels require at least two scrape samples and ongoing traffic; empty
panels on an idle system do not imply failure.

Verified PromQL names:

```promql
sum(rate(stockpilot_http_requests_total[5m]))
histogram_quantile(0.95, sum by (le) (rate(stockpilot_http_request_duration_seconds_bucket[5m])))
sum(rate(stockpilot_llm_errors_total[5m]))
sum by (outcome) (rate(stockpilot_orders_finalized_total[5m]))
sum by (reason) (rate(stockpilot_orders_failed_total[5m]))
```

Prometheus normalizes attribute dots into underscores, e.g.
`http_response_status_code`. Scrape job/instance and OTel resource job/instance
labels may be added by the exporter; they identify service/process, not requests.
Counters reset on API restart. No alerting, Alertmanager, notifications or SLOs
are configured. Collection is best-effort, not business accounting.
HTTP duration is measured to response headers, not completion of streaming bodies.
Histograms use SDK default boundaries; tune them only after observing workloads.

## SLI/SLO and local alerts

**SLI** is a measurement, **SLO** its target, and **alert** a signal of sustained
deviation. These are initial dev/demo objectives, not monthly contractual SLOs.
Each SLI uses a rolling five-minute `rate()` window, aggregated across API
instances for `exported_job="stockpilot-ai"`:

| Recording rule | Definition | Target |
| --- | --- | --- |
| stockpilot:sli:assistant_availability:5m | non-5xx / all `/assistant` requests | >=99% |
| stockpilot:sli:assistant_p95_latency:5m | p95 HTTP histogram, `/assistant` only | <2 seconds |
| stockpilot:sli:order_success_rate:5m | finalized / (finalized + failed) | >=95% |
| stockpilot:sli:llm_success_rate:5m | 1 - errors / calls | >=95% |
| stockpilot:sli:mongo_transaction_success_rate:5m | committed / all transaction outcomes | >=99% |

Exact executable PromQL is in `docker/prometheus-rules.yml`. It uses exported
labels `http_route`, `http_response_status_code`, `outcome`, and the counter/
histogram names verified from Prometheus. Missing error series are zero; missing
success series are zero when failures exist. Each result is gated by a positive
denominator: no traffic yields **no series**, not NaN/Inf or a fabricated 100%.
Latency likewise requires a positive histogram observation rate.

ORDER includes replay successes and expected reservation refusals as currently
instrumented. Mongo counts adapter-level transaction calls, not individual retry
attempts; aborted stock refusals lower its success ratio. Availability treats
4xx and HTTP 200 business refusals as available. These measure different concepts
and should be recalibrated/split after observing realistic workloads.

The provisioned **StockPilot SLO** dashboard contains five current-value stat
panels, showing the target in each title: green=OK, orange=warning/breach,
and `No traffic` when absent. The sixth panel shows availability error rate
against a **1% allowance**. It is educational: no monthly remaining budget or
multi-window burn rate is claimed. The latency threshold is inclusive (`>=2`)
because exactly 2 seconds already violates the `<2` target.

Prometheus evaluates recording/alert rules every ten seconds. Alerts become
pending when breached and firing after five continuous minutes:
`AssistantHighErrorRate`, `AssistantHighLatency`, `OrderFailureRateHigh`,
`LLMErrorRateHigh`, `MongoTransactionFailure`. They have warning severity.
No Alertmanager or notifications are installed. Absence of traffic, metrics,
or a stopped API does **not** trigger these ratio alerts; a separate liveness/
telemetry-missing alert would be needed in a later step.

```sh
docker compose up -d --force-recreate prometheus grafana
for i in {1..10}; do
  curl -s -X POST http://localhost:8000/assistant \
    -H 'Content-Type: application/json' \
    -d '{"message":"Est-ce que Bosch X100 est disponible ?","thread_id":"slo-demo"}'
done
```

Grafana: **Dashboards -> StockPilot -> StockPilot SLO**. Allow export/scrape/rule
evaluation time. The LLM-dependent assistant can naturally exceed two seconds;
the initial target is deliberately provisional.

Prometheus remains unexposed. Inspect its APIs using Grafana's installed wget
over the Docker network:

```sh
docker compose exec -T grafana wget -qO- http://prometheus:9090/api/v1/rules
docker compose exec -T grafana wget -qO- http://prometheus:9090/api/v1/alerts
```

Alternatively query `ALERTS{alertstate="firing"}` in Grafana Explore/Prometheus.
The evaluated rules are visible in Prometheus's API; no separate Grafana-managed
alert rules are provisioned.

To test alert behavior **without disrupting Ollama/Mongo or injecting false
metrics into the live store**, use the synthetic promtool fixture:

```sh
docker compose run --rm --no-deps --entrypoint promtool prometheus \
  check config /etc/prometheus/prometheus.yml
docker run --rm --entrypoint promtool \
  -v "$PWD/docker:/rules:ro" -w /rules prom/prometheus:v3.2.1 \
  test rules prometheus-rules-tests.yml
```

It simulates no traffic, healthy traffic without error series and sustained
breaches for all five alerts, checking the five-minute hold period.
For a live latency alert, generate ongoing requests for at least five minutes
whose observed p95 exceeds 2 seconds, then inspect the alerts API. Do not stop
dependencies or alter business data merely to demonstrate alerts.

No instrumentation or high-cardinality metric attributes are added in this step.
SLOs depend on best-effort exported metrics, scrape availability, counter resets
and histogram approximation. Low traffic can cause noisy ratios; no minimum
sample-volume policy is imposed yet.

## Remaining limitations

PyMongo calls are synchronous in an async flow and can block the event loop.
Checkpoint state is now durable and accessible across API instances, but persistence
alone does not serialize simultaneous requests for the same thread. Operation
idempotency and Mongo transactions still protect order finalization. PostgreSQL
checkpoints and Mongo orders are not part of a shared distributed transaction.
The saver owns one async connection per API process; pooling, retention/cleanup,
backup policies and access control for conversation history remain separate work.
Concurrent first-start schema migrations should be coordinated operationally.
Replica set availability and commit reconciliation can still fail; atomicity does
not guarantee that a client immediately knows the result. The local single-node
setup has no redundancy, authentication or production hardening.
Product lookup retains the existing regex strategy and assumes an unambiguous
canonical product name.
