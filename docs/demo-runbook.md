# V7 integrated local demo

Synthetic data only. Dedicated stores: Mongo `stockpilot_demo` on writable
`rs0` primary, PostgreSQL `stockpilot_demo`, namespace `public`. Never run the
normal initializer, import-index activation, RAG ingestion, or an E2E reset
against this demo. CSV import is intentionally unavailable: the demo does not
have an authorized `product_imports` collection/index.

## Launch from WSL/Linux

Use the installed Python/Node dependencies (Node/npm must be on `PATH`).
Ordinary startup needs no DEV sourcing or manual database exports:

```sh
bash scripts/run-demo.sh
```

The launcher selects Mongo `stockpilot_demo` **before** read-only preflight.
It reuses the normal `MONGO_URI` server/replica configuration (or its Settings
default), but refuses a URI embedding any other database instead of rewriting
it. PostgreSQL defaults to the existing `stockpilot-postgres` Docker server:
credentials are resolved privately in process memory, its TCP system identifier
is checked, and only the already-provisioned `stockpilot_demo` is selected.
Inherited `MONGO_DATABASE`, `POSTGRES_URL` and `LANGGRAPH_CHECKPOINT_DB_URI`
do not redirect demo startup to DEV/E2E. Normal Settings defaults,
`.env.local` and `scripts/dev-env.sh` are unchanged and are not sourced or edited.

Optional private overrides are `STOCKPILOT_DEMO_MONGO_URI`,
`STOCKPILOT_DEMO_POSTGRES_URL` and `STOCKPILOT_DEMO_CHECKPOINT_URI`. Both
PostgreSQL overrides must explicitly name `stockpilot_demo`; Mongo must declare
`replicaSet=rs0` and may embed only `stockpilot_demo`. Empty or wrong-target
overrides fail closed. Without a checkpoint override, checkpoints use the
selected demo PostgreSQL URL. The exact checked URIs are passed to the API
child; no database configuration or credential is sent to Vite. Launcher and
both verifiers use the same resolver and pin its private overrides in child
environments, while preserving the original DEV environment. Actual Mongo
`replicaSetId` must match the server configured by the original `MONGO_URI`
(or its default); PostgreSQL RAG and checkpoints must match the existing Docker
server `system_identifier`. Aliases/ports are accepted only for those instances,
not for a different server with a database of the same name. These read-only
identity probes require replica-set configuration and PostgreSQL control-system
read permissions; unavailable identities fail closed before startup. Existing
non-database server configuration may still be supplied privately as needed.
Never print resolved credentials or enable shell tracing.

### Separate, explicitly authorized provisioning

Preflight never provisions a database or runs seed/repair migrations. After it
passes, application startup requests the existing Mongo indexes and invokes
the official checkpointer `setup()` on the verified current schema. A launch
is therefore not a strictly read-only check.
If demo PostgreSQL has not yet been prepared, provision it separately only
with explicit authorization:

```sh
.venv/bin/python scripts/prepare_demo_postgres.py --authorize-stockpilot-demo
```

The provisioning command checks that the TCP server is the existing Docker
server using its system identifier. It creates the fixed database only when
absent, executes the existing vector/knowledge migrations, and invokes the
installed official LangGraph checkpointer setup (`langgraph-checkpoint-postgres`
3.1.2). It neither copies normal data nor ingests documents. It validates the
actual database, tables, migration version and knowledge schema afterward.
Normal PostgreSQL and E2E PostgreSQL are not provisioned by this command.

The launcher itself remains read-only until the application starts. It refuses
normal/evaluation/E2E targets and verifies both effective PostgreSQL URIs,
`public` namespace, the immutable Mongo reference content/hash, separately
manifested test fixtures and the exact existing six custom Mongo indexes.
It prompts privately for administrator credentials. Vite receives only a
minimal environment and an empty project-local temporary env directory, removed
on shutdown; no server secret is a `VITE_*` variable.

Startup-selection regression: **34 launcher/runtime guard tests passed**.
A real `bash scripts/run-demo.sh` launch, without DEV sourcing/database exports
and with deliberately inherited DEV/E2E database variables, passed frontend,
proxied health/catalogue, private admin cookie/login-CSRF/`me`/dashboard/logout
and customer JWT read/isolation checks. Before/after snapshots preserved every
Mongo document/index (554 products, 2,018 orders, 3,039 movements, 7 manifests),
every public PostgreSQL table/index, all 9 checkpoints, empty RAG and both DEV
configuration hashes. Only ephemeral login/logout sessions were mutated;
owned listeners were stopped. This startup check did not provision, seed,
repair or create business fixtures.

