# StockPilot frontend MVP

Vue 3 + TypeScript + Vite, Composition API, vue-router and native fetch.
The customer endpoints `POST /assistant`, `GET /orders`,
`GET /orders/{order_id}` and `POST /customer/orders/{order_id}/cancel` are used.
The catalogue additionally uses public read-only `GET /products` and
`GET /products/{sku}`; no JWT is sent with these public requests.
The separate admin `/admin/login`, `/admin/orders`, `/admin/orders/:orderId` space
uses V2 server-side demo admin sessions, global reads and dedicated operational
order endpoints. The server's existing business rules remain authoritative.
The protected `/admin/dashboard` presents read-only operational counts and
bounded recent activity from `GET /backoffice/dashboard`.

## Operational administration

Configure admin username, derived password verifier and trusted browser origin
**on the backend only**; see [setup](../docs/getting-started.md#demo-administrator)
and [security/production limits](../docs/order-flow.md#demo-administrator-sessions-v2).
No VITE secret is required. `api/admin.ts` always uses same-origin
`/api/backoffice`, `credentials: include`, no customer JWT or technical Bearer.
Customer/public requests retain their existing cookie-free transport.

Routes verify `/backoffice/session/me` before protected navigation; expired
reads redirect to login. Reload revalidates the HttpOnly cookie. CSRF uses an
explicit login custom header plus exact server Origin checking and a random
session-bound token shared by logout and mutations. Password input clears on submission; no credentials
are stored in localStorage/sessionStorage or URLs. Only CSRF lives in module
memory. Logout invalidates the server session, not merely the displayed UI.

Lists show all owners, nullable legacy owner, product/quantity/date/status,
loading/error/empty states and offset pagination (20). The UI requests exact totals and `has_more` with `include_total=true`, while the legacy API shape remains available. Detail uses the existing
badge/timeline and records only server events. Detail offers the next legal
transition and explicit cancellation confirmation for CREATED/PROCESSING.
Pending operations disable mutations/refresh. Success replaces status/events
from the server; conflict, network/20-second timeout, invalid response or server
error blocks actions and GET-reconciles, without mutation retries or compensation.
Failed reconciliation stays blocked until a reliable manual refresh; 401
redirects to login, 404 clears data, 403 requires refresh. Route/logout/expiry
discard stale responses and abort pending requests. No admin polling is added.
The customer JWT panel is hidden in this separate space.

### Read-only operational dashboard (V5)

`/admin/dashboard` is protected by the existing backend-verified admin route
guard and session cookie; the dashboard endpoint additionally rejects public,
customer JWT and technical Bearer access. It uses the same fixed same-origin,
`credentials: include`, `cache: no-store` transport. Expired sessions redirect
to admin login. Refresh and navigation/unmount invalidate stale responses.

Order totals include legacy orders without an owner and show counts for each
stored lifecycle status. Product cards distinguish stock `> 0` from `<= 0` and
sum stock units without combining prices, currencies or defining a low-stock
threshold. The five recent orders link to admin detail; five recent stock
adjustments link to the product's complete movement history. Empty recent lists,
loading and sanitized retryable errors are explicit. Dashboard reads are
independent, so concurrent writes may briefly make metrics reflect slightly
different instants; the dashboard is not a transactional snapshot or full audit.

### Product metadata administration (V4.1)

The same navigation/guard protects `/admin/products`, `/admin/products/new` and
`/admin/products/:sku/edit`. List shows metadata/read-only stock, loading,
errors/empty/refresh and offset pagination with exact filtered totals and `has_more`.
Create accepts SKU/name/category/finite nonnegative price and initializes server
stock to zero. Edit sends only name/category/price and shows immutable SKU.
No DELETE, disable, stock input, UI library or global store.

Responses supply persisted values; submit and refresh are guarded against
overlap/double clicks. A successful creation stays disabled and links to edit.
Ambiguous/failed creation is **never reposted automatically**: GET the exact
submitted SKU. Found means creation remains blocked (do not claim ownership of a
concurrent create); missing after a reliable 404 permits only manual resubmission.
Failed reconciliation remains blocked until a successful manual reload.
Edit failures likewise reload persisted metadata before another action.
401 redirects/clears the session; abort/route/logout/timeouts discard stale work.
Cookie CSRF transport remains distinct from customer JWT requests.

Renaming preserves existing referenced orders' stock restitution by `_id`, but
can invalidate pending confirmations prepared by old name; the form warns about
this unchanged workflow behavior. Normal product metadata writes remain blocked
until the exact unique SKU index is explicitly provisioned by an authorized
operator; [preflight/provisioning](../docs/database.md#product-metadata-administration-and-sku-activation-v41).

### Administrative stock adjustments (V4)

Product list/edit link to the protected `/admin/products/:sku/stock` page.
Enter a strict signed nonzero integer delta (absolute maximum 1,000,000) and a
trimmed reason (1..500 characters). Preview is not a mutation: it shows estimated
stock, requires explicit confirmation and refuses a negative estimate. The
server remains authoritative under concurrent orders/adjustments.
Cookie-only POST with existing Origin/session CSRF writes one StockMovement.
GET history is paginated using `has_more`, with actual timestamp/reason/source
and stock-before/after, never invented order events.

One operation ID is generated and persisted with SKU/delta/reason in tab-local
sessionStorage **before** POST; no credentials are stored. Double clicks are
guarded. Network/timeout/invalid response/server failures never retry POST.
The UI GET-reconciles that operation plus current product/history. An unresolved
operation remains blocked even after reload/route changes; verification sends
only GET and retains its original ID. A 404 is not evidence of rollback.
A proven matching commit plus successful rereads re-enables actions; definitive
409 rejection is checked separately. Corrupt storage stays blocked, unavailable
storage prevents sending. Logout/route/unmount/session epochs discard stale work.
An abort does not compensate a server commit. The reason is operational data:
avoid sensitive personal information in it, and resolve unknown operations
before manually clearing tab storage.

Normal stock adjustments require the explicitly authorized
[unique operation index](../docs/database.md#administrative-stock-movements-v4),
which is now provisioned on `stockpilot`. No index is created at normal startup;
the guarded E2E initializer separately provisions its isolated test indexes.
Product metadata PATCH still cannot modify stock/SKU; new products still start
at zero.

### CREATE_ONLY CSV product imports (V6)

`/admin/products/import`, linked from products, uses the existing session guard
and fixed cookie/CSRF transport. Select a nonempty file (UI default maximum
2 MiB), preview it, inspect counts/physical starting lines/errors/existing SKU
conflicts, then check explicit confirmation. Changing the file invalidates the
preview and checkbox. The server validates content, not extension/MIME, with
500 products maximum and bounded errors; its limits remain authoritative.
The downloadable UTF-8 example and page explain exact columns, category enum,
comma/semicolon quoting and stock-zero CREATE_ONLY rules. Preview reserves nothing.
See [full CSV/API contract](../docs/database.md#create_only-product-csv-imports-v6).

Loading/double-click guards, abort/20-second timeout and epochs discard stale
responses. Only a validated actual server receipt reports success and links back
to the catalogue. Before POST, tab sessionStorage holds **only** stable UUID and
SHA-256 file fingerprint, never CSV content, secrets or preview tokens. Network,
invalid-response/server errors and session changes retain that operation;
there is no automatic POST retry or replacement ID. Reload can GET-reconcile
without the file. Matching receipt proves completion; missing 404 stays blocked.
Corrupt/unavailable storage fails closed. Definitive validation/CSRF/conflict
refusals require a fresh preview. Browser abort is not server rollback.
Resolve unknown operations before manually clearing storage; closing a tab loses
that local marker but does not undo the server batch. Keep the displayed ID.

`chromium-import` depends on `chromium-dashboard` so all six earlier scenario
sources/seed-dependent assertions remain intact. It previews multiple products
without mutation, confirms through UI, checks actual Mongo zero stock and public
catalogue, rejects reimport duplicates and compares actual dashboard totals.
Only guarded E2E provisioning adds the new import receipt index; normal activation
waits for separate authorization.

The isolated admin browser scenario creates real orders via authenticated
customer assistant preparation/confirmation, then operates on them through admin
UI and verifies actual Mongo events/stock with the existing read-only probe:

```sh
# Root, with required local services already available:
source scripts/dev-env.sh >/dev/null
cd frontend
npm run test:e2e -- --project=chromium-admin --no-deps admin.spec.ts
# All seven scenarios: customer/catalogue first, then admin orders,
# product metadata, stock, dashboard and finally CSV import.
npm run test:e2e
```

The launcher generates ephemeral admin credentials/verifier in Node, passes only
the verifier to the FastAPI child, and explicitly selects local HTTP origins.
No admin secret is compiled into Vue. Existing order/catalogue scenarios remain
unchanged. Mongo/PostgreSQL are restricted to `stockpilot_e2e` by checked guards
and advisory locking; no global drop. Traces/screenshots/videos stay disabled.
Admin-only coverage includes route protection, real cookie login/reload/logout,
CSRF/Origin denial, technical read compatibility, client/technical mutation
denial, empty pagination, missing detail, every lifecycle step, cancellation,
replay/restock once and terminal denial. Stored BACKOFFICE events and final stock
(one delivered reservation consumed) are asserted. Legacy owners, concurrency,
expiry/rotation, uncertain outcomes and brute force are covered in backend/Vitest.
`chromium-products` depends on `chromium-admin`; its real scenario creates/edits
metadata, checks Mongo identity/stock zero and public reads, refuses stock/SKU/
deletion/CSRF violations, races duplicate creation against the actual Mongo unique
constraint, and exercises customer ORDER refusal on the new zero-stock product.
`chromium-stock` depends on `chromium-products` so all four historical scenarios
stay unchanged. Its additional scenario previews/confirms positive/negative stock adjustments, rejects
insufficient stock in UI/server, replays an identical operation without another
movement, and reserves/cancels a real assistant order against adjusted stock.
Read-only Mongo probes assert movements, stock, order events and internal product
identity. `chromium-dashboard` depends on `chromium-stock`: it compares dashboard
counts and recent results with independent read-only Mongo probes, creates and
evolves a real order through the existing assistant/admin path, then records one
V4 stock adjustment and verifies it after a dashboard refresh. Only `stockpilot_e2e`
gets SKU/stock-movement indexes from the guarded initializer.

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
  pages/               Assistant and Mes commandes page shells
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
20; `count` is the page size, not a total. With `include_total=true`, paginated
responses additionally expose the exact `total` and `has_more`; existing API
clients can continue using the unchanged default response.

Each history card and identified assistant summary links to its detail page.
The page loads the current owned order directly through the customer API, not
through the LLM. It renders product, quantity, identifier, creation date and
status, plus a timeline of recorded `events`, in their persisted order. Each event
shows status, formatted timestamp and source SYSTEM/BACKOFFICE/CUSTOMER. CANCELLED
shows cancellation without suggesting subsequent stages were reached.
Legacy orders with absent/empty events show their current status and an
unavailable-history notice; neither earlier steps nor timestamps are inferred.

The detail page polls 15 seconds **after each completed successful request**,
only while mounted and non-terminal, without overlapping requests. DELIVERED,
CANCELLED and errors stop polling; manual refresh remains available. Changing
the route/JWT aborts stale requests and clears the old detail. 404 does not
distinguish foreign, legacy and nonexistent orders. A request already processed
by the server is not rolled back by browser cancellation.

### Local status notifications

Order Detail compares each successful GET status with the last successfully
loaded status for the current order/session. Initial reads and unchanged statuses
never notify. Later changed statuses (polling, manual refresh or reconciliation)
show a page-local `OrderStatusNotification` with deterministic French messages.
Events are replaced from the API response, never synthesized for notifications.

The accessible `role="status"` message can be closed manually and expires after
8 seconds. A newer change replaces the notification and restarts its lifetime;
equal reads do not restart or recreate it. Changing JWT/order or leaving the page
cleans the notification and timer. DELIVERED/CANCELLED still stop polling while
allowing the final notification to finish. Successful direct cancellation keeps
its existing action feedback rather than producing a duplicate notification.

E2E uses an ephemeral back-office credential only in Playwright's Node process
to PATCH the dedicated E2E backend to PROCESSING. No browser code receives that
credential or gains a mutation action. The normal 15-second polling then updates
the badge, recorded timeline and notification. The final history is exactly
CREATED/SYSTEM, PROCESSING/BACKOFFICE, CANCELLED/CUSTOMER.

`/assistant` returns the existing partial LangGraph state, not a dedicated
Pydantic response DTO. Types are based on `schemas/chat.py`, `schemas/order.py`,
`workflows/state.py` and domain enums. The client checks JSON and the fields
needed to start rendering, not a complete generated runtime schema.
Assistant text is rendered as escaped plain text, not HTML or Markdown.
Structured response cards supplement that text: pending orders offer Confirm
and Cancel buttons, confirmed orders show their identifier and a Mes commandes link,
insufficient stock shows the requested quantity, and FOUND status lookups show a
badge for the exact supported status. Mes commandes uses the same status badge.
Cards remain in the local transcript; only the latest eligible preparation can
be acted on. Buttons send `Oui je confirme` or `Non annule` through `/assistant`
in the current thread, never a back-office request. Old cards, in-flight requests
and failed decision attempts cannot trigger another click-based confirmation.

The API does not provide structured available stock or product/quantity in
ORDER_STATUS; those values are not guessed from prose or stale workflow fields.
The insufficient-stock card points to the response text for availability.
The status card shows only the queried ID/status. A creation summary uses its
confirmed pending-order payload and ID, and links to Mes commandes for current status
instead of inventing a lifecycle field absent from that response.
ORDER remains conversational: enter the requested product/quantity, then follow
the backend's explicit confirmation prompt in the same thread.
No request is retried automatically, especially after ambiguous order failures.
Aborting a browser request does not roll back server-side work; consult order
history/status before submitting another confirmation.

## Validation

### Product catalogue

`/products` lists Mongo products in stable SKU order with limit 20 and offset
pagination. `/products/:sku` displays name, SKU, category, price and stock.
The list response retains `items`, `limit`, `offset` and `count` (page size)
and includes `has_more`. The UI opts into `include_total=true`: a filtered
Mongo `$facet` returns bounded items and the exact total in one aggregation.
Legacy requests retain the extra-document approach. Customer order history
also exposes exact owner-scoped totals when requested.
The backend exposes only these operational fields; no description, image or
currency is invented (the current model stores a numeric price without a
currency field). Availability is derived from stock > 0 and remains a snapshot.

`Demander à StockPilot` and `Commander` navigate to `/assistant?message=...`
and prefill the composer via Vue props. They never auto-send, change threads
or bypass confirmation. The advice draft includes the actual tooling category,
so it remains compatible with the existing supported-tooling classifier guard.
JWT is still required to send. Connecting a JWT preserves this catalogue draft;
switching customers clears the session and composer as before.
Query parameters contain only the product-based message, never credentials;
the draft may appear in browser history and can be edited before sending.
No cart, payment, catalogue recommendations or new RAG ingestion
is implemented. Unknown SKU returns 404; malformed catalog data fails explicitly.

The catalogue supports debounced search and explicit-submit combined filters: literal case-insensitive text in name
or SKU (`q`, max 100 characters), exact domain category and availability
(`true`: stock > 0; `false`: stock <= 0). Text searches apply after 400 ms;
Rechercher applies every field immediately. All fields are applied together and reset offset to zero.
The URL is the source of applied state (`q`, `category`, `available`, `offset`, `limit`);
refresh/back navigation restores it, and pagination preserves it. Reset clears
both submitted filters and unsent drafts. Invalid URL criteria show an error,
not silently broadened results. Filtered empty pages show a criteria-specific
empty state.

Product-card links carry only validated applied `q`, `category`, `available`
and `offset` parameters into the detail URL. `Retour au catalogue` restores
that context, including after a detail-page refresh; direct detail access
without context returns to `/products`. Unknown, repeated or invalid context
values are omitted individually from navigation links. The catalogue itself
retains its explicit invalid-URL error behavior. No `returnUrl`, global state
or browser storage is used. Native browser back navigation remains unchanged.
Card links include the product name for accessible identification. Assistant
links still carry only their prefilled message, not the catalogue context.

Categories reuse the existing typed category-label mapping for DRILL/SAW/SANDER/
SCREWDRIVER/OTHER, which corresponds to the fixed backend enum, rather than
deriving an incomplete list from a paginated response. No categories endpoint,
search engine or debounce dependency is introduced.

The isolated browser E2E first checks combined search/category/availability,
reset and URL restoration with Bosch X100 (DRILL, stock 10), Makita D200
(DRILL, stock 0) and Bosch S500 (SAW, stock 5). It then opens Bosch X100, clicks
Commander, sends the prefilled draft and uses the existing confirmation UI before
checking Mes commandes, order detail and ORDER_STATUS.

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
cursor pagination or production-grade administrative identity.

## Real browser E2E

Playwright preserves `e2e/order-flow.spec.ts`, `e2e/catalogue-navigation.spec.ts`,
`e2e/admin.spec.ts` and `e2e/admin-products.spec.ts`, then runs the dedicated
`e2e/admin-stock.spec.ts`, `e2e/admin-dashboard.spec.ts` and `e2e/admin-import.spec.ts` scenarios. It starts a
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
and resets only Mongo `orders`/`products`/`stock_movements`/`product_imports`. It preserves checkpoint migration
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

The order scenario connects through the UI, sends the catalogue draft
`Commande-moi 1 Bosch X100`,
checks preparation and explicit confirmation, clicks **Confirmer** (sending
`Oui je confirme`), checks
the created order directly in Mongo, opens Mes commandes and the CREATED detail page,
checks its timeline and manual refresh, then returns to Assistant
and asks `Quel est le statut de ma dernière commande ?`. It checks that all
assistant requests share the same generated thread, that the listing displays
the created order, and that ORDER_STATUS matches the stored Mongo status.
It also rejects console errors and uncaught browser exceptions.
The catalogue scenario covers detail reload, return link, native browser back,
direct access and offset preservation using the same three products. It checks
six real list responses for `has_more` across limits, offset and DRILL/availability
filters, the unchanged detail contract, and the disabled Suivant button.

`e2e/support/backend_probe.py` refuses non-E2E targets and performs **read-only**
checks: exactly one matching Bosch X100 with **stock 10**, no previous orders for this new customer, and direct
verification of the owned order after creation/status lookup. It does not import
`main.py`, create indexes, seed fixtures or mutate stock.

Each success leaves exactly one CANCELLED E2E order, stock **10** and its checkpoint for
inspection. The next `npm run test:e2e` resets these stores to stock 10 with no
orders/checkpoint payloads. The test asserts initial stock 10, reserved stock 9,
then restored stock 10 after customer cancellation and again after reload.
The test also verifies CREATED/SYSTEM, PROCESSING/BACKOFFICE then CANCELLED/CUSTOMER events,
valid ordered timestamps, the rendered three-event timeline and unchanged persisted
timestamps after reload.
There is no broad database drop or back-office cancellation.

### Customer cancellation UX

Order Detail offers cancellation only for CREATED/PROCESSING, with an inline
confirmation and no immediate mutation on the first click. Buttons are disabled
during POST, duplicate clicks are guarded, and success updates the detail and
timeline to CANCELLED and stops polling.

A conflict or ambiguous technical failure never retries the POST automatically.
The page performs a GET to reconcile the true status; another action is offered
only after a successful read. Failed reconciliation requires manual Actualiser.
Changing JWT, changing route or unmounting aborts outstanding requests and ignores
their stale results, but cannot undo a server-side transaction already committed.
Mes commandes reads CANCELLED naturally on its next load. No payment/refund logic,
new library or back-office client capability is introduced.

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


## V9 UI/UX overhaul

The shared design uses a light surface, indigo navigation/actions, 4 px spacing,
44 px controls, a responsive header and a dedicated admin sidebar. Existing
cards, error notices, timelines and confirmation flows reuse the common styles.
Public/admin products support combined literal name/SKU, category and availability
filters. Admin orders search literal substrings of order ID, product and customer ID.
Search is debounced by 400 ms; route changes abort pending reads and discard stale
responses. Product filters and pagination remain in the URL, including the public
10/20/50/100 page size. Changed filters reset the offset; invalid/outdated offsets
are validated or brought back to the last available page.

Pagination requests `include_total=true`; default API responses remain compatible.
`total` and bounded items share one Mongo `$match`/`$facet`; no frontend full scan,
index activation, migration or workflow change is introduced. Stable sorts include
`_id` as a final tie-breaker. Totals refresh with each read after filters/mutations;
separate pages are live views, not a cross-page database snapshot.

Run the existing browser suite without resetting or seeding retained stores:

```sh
# Root; preserve the private local environment without printing it.
source scripts/dev-env.sh >/dev/null
cd frontend
E2E_NO_RESET=1 PYTHONDONTWRITEBYTECODE=1 npm run test:e2e
PYTHONDONTWRITEBYTECODE=1 npx playwright test --config playwright.visual.config.ts
```

The visual configuration always uses the guarded existing `stockpilot_e2e` stores.
The interaction scenario appends a uniquely owned 23-product CSV batch through the
real authenticated UI to test several pages; it never resets existing data. All
screens are captured at 1440/768/390 px. Credentials/JWTs are never captured filled.
The existing suite retains its original reset mode when `E2E_NO_RESET` is absent;
use the explicit no-reset command above for this task. Its no-reset assertions use
real initial stock and unique product SKUs rather than rewriting retained fixtures.
Review captures with `artifacts/ui-ux/index.html` and see the delivery report in
`docs/archive/frontend-ux-overhaul.md`. Screenshots are local review artifacts ignored by Git.
