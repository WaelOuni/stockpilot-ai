# Architecture

[Back to README](../README.md) · [Order flow](order-flow.md) · [RAG](rag.md)

StockPilot is a local development/demo backend for French tool advice, inventory
lookup and explicitly confirmed orders. It separates deterministic business
rules from model interpretation and infrastructure through application ports.
It is not a production commerce service.

## Request path and boundaries

```text
User -> FastAPI -> LangGraph -> Use cases -> Ports
                                           |-> Ollama: classification/generation
                                           |-> PostgreSQL/pgvector: document RAG
                                           |-> MongoDB rs0: products/orders
                  |-> PostgreSQL: conversation checkpoints
```

`src/stockpilot/main.py` is the composition root. It wires adapters and use cases,
configures logging/telemetry and manages the checkpoint connection in FastAPI
lifespan. The graph is compiled during lifespan, not at module import.

```text
src/stockpilot/
  domain/          entities, enums, deterministic services
  application/     DTOs, ports, use cases, LangGraph workflows
  infrastructure/  Ollama, configuration, Mongo/PostgreSQL adapters
  presentation/    FastAPI routes and request schemas
  main.py          composition root and resource lifespan
```

The domain/application do not import PyMongo. `OrderTransactionPort` defines the
atomic confirmation operation; the Mongo adapter owns sessions and retries.
RAG and checkpoints share PostgreSQL by default but use separate connections
and tables. Mongo orders and PostgreSQL checkpoints are not a distributed
transaction.

## Layer responsibilities and dependency rules

| Layer | Responsibility and boundary |
|---|---|
| Domain | Entities, enums and deterministic services; no FastAPI, HTTP, Ollama or LangGraph dependencies |
| Application | DTOs, use cases and ports (`LlmPort`, embedding, knowledge, product, order and transaction interfaces); LangGraph workflows orchestrate these operations |
| Infrastructure | Implements application ports with Ollama, Mongo and PostgreSQL; owns connection/session details and environment configuration |
| Presentation | FastAPI routes and request schemas translate HTTP requests into workflow/use-case calls |
| Composition root | `main.py` selects concrete adapters, injects them and manages their lifespan |

Adapters depend on application interfaces, not the other way around. LangGraph
intentionally remains in `application/workflows`, outside the domain.
Logging/telemetry are shared cross-cutting instrumentation used by application
and infrastructure code; this is lightweight Clean/Hexagonal Architecture,
not complete framework isolation of the application layer.

## Workflow

Every `/assistant` request first resolves a pending order confirmation.
If not completed there, classification routes to PRODUCT_ADVICE, STOCK_CHECK,
ORDER or UNKNOWN. Advice retrieves product documents and separately checks
inventory; ORDER prepares a pending operation and awaits explicit confirmation.
Keep the same caller-supplied `thread_id` across turns and restarts.

Classification uses structured extraction and application category normalization.
A positive lexical tooling-domain guard applies only to PRODUCT_ADVICE;
ORDER/STOCK_CHECK are not filtered. This is not a semantic domain guarantee.
See [RAG](rag.md) and [measured AI evaluations](ai-evals.md).

## Stack

| Area | Implementation |
|---|---|
| HTTP/configuration | Python 3.12, FastAPI, Uvicorn, Pydantic/settings, httpx |
| Orchestration | LangGraph; official AsyncPostgresSaver |
| Local models | Ollama, Qwen `qwen2.5:7b`, `nomic-embed-text` |
| Persistence | MongoDB 8 single-node replica set; PostgreSQL 16/pgvector |
| Logs | stdlib JSONL, Grafana Alloy, Loki |
| Traces | OpenTelemetry HTTP/protobuf, Collector contrib, Tempo |
| Metrics/availability | OpenTelemetry, Prometheus, Blackbox Exporter |
| Visualization | Grafana overview and SLO dashboards |
| Validation | Mocked pytest, deterministic eval framework, manual real AI CI |

Exact dependency declarations are in [pyproject.toml](../pyproject.toml);
development services are in [docker-compose.yml](../docker-compose.yml).
Dependency ranges are not a full lockfile. The checkpoint adapter pins
`langgraph-checkpoint-postgres==3.1.2`; the previously verified installed versions
were LangGraph 1.2.14 and langgraph-checkpoint 4.2.0, not locked dependencies.

## Tests

The standard suite uses fakes/mocks, including injected MemorySaver or mocked
startup contexts, and does not contact Mongo/PostgreSQL/Ollama:

```sh
python -m pytest -q
```

[Order flow](order-flow.md#tests) documents opt-in replica-set tests;
[database](database.md#checkpoint-validation) documents PostgreSQL tests.
[evals/README.md](../evals/README.md) documents framework scoring and real runs.

## Boundaries and limitations

- No authentication, payment or frontend; not hardened for public access.
- Model extraction is probabilistic and the advice guard is lexical.
- Product lookup uses the existing regex strategy and assumes an unambiguous
  canonical product name.
- Synchronous PyMongo calls in an async flow can block the event loop.
- Durable checkpoints alone do not serialize simultaneous requests for a thread.
  Operation idempotency and Mongo transactions still protect finalization.
- Checkpoints and orders have separate commit boundaries; uncertain Mongo
  commits can require manual reconciliation.
- Single-node databases have no redundancy. Pooling, conversation cleanup,
  backup policies and history access controls remain operational work.
- Small datasets and a three-document corpus limit evaluation conclusions.
- Observability is best-effort, not durable delivery or business accounting.

See [getting started](getting-started.md) for network/data warnings and
[observability](observability.md) for privacy and delivery limitations.