UI: **http://127.0.0.1:5175**; API: **http://127.0.0.1:8003**. Both are
loopback-only. Vite proxies `/api` to the actual FastAPI application. Admin
authentication uses its real HTTP-only host-only local cookie and exact
trusted Origin; mutations additionally require session CSRF. This development
HTTP configuration must not be exposed externally.

## Browser walkthrough

1. `/products`: inspect pages of 20, next/previous, literal SKU/name search,
   category and availability. Open a product and return to the preserved URL
   filters. Stock is current Mongo inventory, not an inference from RAG.
2. `/admin/orders`: log in with the privately chosen credentials.
3. `/admin/dashboard`: global totals include the immutable reference **and**
   retained test fixtures. Recent orders/adjustments are top-five summaries,
   not exhaustive ledgers. Follow order details and stock-history links.
4. `/admin/products`: paginate the full product catalogue. Do not alter the
   seeded reference products. Business tests use only new UUID-owned fixtures.
5. An authenticated customer can prepare/confirm ORDER with the installed
   Ollama model, read status/history, and cancel CREATED/PROCESSING orders.
   SHIPPED cancellation is forbidden; the separate existing E2E suite covers
   shipment and delivery.

`knowledge_chunks` is intentionally **empty**. Product advice has no ingested
documentary evidence and is not validated as reliable RAG advice. No corpus
ingestion, embedding campaign, model pull or real-AI evaluation was run.
Only a light real ORDER preparation/confirmation was exercised.

## Reproduce isolated validation

These commands start services and have different side effects; do not substitute
the normal Playwright configuration for the dedicated demo browser configuration.
`--read-only` skips business writes only: startup still invokes the existing
initialization paths and writes a local report. Combining it with `--full-checks`
still runs integration writes and the E2E reset. Restart verification also
starts the application; none of these commands is an offline unit check.

For launcher/schema guards without services or database writes:

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_demo_launcher.py tests/test_demo_runtime.py
```

```sh
# Actual guarded launcher, private ephemeral admin/customer credentials,
# API-created retained fixtures, read benchmarks and no-reset demo browser.
.venv/bin/python scripts/verify_demo_runtime.py --execute --full-checks

# Recheck a completed retained fixture without adding business documents.
.venv/bin/python scripts/verify_demo_runtime.py --execute --read-only

