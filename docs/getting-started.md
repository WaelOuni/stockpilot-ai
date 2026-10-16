# Getting started

[Back to README](../README.md) · [Database](database.md) · [Observability](observability.md)

All shell commands here run from the **project root in WSL/Linux**, not `docs/`.
Prerequisites: Python **3.12**, Docker Desktop with Ubuntu WSL integration
(or compatible Docker Engine/Compose), and local Ollama. Model downloads need
disk space; Qwen needs adequate RAM and CPU inference can be slow.

## Python and local models

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'

# Separate terminal, only if Ollama is not already running:
ollama serve
# Another terminal connected to that same Ollama instance:
ollama pull qwen2.5:7b
ollama pull nomic-embed-text
```

On Windows, Ollama may run on the Windows host. The existing
[scripts/dev-env.sh](../scripts/dev-env.sh) activates `.venv` if present,
uses `ip route`/`awk` to obtain the current gateway, and exports
`WINDOWS_HOST`, `OLLAMA_BASE_URL`, Qwen and embedding model settings.
It requires the `ip` command (iproute2); it is not a universal WSL networking
detector and does not configure Windows listeners/firewalls.

```sh
# Only for Windows-host Ollama:
source scripts/dev-env.sh
curl "$OLLAMA_BASE_URL/api/tags"
```

Keep Ollama reachable only from a trusted local environment. Do not hardcode a
WSL IP. Verify the actual network path before changing host bindings/firewalls.
[Self-hosted runner](self-hosted-runner.md) explains why workflow-process
connectivity must be configured separately from an interactive shell.

## Environment and Compose

```sh
cp .env.example .env.local
# Review .env.local before exporting:
set -a
source .env.local
set +a
# Only when using Windows-host Ollama, after sourcing .env.local:
source scripts/dev-env.sh
docker compose up -d --wait
```

[Settings](../src/stockpilot/infrastructure/config/settings.py) reads environment
variables; it does **not** automatically load `../.env.local`. Export values explicitly.

| Variable | Default / role |
|---|---|
| `OLLAMA_BASE_URL` | `http://localhost:11434` |
| `OLLAMA_MODEL` | `qwen2.5:7b` |
| `OLLAMA_EMBEDDING_MODEL` | `nomic-embed-text` |
| `MONGO_URI` | `mongodb://localhost:27017/?replicaSet=rs0&directConnection=true` |
| `MONGO_DATABASE` | `stockpilot` |
| `BACKOFFICE_API_TOKEN` | Unset: technical order routes return 503; configure a private shared Bearer secret |
| `CUSTOMER_JWT_SECRET` | Unset: `/assistant` and `GET /orders` return 503; private HS256 signing secret |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD_VERIFIER` | Unset: demo admin sessions fail closed; server-only username and salted derived verifier |
| `ADMIN_TRUSTED_ORIGIN` | Unset: exact browser origin required for login/logout, e.g. `https://admin.example.test` |
| `DEPLOYMENT_ENVIRONMENT` | `development`; use `production` to forbid the local HTTP exception |
| `ADMIN_ALLOW_LOCAL_HTTP` | `false`; explicit loopback-only development exception |
| `ADMIN_SESSION_TTL_SECONDS` / `ADMIN_MAX_SESSIONS` | `1800` / `100`; fixed lifetime and bounded process-local capacity |
| `PRODUCT_IMPORT_MAX_BYTES` / `PRODUCT_IMPORT_MAX_PRODUCTS` | `2097152` / `500`; raw CSV byte limit and small atomic product batch limit |
| `POSTGRES_URL` | Compose local demo `stockpilot` PostgreSQL database |
| `LANGGRAPH_CHECKPOINT_DB_URI` | Unset: checkpoint storage uses `POSTGRES_URL` |
| `LOG_LEVEL` / `LOG_FILE` | `INFO` / `logs/stockpilot.jsonl`; empty file path disables file logs |
| `OTEL_ENABLED` | `false` |
| `OTEL_SERVICE_NAME` | `stockpilot-ai` |
| `OTEL_EXPORTER` | Settings default `console`; `.env.example` selects `otlp` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | Base URL `http://localhost:4318` |
| `OTEL_METRIC_EXPORT_INTERVAL_MS` | `10000`, positive integer |

Set a strong random `CUSTOMER_JWT_SECRET` in the API environment. A trusted
issuer using the same secret can create a one-hour local/demo token:

```sh
export CUSTOMER_JWT_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export CUSTOMER_TOKEN="$(python scripts/create_customer_token.py demo-customer)"
```

Use at least 32 random bytes. Export the same secret securely in the API process
environment; do not commit it or place it in captured logs. `/assistant` verifies
the token's signature, expiration and `customer_id` claim. The other public
endpoints do not require it.

Compose has public **demo-only** PostgreSQL credentials. Mongo/PostgreSQL ports
are published beyond loopback: use a trusted machine/firewall, never expose this
stack to the Internet. Existing volumes are preserved. Never use `down -v` or
seed commands on valuable data. For an existing standalone Mongo, follow
[non-destructive migration/recovery](database.md#mongo-replica-set-migration-and-recovery)
before starting the API.

## Demo administrator

Configure the backend process, never a `VITE_*` variable. Use a distinct,
random 16..256-character password; do not reuse either technical API token or
customer signing key. Generate its verifier with hidden terminal prompts:

```sh
source .venv/bin/activate
python scripts/create_admin_verifier.py
```

Securely set the returned verifier as `ADMIN_PASSWORD_VERIFIER` and configure
`ADMIN_USERNAME` plus `ADMIN_TRUSTED_ORIGIN`. There is no configured plaintext
password field. The verifier is sensitive server configuration: do not commit
it, publish its output or put it in browser artifacts. The helper deliberately
does not accept a password as a command-line argument (shell/process history).

HTTPS and a Secure HttpOnly `__Host-` cookie are the defaults. For a strictly
local HTTP demo, explicitly export `ADMIN_ALLOW_LOCAL_HTTP=true`,
`DEPLOYMENT_ENVIRONMENT=development` and an exact loopback browser origin
including Vite's port, for example `http://127.0.0.1:5173`. Match the actual
browser origin; no trailing slash. The backend request host must also be
loopback. `production` rejects that exception. With TLS termination, configure
Uvicorn's trusted proxy addresses narrowly so HTTPS scheme forwarding reflects
the trusted TLS proxy only.

Start the backend normally; open `/admin/login` on Vite, log in and consult
`/admin/orders`. Customer JWTs and technical Bearer tokens are not login
credentials and are not used by the admin Vue transport. The detail offers the
next legal status transition and confirmed cancellation for CREATED/PROCESSING.
These dedicated cookie-only routes require the exact Origin and the existing
session-bound CSRF token. No arbitrary stock mutation is exposed.
Uncertain outcomes are reread, never retried automatically.
Missing/unsafe configuration fails closed
without preventing the existing customer/public/technical routes from running.
The default `ADMIN_SESSION_STORE=memory` is limited to development/test. For
durable local operator sessions, explicitly select `ADMIN_SESSION_STORE=mongo`
before startup. This creates dedicated security collections and an expiry index
in the configured Mongo replica set; use only the intended environment. There is
no migration or automatic fallback. Store configuration changes require restart.
See [the durable session contract and limitations](security/admin-sessions.md)
and [session security](order-flow.md#demo-administrator-sessions-v2).
See [V3 operations and isolated E2E](order-flow.md#operational-administrator-orders-v3).

V4.1 adds `/admin/products`, `/admin/products/new` and
`/admin/products/:sku/edit` to the same guarded admin navigation. Metadata only:
SKU/name/category/finite nonnegative price on creation, immutable SKU and
name/category/price on edit, stock always read-only and initially zero.
No deletion/disable; stock adjustment uses its separate V4 page. Renaming preserves referenced existing
order restitution, but pending confirmations using the old name can fail.
See [SKU provisioning and the exact blocked activation](database.md#product-metadata-administration-and-sku-activation-v41).
The exact unique SKU index is now provisioned after separate authorization;
missing prerequisites still block writes with 503. Preparation commands are
read-only unless explicitly authorized with `--apply --confirm-database`.

### CSV product import (V6)

V6 adds `/admin/products/import`, linked from the admin product catalogue.
Select a UTF-8 CSV, review per-line errors/existing conflicts and confirm
CREATE_ONLY explicitly. Products start at stock zero; existing products are
never changed. Downloadable examples use real domain categories. The normal SKU
and stock prerequisites are already active, but the **new import receipt index
is not**: execution intentionally fails closed until separately authorized.
No normal provisioning is performed automatically. See
[format, limits, HTTP headers and manual activation](database.md#create_only-product-csv-imports-v6).

## Fresh application RAG database

For a **new demo database**, these SQL files create the extension/table and then
add source uniqueness. They do not drop tables/data:

```sh
docker compose exec -T postgres psql -v ON_ERROR_STOP=1 \
  -U stockpilot -d stockpilot < migrations/000_create_knowledge_chunks.sql
# Apply once to a newly created table, not an already migrated database:
docker compose exec -T postgres psql -v ON_ERROR_STOP=1 \
  -U stockpilot -d stockpilot < migrations/001_knowledge_chunks_source_unique.sql
python scripts/ingest_products.py
```

Do not reapply 001 blindly. Inspect constraints/duplicates on existing databases
and run migrations sequentially, not concurrently. Nomic vectors are 768
dimensions; changing model requires a separate migration/corpus plan, not
mixing embeddings in a populated table. See [RAG](rag.md) and
[dedicated eval database](database.md#dedicated-eval-database).

## Demo inventory and API

**`scripts/seed_products.py` deletes every product in local
`stockpilot.products` before inserting the three fixtures.** It directly uses
local Mongo, not `MONGO_URI`. Do not run it against real inventory.
Seeding is optional when the demo products already exist.

```sh
python scripts/seed_products.py
python -m uvicorn stockpilot.main:app --reload
```

Startup creates the unique partial order-operation index and official PostgreSQL
checkpoint tables. Mongo must be a healthy replica set. PostgreSQL startup
failure is explicit; there is no in-memory checkpoint fallback.

| Endpoint | Contract |
|---|---|
| `GET /health` | `{"status":"UP"}` process liveness, not dependency readiness |
| `POST /chat` | `{"message":"..."}`; direct LLM generation |
| `POST /classify` | `{"message":"..."}`; structured intent/extraction |
| `POST /assistant` | `{"message":"...","thread_id":"..."}`; requires an HS256 JWT with `customer_id` and `exp`; advice/stock/ORDER/ORDER_STATUS |
| `PATCH /orders/{order_id}/status` | `{"status":"PROCESSING"}`; technical/demo lifecycle update, back-office Bearer token required |
| `GET /orders` | Customer JWT required; own order history, bounded limit/offset pagination |
| `POST /customer/orders/{order_id}/cancel` | Customer JWT required; owned-order transactional cancellation |
| `POST /orders/{order_id}/cancel` | No body; transactional cancellation, back-office Bearer token required |

```sh
curl http://localhost:8000/health
curl -X POST http://localhost:8000/assistant -H "Authorization: Bearer $CUSTOMER_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"message":"Quel outil pour couper des panneaux de bois ?","thread_id":"demo-advice"}'
curl -X POST http://localhost:8000/assistant -H "Authorization: Bearer $CUSTOMER_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"message":"Est-ce que Bosch X100 est disponible ?","thread_id":"demo-stock"}'
curl -X POST http://localhost:8000/assistant -H "Authorization: Bearer $CUSTOMER_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"message":"Commande-moi 2 Bosch X100","thread_id":"demo-order"}'
# Same thread; confirmation modifies demo stock and creates a Mongo order:
curl -X POST http://localhost:8000/assistant -H "Authorization: Bearer $CUSTOMER_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"message":"Oui, je confirme","thread_id":"demo-order"}'
```

Keep the same verified customer identity and thread ID for all turns. Obtain a
new token from the trusted issuer when the one-hour token expires.
Checkpoints are isolated by `(customer_id, thread_id)`, not the raw thread alone.
Identity comes only from signed JWT claims; caller-supplied headers are ignored.
The back-office credential is separate. Legacy unowned orders remain
inaccessible, and old unnamespaced conversations are not resumed.
Use fresh thread IDs for independent experiments. Repeating a matching
operation ID is idempotent; starting another order is a new business operation.
[Order flow](order-flow.md) explains refusal, replay and unknown outcomes.

## Validation and shutdown

```sh
python -m pytest -q
python scripts/run_evals.py --mode deterministic --suite enriched
docker compose config --quiet
```

Deterministic evals are fixture/framework checks, not AI quality. Real measurement
commands and reviewed baseline policy are in [AI evals](ai-evals.md).
Generated reports, logs, `../.env.local`, IDE state and Mongo backups stay outside Git.

Stop Uvicorn with Ctrl+C (including its reload supervisor), and preserve volumes:

```sh
docker compose stop
```

Do not delete volumes to diagnose startup failures. Use the targeted
[database diagnostics](database.md) or [telemetry diagnostics](observability.md).
