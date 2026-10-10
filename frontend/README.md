# StockPilot frontend MVP

Vue 3 + TypeScript + Vite, Composition API, vue-router and native fetch.
Only the customer endpoints `POST /assistant`, `GET /orders` and
`GET /orders/{order_id}` are used.
There is no back-office UI, login service or client-side business transition logic.

## Run locally

Use Node 22.13+ (22.x), Node 24.x or Node 26+ and npm. In WSL with nvm installed,
load your nvm environment before running these commands.
Start the existing FastAPI backend separately on port 8000 with its required
Mongo/Postgres/Ollama services and `CUSTOMER_JWT_SECRET` configured.

```sh
cd frontend
cp .env.example .env.local
npm install
npm run dev
```

Open the local address printed by Vite. `VITE_API_BASE_URL` defaults to `/api`.
Vite proxies `/api/*` to `API_PROXY_TARGET` (default `http://127.0.0.1:8000`)
and removes the `/api` prefix. This avoids changing backend CORS configuration.
Only configure a trusted backend URL: the HTTP client sends the JWT to it.

From the repository root, in a separate terminal with the backend's private
signing secret already exported, generate a short-lived customer JWT:

```sh
source .venv/bin/activate
python scripts/create_customer_token.py local-client-a
```

Paste the resulting JWT into the demo connection panel. Do not paste the signing
secret. Do not put tokens/secrets in `VITE_*` variables: those values are public
in the compiled JavaScript. Avoid sharing screenshots or terminal output with JWTs.

The token is held only in memory, not in localStorage, sessionStorage, cookies
or URLs. The input is cleared after submission. Reloading the page requires
reconnecting. Switching/disconnecting the token resets the conversation/thread,
clears order data and aborts old requests. An invalid token is rejected by the
backend; the panel does not claim to validate it.

## Structure

```text
src/
  api/                 HTTP transport, errors and typed customer endpoint calls
  components/          Demo JWT panel and shared error notice
  features/assistant/  Session-local transcript, thread and conversation UI
  features/orders/     Customer history and offset pagination
  pages/               Assistant and My Orders page shells
  router/              /assistant, /orders and /orders/:orderId
  types/               Backend request/response and enum contracts
  utils/               In-memory token and display formatters
  App.vue              Navigation and shared layout
  main.ts              Vue composition root
  style.css            Shared responsive styles
```

No Pinia is needed: a small reactive assistant session survives route navigation.
Orders reload on page entry and through the refresh button. All history states,
including CANCELLED, are shown. Pagination uses limit 20 and offset increments of
20; `count` is the page size, not a total. A full last page can lead to an empty
next page because the API has no `has_more` field.

Each history card and identified assistant summary links to its detail page.
The page loads the current owned order directly through the customer API, not
through the LLM. It renders product, quantity, identifier, creation date and
status, plus a CREATED / PROCESSING / SHIPPED / DELIVERED timeline. CANCELLED
shows cancellation without suggesting subsequent stages were reached.
There are no stage timestamps in the contract, so none are invented.

The detail page polls 15 seconds **after each completed successful request**,
only while mounted and non-terminal, without overlapping requests. DELIVERED,
CANCELLED and errors stop polling; manual refresh remains available. Changing
the route/JWT aborts stale requests and clears the old detail. 404 does not
distinguish foreign, legacy and nonexistent orders. A request already processed
by the server is not rolled back by browser cancellation.

`/assistant` returns the existing partial LangGraph state, not a dedicated
Pydantic response DTO. Types are based on `schemas/chat.py`, `schemas/order.py`,
`workflows/state.py` and domain enums. The client checks JSON and the fields
needed to start rendering, not a complete generated runtime schema.
Assistant text is rendered as escaped plain text, not HTML or Markdown.
Structured response cards supplement that text: pending orders offer Confirm
and Cancel buttons, confirmed orders show their identifier and a My Orders link,
insufficient stock shows the requested quantity, and FOUND status lookups show a
badge for the exact supported status. My Orders uses the same status badge.
Cards remain in the local transcript; only the latest eligible preparation can
be acted on. Buttons send `Oui je confirme` or `Non annule` through `/assistant`
in the current thread, never a back-office request. Old cards, in-flight requests
and failed decision attempts cannot trigger another click-based confirmation.