# Only owned app restarts and real short-lived sessions; no Mongo mutations.
.venv/bin/python scripts/verify_demo_restart.py
```

`--execute` adds one product, one order and two administrative stock movements
per successful runtime execution, plus its manifest. Business values are
created/changed through actual API/use cases; direct writes are limited to
ownership markers and manifest bookkeeping. Failed attempts are retained and
reconciled, never deleted or silently retried. The original reference
fingerprint remains
`a7112fc09fb3afd2618dfd3bb7b8f5a2e3f83203b5e40d17e64083bdeee8d22c`.

`--full-checks` runs ordinary pytest, the two existing UUID-isolated Mongo
integration modules, frontend tests/build/lint and the existing seven-test
Playwright suite. Only that existing harness resets **stockpilot_e2e**, after
its exact-store guards and PostgreSQL advisory lock. Demo Playwright uses
`frontend/playwright.demo.config.ts`, never seeds/resets, and disables traces,
screenshots and videos. Runtime JSON reports contain only synthetic identifiers,
totals, hashes, statuses and timings; no passwords, cookies, JWTs or URIs.

External local HTTP measurements are sequential, ten samples per endpoint
after warm-up, nearest-rank p50/p95. They include the actual Vite proxy and
FastAPI/Mongo reads, but are not production throughput, concurrency or SLA
evidence. The original 100-request in-process read benchmark is separate.

## Measured V7 runtime results (10 October 2026)

The actual catalogue/admin browser tests passed **2/2** against the guarded
servers, not an in-process application. The real configured Ollama model
prepared an ORDER and explicit confirmation reserved one unit on an isolated
API-created product. Positive/negative stock adjustments and identical-operation
replay produced exactly two movements; CREATED → PROCESSING → CANCELLED,
customer status/history reads and a second cancellation restored stock only
once. No reference product was edited. One earlier runtime harness attempt
omitted the customer identity claim and is retained as a failed fixture run;
the corrected token uses the existing `customer_id` claim.

Current verified Mongo totals are **554 products / 2,018 orders / 3,039
movements**, including **54 / 18 / 39** separately manifested test documents.
Six test manifests comprise three completed and three retained failed runs
(including the two earlier transaction-harness failures). The immutable
500/2,000/3,000 reference documents still match every field and the original
hash. Dashboard totals read from the actual Mongo adapters match browser/API:
546 available products, 8 unavailable, **41,882 stock units**; orders:
CREATED 214, PROCESSING 200, SHIPPED 200, DELIVERED 1,200, CANCELLED 204.
Seeded future-dated movements still sort above today's adjustment: they were
not rewritten to make the dashboard artificially look recent.

The refreshed read verifier paginated all 554 products and 2,018 admin orders,
validated details/customer ownership/history/filter boundaries, and audited
**zero persistent write commands**. Its 100 timed reads had zero errors.
Actual loopback HTTP/proxy timings (50 timed reads, zero errors):

| Endpoint | p50 ms | p95 ms |
|---|---:|---:|
| Catalogue | 2.890 | 3.587 |
| Combined catalogue filters | 3.196 | 3.805 |
| Unavailable catalogue | 2.534 | 2.872 |
| Dashboard | 10.988 | 11.450 |
| Admin orders | 4.848 | 5.209 |

Backend regression: **1,337 passed, 22 deselected**; guarded UUID-isolated Mongo
integration: **21 passed**. Frontend: **355 tests / 19 files**, successful
vue-tsc/Vite build and ESLint. New schema/launcher guards: **19 passed**.
The initial full run exposed an environment-dependent test fixture default;
the fixture now explicitly clears its checkpoint URI, and regression subprocesses
do not inherit private runtime authentication/database settings.

The existing complete Playwright suite passed **7/7** in its authorized
`stockpilot_e2e` stores, preserving catalogue, customer polling/cancellation,
admin lifecycle, products, stock, dashboard and CSV-import coverage. The final
dedicated demo browser recheck passed **2/2** after tightening response predicates
to avoid confusing an in-flight restored-filter request with the unavailable
filter request. This was a test synchronization defect, not a stock/filter
application defect.

Two real owned app restarts passed short-lived session expiry/relogin and
old-cookie rejection. All **9** persisted demo checkpoint rows, the immutable
Mongo baseline, manifested fixture counts and empty RAG table were preserved.
Demo listeners 8003/5175 and E2E listeners 8002/5174 were shut down; no shared
Docker restart was performed. Git HEAD remains `ef6ef83`; no commit/push/reset,
normal-store write, dependency installation or infrastructure addition.

Final connection-resolution validation passed the 46 offline tests and a
real guarded launch on 11 October 2026 (Europe/Paris). Mongo replica-set identity,
PostgreSQL server identity, FastAPI/Vue startup, admin login/dashboard/logout
and owned-process shutdown passed without business mutations. Before/after
snapshots preserved all Mongo documents/indexes and all public PostgreSQL
tables/indexes, including the reference fixture and checkpoints. See
`final_v7_live_validation` in `data/demo-runtime-report.json`; its timestamp is
UTC. Earlier full-suite/runtime reports retain their historical results.
Machine-specific Python executable paths in validation commands were replaced
with `python`; measured outcomes were preserved.

Evidence: `data/demo-runtime-report.json` (latest read-only live recheck),
`data/demo-full-validation-report.json` (successful full-suite runtime snapshot),
`data/demo-read-report.json` (100 audited read requests), and
`data/demo-restart-report.json` (owned restart/session persistence).

## Stop and diagnose

Ctrl+C or SIGTERM to the launcher PID stops only its owned API/Vite process
groups. Validation scripts also verify that both listening ports are closed.
No shared Docker service is restarted. On a busy port, the launcher refuses
without replacing a process; its probe tolerates TCP TIME_WAIT but not a live
listener. In WSL, inspect `ss -ltnp` and process IDs. Never kill by process name.

A preflight failure starts no application process. Check dedicated target
names, replica-set primary, exact schema/index definitions and reference hash;
do not repair/delete reference data to bypass a gate. PostgreSQL credentials
from stale source configuration can differ from the existing Docker server:
the launcher's private resolver reads its environment and verifies server identity.
The Bash launcher must use LF line endings (`.gitattributes`).
