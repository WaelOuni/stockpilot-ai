# StockPilot AI

## What is StockPilot?

StockPilot is a local AI assistant for French tool advice, stock lookup and
multi-turn orders. It combines structured intent extraction, product-document
RAG and explicit order confirmation. It is a **development/demo backend**,
not a production-ready commerce service.

## Key features

- Structured intent classification and product/category/quantity/budget extraction.
- Product advice grounded in local product documents through pgvector RAG.
- Stock lookup against Mongo inventory, separate from document recommendations.
- Multi-turn ORDER preparation followed by explicit confirmation.
- ORDER_STATUS lookup by explicit order ID or the last created order in the
  current thread, with a deterministic response read from Mongo.
- Atomic stock reservation/order creation with Mongo replica-set transactions.
- Idempotent confirmation using a stable business operation identifier.
- Persistent LangGraph conversation checkpoints in PostgreSQL.
- Full local observability: JSON logs, traces, metrics, dashboards, SLO rules
  and independent Blackbox liveness probes.
- Mocked pytest, deterministic eval-framework CI and a manual real AI quality gate.

## Architecture

```text
User -> FastAPI -> LangGraph -> Use cases -> Ports -> MongoDB (products/orders)
                  |                         |----> PostgreSQL/pgvector (RAG)
                  |                         |----> Ollama (local models)
                  +-> PostgreSQL (conversation checkpoints)
```

The composition root wires application ports to infrastructure adapters.
Business rules do not own database sessions; the Mongo transaction adapter does.
See [architecture](docs/architecture.md) for layers, workflow and boundaries.

## Tech stack

| Area | Stack |
|---|---|
| API / orchestration | Python 3.12, FastAPI/Pydantic, LangGraph |
| Models | Ollama, `qwen2.5:7b`, `nomic-embed-text` |
| Data | MongoDB 8 replica set, PostgreSQL 16/pgvector |
| Telemetry | OpenTelemetry, Collector contrib, Alloy/Loki, Tempo |
| Monitoring | Prometheus/Blackbox Exporter, Grafana |
| Validation | pytest, offline golden datasets, GitHub Actions |

Dependencies: [pyproject.toml](pyproject.toml).
Local services: [docker-compose.yml](docker-compose.yml).

## Quickstart: fresh demo environment

Run shell commands from the **project root in WSL/Linux**. Prerequisites:
Python **3.12**, Docker Desktop with Ubuntu WSL integration (or compatible
Docker Engine/Compose), and Ollama. Model downloads require disk space;
Qwen needs adequate RAM and CPU inference can be slow.

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'

# Separate terminal, only if Ollama is not already running:
ollama serve
# Another terminal connected to the same Ollama instance:
ollama pull qwen2.5:7b
ollama pull nomic-embed-text
```

Review/export the example environment; Settings does **not** load `.env.local`
automatically:

```sh
cp .env.local.example .env.local
set -a
source .env.local
set +a
# Only when Ollama runs on the Windows host:
source scripts/dev-env.sh
curl "$OLLAMA_BASE_URL/api/tags"
docker compose up -d --wait
```

The WSL helper uses the current gateway, not a hardcoded IP, and requires
`ip route`/`awk`. Verify actual connectivity; keep Ollama local/trusted.
Compose uses public **demo-only** PostgreSQL credentials.
Mongo/PostgreSQL ports are published beyond loopback: use a trusted local
machine/firewall, never Internet exposure.

### Initialize the application document corpus

For a **new demo database only**:

```sh
docker compose exec -T postgres psql -v ON_ERROR_STOP=1 \
  -U stockpilot -d stockpilot < migrations/000_create_knowledge_chunks.sql
# Apply 001 once to a newly created table, not an already migrated database:
docker compose exec -T postgres psql -v ON_ERROR_STOP=1 \
  -U stockpilot -d stockpilot < migrations/001_knowledge_chunks_source_unique.sql