The API does not provide structured available stock or product/quantity in
ORDER_STATUS; those values are not guessed from prose or stale workflow fields.
The insufficient-stock card points to the response text for availability.
The status card shows only the queried ID/status. A creation summary uses its
confirmed pending-order payload and ID, and links to My Orders for current status
instead of inventing a lifecycle field absent from that response.
ORDER remains conversational: enter the requested product/quantity, then follow
the backend's explicit confirmation prompt in the same thread.
No request is retried automatically, especially after ambiguous order failures.
Aborting a browser request does not roll back server-side work; consult order
history/status before submitting another confirmation.

## Validation

```sh
npm run build
npm run test
npm run lint
```

Vitest + Vue Test Utils test fetch transport, JWT injection, HTTP/network/JSON
errors, both page renderings, ORDER confirmation, pagination and identity-change
isolation. Tests mock fetch; they do not contact Ollama or mutate Mongo orders.
ESLint covers TypeScript and Vue; build runs vue-tsc before Vite.

## Deployment and limits

Vite's proxy exists only in development, not in the production bundle or preview.
For deployment, serve `dist/` behind a trusted same-origin reverse proxy mapping
`/api` to FastAPI, plus SPA fallback to `index.html` for frontend routes.
Alternatively configure a trusted public API URL and deliberately configure its
CORS policy outside this MVP. Use HTTPS off loopback. Never place JWT signing or
back-office secrets in the frontend.

This is local/demo auth: no login, refresh, revocation or automatic expiry check.
An XSS vulnerability or malicious browser extension can still access an in-memory
token. Local conversation and thread IDs disappear on reload; server-side
checkpoints/orders remain. There are no streaming responses, automatic retries,
cursor pagination or back-office mutations.

## Real browser E2E

Playwright runs one Chromium test in `e2e/order-flow.spec.ts`. It starts a
dedicated FastAPI on port **8002** and Vite on **5174**, with no server reuse
and no automatic retries. Only Mongo/Postgres/Ollama must already be running.
The normal development FastAPI instance is neither used nor stopped.

| Resource | E2E target |
|---|---|
| Mongo products and orders | `stockpilot_e2e` |
| PostgreSQL checkpoints | Database `stockpilot_e2e`, public checkpoint tables |
| FastAPI | `127.0.0.1:8002` |
| Vite proxy | `/api` exclusively to `127.0.0.1:8002` |
| JWT | Ephemeral signing secret shared by the launcher and workers |

Before serving, `scripts/init_e2e_env.py` creates the dedicated PostgreSQL
database if absent, runs the official checkpointer setup, truncates only
`public.checkpoints`, `public.checkpoint_blobs` and `public.checkpoint_writes`,
and resets only Mongo `orders`/`products`. It preserves checkpoint migration
tables, Mongo indexes and all unrelated tables/collections. Bosch X100 is seeded
with SKU `E2E-BOSCH-X100`, category DRILL, price 99 and **stock 10**.
No development or production data is copied. The ORDER scenario needs no RAG
documents; PostgreSQL RAG connections are also pointed to the E2E database,
but no RAG schema/fixtures are seeded in this step.

Install the browser once:

```sh
cd frontend
npm install
npx playwright install chromium
# If Linux reports missing browser libraries, install the OS dependencies:
# npx playwright install-deps chromium
```

Use a shell with local PostgreSQL connection credentials and the working Ollama
URL. Settings does not load env files automatically. The E2E PostgreSQL URL
defaults to `POSTGRES_URL` with its database replaced by `stockpilot_e2e`.
Mongo defaults to the local replica-set URI. Optional `E2E_POSTGRES_URL` and
`E2E_MONGO_URI` override only the connection; they must pass strict target checks.
Do not configure remote production infrastructure.