python scripts/ingest_products.py
```

Do not blindly reapply the uniqueness migration to existing data.
Nomic embeddings use 768 dimensions; changing models requires a separate
migration/corpus plan. Ingestion upserts the three demo documents by source.
See [database](docs/database.md) for existing databases, dedicated `stockpilot_eval`
initialization, checkpoints and all Mongo recovery procedures.

### Seed demo inventory and start the API

**The seed script deletes all products in local `stockpilot.products` before
inserting fixtures.** It uses local Mongo directly, not `MONGO_URI`.
Skip seeding if demo products already exist. Never seed valuable inventory.
Existing volumes are preserved; do not use `docker compose down -v`.

```sh
python scripts/seed_products.py
python -m uvicorn stockpilot.main:app --reload
```

Startup requires healthy Mongo `rs0` and PostgreSQL, creates the unique order
operation index and official checkpoint tables, and fails explicitly on checkpoint
connection/setup errors. There is no in-memory checkpoint fallback.
For an existing standalone Mongo, read the
[migration guide](docs/database.md#mongo-replica-set-migration-and-recovery) first.

## API examples

| Endpoint | Contract |
|---|---|
| `GET /health` | Process liveness, not dependency readiness |
| `POST /chat` | `{"message":"..."}`; direct LLM generation |
| `POST /classify` | `{"message":"..."}`; structured intent/extraction |
| `POST /assistant` | `{"message":"...","thread_id":"..."}`; advice/stock/ORDER/ORDER_STATUS |
| `PATCH /orders/{order_id}/status` | `{"status":"PROCESSING"}`; Bearer-protected demo back-office lifecycle update |
| `POST /orders/{order_id}/cancel` | No body; atomic cancellation/stock restoration, requires back-office Bearer token |

```sh
curl http://localhost:8000/health
curl -X POST http://localhost:8000/assistant \
  -H 'Content-Type: application/json' \
  -d '{"message":"Quel outil pour couper des panneaux de bois ?","thread_id":"demo-advice"}'
curl -X POST http://localhost:8000/assistant \
  -H 'Content-Type: application/json' \
  -d '{"message":"Est-ce que Bosch X100 est disponible ?","thread_id":"demo-stock"}'
curl -X POST http://localhost:8000/assistant \
  -H 'Content-Type: application/json' \
  -d '{"message":"Commande-moi 2 Bosch X100","thread_id":"demo-order"}'
# Same thread: explicit confirmation modifies demo stock and creates an order:
curl -X POST http://localhost:8000/assistant \
  -H 'Content-Type: application/json' \
  -d '{"message":"Oui, je confirme","thread_id":"demo-order"}'
```

Keep `thread_id` across turns/restarts; use fresh IDs for separate experiments.
After confirmation, ask for status in the same thread:

```sh
curl -X POST http://localhost:8000/assistant \
  -H 'Content-Type: application/json' \
  -d '{"message":"Quel est le statut de ma dernière commande ?","thread_id":"demo-order"}'
```

An explicit order ID takes priority. Only Mongo's stored status is returned;
this demo has no authentication or ownership checks. See
[ORDER_STATUS](docs/order-flow.md#order_status-read-only-lookup) for lookup results.

Matching operation replays return the original order without decrementing stock
again. Unknown commit outcomes require reconciliation, not blind compensation.
See [order flow](docs/order-flow.md).

## AI quality

```sh
python -m pytest -q
python scripts/run_evals.py --mode deterministic --suite enriched
```

Standard pytest is mocked and needs no Mongo/PostgreSQL/Ollama.
Deterministic eval mode is a fixture/framework smoke run, **not AI quality**.
Real local evals require installed models and a dedicated ingested document DB;
commands/provenance are in [AI evals](docs/ai-evals.md) and
[the scoring reference](evals/README.md).

The human-reviewed historical **60-case** local Qwen/pgvector reference measured:

| Metric | Historical result |
|---|---|
| Failed cases | 0 / 60 |
| Intent / product-name / ORDER quantity accuracy | 100% / 100% / 100% |
| Recall@1 / Recall@3 | 87.5% / 100% |
| Recommendation product accuracy / grounding | 100% / 100% |

These are measured historical results on small annotated denominators, **not a
universal guarantee**. Only three documents exist; grounding covers the
structured product, not every generated claim. Enriched datasets remain
separate validation. CI never automatically promotes or overwrites the reviewed
baseline.

## Observability

JSON logs work without telemetry services and exclude bodies/prompts/responses/
chunks. Business identifiers/product names remain private diagnostic data.
Tracing/metrics are disabled by default. To export them, restart the API with
`OTEL_ENABLED=true`, `OTEL_EXPORTER=otlp` and base endpoint
`http://localhost:4318`.