```sh
# Repository root; this file is private and ignored by Git.
set -a
source .env.local
set +a
# Only when Ollama runs on the Windows host:
# source scripts/dev-env.sh
cd frontend
npm run test:e2e
# Optional interactive runner; restart the runner for a fresh reset:
npm run test:e2e:ui
```

The launcher overrides `MONGO_DATABASE`, `POSTGRES_URL`,
`LANGGRAPH_CHECKPOINT_DB_URI`, `API_PROXY_TARGET` and `VITE_API_BASE_URL`; normal
development values cannot direct browser calls/checkpoints to development stores.
It generates a secret once per invocation and preserves it across Playwright
worker config evaluation. No developer JWT secret is needed.
The test invokes the existing backend `scripts/create_customer_token.py` to mint
a 30-minute JWT for a fresh `e2e-customer-<UUID>`. It defaults to the root
`.venv/bin/python`; set `E2E_PYTHON` to a Python executable with StockPilot and
its dependencies installed if needed (including on Windows).
The signing secret and generated JWT are never hardcoded or logged.
Avoid enabling Playwright debug/API logging, which can record token input.
Screenshots, traces and videos are deliberately disabled.

The scenario connects through the UI, sends `Commande-moi une Bosch X100`,
checks preparation and explicit confirmation, clicks **Confirmer** (sending
`Oui je confirme`), checks
the created order directly in Mongo, opens My Orders and the CREATED detail page,
checks its timeline and manual refresh, then returns to Assistant
and asks `Quel est le statut de ma dernière commande ?`. It checks that all
assistant requests share the same generated thread, that the listing displays
the created order, and that ORDER_STATUS matches the stored Mongo status.
It also rejects console errors and uncaught browser exceptions.

`e2e/support/backend_probe.py` refuses non-E2E targets and performs **read-only**
checks: exactly one matching Bosch X100 with **stock 10**, no previous orders for this new customer, and direct
verification of the owned order after creation/status lookup. It does not import
`main.py`, create indexes, seed fixtures or mutate stock.

Each success leaves exactly one E2E order, stock **9** and its checkpoint for
inspection. The next `npm run test:e2e` resets these stores to stock 10 with no
orders/checkpoint payloads. The test asserts both initial and remaining stock.
There is no broad database drop or back-office cancellation.

### Reset safety

All destructive operations require the exact database name `stockpilot_e2e`,
not merely a name containing `e2e`. A Mongo URI naming another database is
rejected; an explicit PostgreSQL override must name `stockpilot_e2e`.
The connected PostgreSQL database and Mongo database are rechecked before reset.
No `DROP DATABASE`, cascading truncation or arbitrary collection cleanup is used.
The only operation on PostgreSQL's admin database is creating `stockpilot_e2e`
if needed; the account needs database creation permission for first setup.

A PostgreSQL advisory lock is held across initialization **and the running
backend**. Another initializer fails instead of resetting an active managed run.
Use only this launcher for E2E servers; the lock cannot protect a manually started
server that ignores this convention. Reserved ports must be available.
Mongo must be a writable replica-set primary. Ollama is checked before reset,
and connection/setup failures are explicit, not skips or mocked successes.
The Mongo and PostgreSQL resets are not one cross-database transaction:
if setup fails partway, no backend is served and the next reset repairs E2E state.

An explicit standalone reset (stop any manually started E2E server first):

```sh
# Repository root, with local connection configuration exported:
.venv/bin/python scripts/init_e2e_env.py
```

This is a real-AI E2E test, separate from Vitest and from the golden AI evals.
The classifier's interpretation and Ollama latency can cause failures. Each
assistant response gets up to 120 seconds, with a 240-second scenario timeout.
The test does not rewrite fixtures or weaken assertions to hide those failures.
No E2E CI job is added in this step.
Run three separate `npm run test:e2e` invocations to validate reset repeatability;
`--repeat-each=3` alone does not rerun webServer initialization between tests.

An HTTP 500 on the first assistant message can indicate an unreachable Ollama,
even if `/health` returns 200. On WSL, `localhost:11434` and the Windows host
address are not necessarily interchangeable; check the existing backend
`scripts/dev-env.sh` configuration rather than modifying frontend contracts.