- **Logs:** rotating JSONL -> Alloy -> Loki.
- **Traces:** OTLP HTTP -> Collector contrib -> Tempo.
- **Metrics:** OTLP HTTP -> Collector Prometheus exporter -> Prometheus.
- **Availability:** Blackbox independently probes `/health` every 10 seconds.
- **Grafana:** http://localhost:3000; provisioned overview/SLO dashboards,
  logs-to-traces and traces-to-logs links.

Collector receives traces/metrics, not the file-log pipeline. Local Prometheus
SLO/reachability alerts exist; no Alertmanager/notifications are configured.
See [observability](docs/observability.md) for startup, credentials, privacy,
retention/rotation caveats, PromQL/LogQL/TraceQL and diagnostic commands.

## Repository / CI strategy

`main` is stable/demo; `dev` is development.
PRs and pushes to **main/dev** run **Unit Tests** and **AI Eval Framework**.
Branch protection must explicitly require these checks to block merges.

Real AI evals are **manual only**, on a Linux x64 self-hosted runner labelled
`stockpilot-evals`, guarded to `WaelOuni/stockpilot-ai` **dev only**.
They require an isolated trusted environment, independent approval, reachable
Ollama models and a pre-ingested dedicated eval DB. Runner/environment setup
must be verified, not assumed configured. See
[GitHub Actions](docs/github-actions.md) and
[self-hosted runner setup](docs/self-hosted-runner.md).

## Documentation

| Guide | Contents |
|---|---|
| [Architecture](docs/architecture.md) | Layers, ports, workflow, stack and boundaries |
| [Getting started](docs/getting-started.md) | Environment, WSL/Ollama, fresh setup and API |
| [Database](docs/database.md) | RAG/eval schemas, Mongo migration/recovery, checkpoints |
| [Order flow](docs/order-flow.md) | Confirmation, transactions, retries and idempotency |
| [RAG](docs/rag.md) | Ingestion, retrieval/filtering, advice guard and grounding |
| [Observability](docs/observability.md) | Logs/traces/metrics, dashboards, SLO/Blackbox diagnostics |
| [AI evals](docs/ai-evals.md) | Run modes, measured quality and reference policy |
| [GitHub Actions](docs/github-actions.md) | Triggers, guards, approval, reports and checks |
| [Self-hosted runner](docs/self-hosted-runner.md) | Linux x64 registration, WSL networking and security |
| [Eval scoring reference](evals/README.md) | Datasets, formulas, reports and correction history |

## Current limitations

No user authentication, payment or frontend. Technical order routes use a shared
`BACKOFFICE_API_TOKEN`; unset configuration disables those routes (503).
See [back-office authentication](docs/order-flow.md#back-office-authentication).
Public assistant endpoints remain unauthenticated. Model extraction is probabilistic;
the advice-domain guard is lexical, not semantic. Small datasets and a
three-document corpus limit quality conclusions. Product lookup assumes
unambiguous canonical names. PyMongo is synchronous in an async flow.
Durable checkpoints do not serialize concurrent same-thread requests, and
Mongo orders/PostgreSQL checkpoints have no shared distributed transaction.
Single-node databases have no redundancy; dependencies are not fully locked.
This stack is not hardened for production/public access.

Keep generated results, logs, `.env.local`, IDE state, runner files and Mongo backups
outside Git. Preserve existing volumes and private data during troubleshooting.
