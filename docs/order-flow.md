# ORDER confirmation, transactions and idempotency

[Back to README](../README.md) · [Database](database.md) · [Architecture](architecture.md)

## Multi-turn flow

```text
ORDER message -> PrepareOrderUseCase -> PENDING_CONFIRMATION
  -> explicit user confirmation -> FinalizeOrderUseCase
  -> Mongo transaction (reserve stock + create order) -> CONFIRMED
```

ORDER classification extracts product/quantity. A valid preparation creates a
pending order with an `operation_id` UUID and asks for explicit confirmation.
Keep the same verified customer identity and `thread_id` for preparation and confirmation; PostgreSQL restores
pending state across restarts. The confirmation resolver runs before ordinary
intent routing. Preparation does not itself finalize stock/order writes.

```sh
curl -X POST http://localhost:8000/assistant -H "Authorization: Bearer $CUSTOMER_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"message":"Commande-moi 2 Bosch X100","thread_id":"demo-order"}'
curl -X POST http://localhost:8000/assistant -H "Authorization: Bearer $CUSTOMER_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"message":"Oui, je confirme","thread_id":"demo-order"}'
```

These commands modify **demo** inventory on confirmation. A new valid preparation
is a new operation; idempotency does not equate every order from one thread.
`operation_id` identifies the business operation, while `order_id` identifies
the persisted order.

### Reserved product identity

Stock mutations resolve the complete literal product name, case-insensitively,
and reject multiple matching documents even if only one has sufficient stock.
The confirmation transaction decrements that document by Mongo `_id` and stores
its native `_id` as `reserved_product_id` in the order document, atomically with
order insertion. This persistence-only reference is not part of the domain
entity, workflow state or public API. SKU is not used as a stock identity:
V4.1 requires a unique SKU index for metadata administration, but does not
replace the existing reserved BSON identity or change order transactions.
The normal database's SKU index preparation remains pending.

Cancellation restores stock only through the saved `_id`, including after a
product rename. A missing referenced document or invalid reference fails without
falling back to a name or SKU. For legacy orders without this field, cancellation
requires exactly one candidate under the old reservation regex in the transaction
snapshot, and that candidate must also match the complete name. An exact match
alongside a similar name is not proof of the historical reservation identity.

V4.1 metadata renaming leaves persisted order snapshots and checkpoints untouched.
An already prepared confirmation still resolves its old product name and may
fail after rename. No aliases, automatic pending-order rewrites or new rename
business restriction are introduced. Legacy cancellations retain the stricter
historical-name proof requirements above. Product stock is read-only in the UI
and never changed by create/edit (creation starts at zero).
Missing, nonexact or ambiguous matches fail before any stock, status or event
mutation. Legacy reads remain unchanged. No migration or new index is required.

### Confirmation replay after cancellation

Replaying a pending confirmation with the same `operation_id` returns the
existing order without stock, status or event writes. If Mongo returns a
`CANCELLED` order, the conversation reports that it already exists but is
cancelled, and both `order_status` and `pending_order.status` are `CANCELLED`.
The original order ID remains in `created_order_id` and `last_created_order_id`
for subsequent owned lookup. No new status or response field is introduced;
initial confirmations and active-order replays retain their existing behavior.

## Customer ownership boundary

Every `/assistant` and `GET /orders` call requires an `Authorization: Bearer`
JWT, including advice and stock requests. The API verifies an HS256 signature and a required
numeric expiration before accepting the nonblank string `customer_id` claim.
Surrounding whitespace is stripped; the remaining opaque identifier is
case-sensitive. The token generator defaults to a one-hour lifetime.

The JWT is signed using `CUSTOMER_JWT_SECRET`, which must remain private and
match the trusted token issuer. The API rejects missing, malformed, expired or
invalidly signed tokens. `X-Customer-Id` and body fields are ignored and cannot
override the signed identity.
The back-office Bearer token remains independent and protects only the two
global technical mutation routes.

HTTP injects the customer into workflow state; message/classifier/body fields
cannot override it. Preparation remembers the owner in `pending_order`;
confirmation propagates it to `FinalizeOrderUseCase` and Mongo `Order.customer_id`.
The confirmation resolver rejects a changed pending owner even for direct
graph calls. Customer IDs are not added to logs, span attributes or metrics.

The checkpoint key is `customer-thread-v1:` plus SHA-256 of an unambiguous JSON
pair `[customer_id, thread_id]`. Customers can reuse a raw thread ID without
sharing pending confirmations, created IDs or lookup results. Keep both values
stable across restarts. Existing raw-thread checkpoints are deliberately
inaccessible from HTTP; there is no checkpoint migration or ownership backfill.

`Order.customer_id` defaults to `None` so older documents/fixtures deserialize.
Technical Python `CreateOrderUseCase`/`FinalizeOrderUseCase` calls may omit it to
create explicitly unowned legacy orders; provided blank IDs are rejected.
These technical calls are not customer-facing HTTP endpoints. HTTP creation
always supplies a nonempty customer. Orders with missing, null or blank owners
cannot be consulted by any customer. Privileged back-office mutations remain
global and their existing `OrderResponse` does not expose the owner.

## ORDER_STATUS read-only lookup

Ask "Où en est ma commande ?" or "Quel est le statut de ma dernière commande ?"
in the same `thread_id`, or supply an explicit order identifier:

```sh
# Replace this illustrative UUID with a real stored order_id:
curl -X POST http://localhost:8000/assistant -H "Authorization: Bearer $CUSTOMER_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"message":"Quel est le statut de la commande 47d8fbf9-1234-4567-89ab-123456789abc ?","thread_id":"demo-order"}'
```

The classifier returns ORDER_STATUS and optional `order_id`. The node selects
that explicit ID first, otherwise `last_created_order_id` from a successful
confirmation. Older checkpoints can fall back to `created_order_id`.
Successful confirmations update the remembered ID; a later failed/cancelled
preparation does not erase it. An explicit unknown ID never falls back to another
order, and reading an explicit order does not replace the thread's last-created
order. The explicit extraction is reset on each classified message.

For ORDER_STATUS only, a targeted application guard clears an extracted
`order_id` if that same identifier is explicitly labelled `operation_id`,
`identifiant d'opération` or an unambiguous equivalent in the original message.
It does not scan all UUIDs, change other extracted fields or reject a different
ID explicitly referring to a command. Operation and order IDs are distinct.

`GetOrderStatusUseCase` reads Mongo through `OrderRepositoryPort.find_by_order_id`.
It returns a deterministic response without LLM generation, stock reservation,
order creation or changes to a pending confirmation:

| `order_status_result` | Meaning |
|---|---|
| `FOUND` | Nonblank request customer exactly matches the stored nonblank owner; ID/status come from that order |
| `NOT_FOUND` | Absent, wrong-owner or legacy unowned order; identical result/response shape with null status |
| `NO_ORDER_ASSOCIATED` | No explicit ID or known created order in this conversation |

`queried_order_status` is null for absent orders. The existing conversation
`order_status` is not overwritten by lookup results. Mongo stores
`OrderStatus` values; these are not the conversation's `CONFIRMED`.
Statuses change only through the technical lifecycle use case below, never
through LLM inference. Mongo errors propagate through
the existing API error handling rather than masquerading as NOT_FOUND.

There is no global order search. Explicit IDs
and cached last-created IDs undergo the same ownership check. NOT_FOUND may
echo the requested ID but never reveals whether a foreign order exists or its
status. No known ID still yields NO_ORDER_ASSOCIATED. Pre-feature checkpoints
are not resumed through the new HTTP namespace.

## Deterministic conversational continuity

Classification still reads only the current message. Before routing, a dedicated
`ContextResolutionUseCase` completes narrowly recognized follow-ups using the
checkpoint's last relevant task. No raw transcript is sent to classification.
The existing confirmation node runs first and still short-circuits the workflow.

| Previous task | Recognized follow-up | Result |
|---|---|---|
| ORDER, Bosch X100, quantity 2 (including insufficient stock) | `est ce que je peux demander 10` / `Et si j'en demande 10 ?` | ORDER, Bosch X100, quantity 10 |
| ORDER, quantity 2, product missing | `de Bosch X100` | ORDER, Bosch X100, quantity 2 |
| STOCK_CHECK, Bosch X100 | `Est-il disponible ?` | STOCK_CHECK, Bosch X100 |
| STOCK_CHECK, Bosch X100 | `Et la S500 ?` | STOCK_CHECK, Bosch S500 |
| PRODUCT_ADVICE, drill recommendation | `Et pour du béton ?` | Advice with the reference task and updated usage |

Current classifier fields always win; carry-over only fills missing fields.
UNKNOWN can be recovered only when the entire message matches the allowlist
for the previous intent. A recognized different intent is never overridden.
Model shorthand expands a missing product only if the previous brand/model
has an unambiguous two-part representation. No product catalog is invented.
Advice follow-ups support a short list of tooling materials and numeric euro
budgets, not arbitrary `Et ...` messages.

State additions: `last_relevant_intent`, `last_product_name`, `last_quantity`,
`last_category`, `last_budget`, `last_usage`, `last_advice_query` and
`context_customer_id`. `advice_query` is a transient resolved retrieval query.
For advice, the stored reference request does not grow on each follow-up:
the node sends that request plus current category/budget/usage and the new
precision, without assistant answers or previous user-message chains.
Standalone advice requests still use their original query.

Greetings/out-of-domain messages remain UNKNOWN and clear this carry-over
context. ORDER_STATUS is never completed using product or quantity context and
clears carry-over; its existing remembered-order lookup remains unchanged.
Completed/cancelled/failed confirmations cannot be reopened by a short fragment.
Explicit new complete orders default to the existing quantity behavior instead
of inheriting a previous quantity.

The memory belongs only to the existing authenticated customer/thread checkpoint,
with an additional owner check before using saved context. Legacy checkpoints
without these fields start with no carry-over; there is no migration or global
memory. Repreparing an order uses a new pending operation as before: it does not
confirm the old operation, reserve stock or change transaction/idempotency rules.
The pending confirmation payload is never used as a source for guessed context.

This is intentionally conservative: free-form ambiguous fragments, bare numbers,
unrecognized materials and broader anaphora require a complete request.
Tests in `tests/test_context_resolution.py` exercise the resolver and real graph
with mocked classification/storage, including insufficient stock, missing
product, confirmation, ORDER_STATUS, and thread/customer isolation.

## Authenticated customer order history

### Customer order detail

`GET /orders/{order_id}` uses the customer JWT dependency and
`GetCustomerOrderUseCase`. The repository queries
`{"order_id": order_id, "customer_id": validated_customer_id}` directly in Mongo.
The existing unique `order_id` index already selects at most one order; no new
index is needed. There is no global lookup followed by an application ownership
check, and no LLM invocation. ORDER_STATUS remains unchanged.

200 returns the existing `OrderResponse`: `order_id`, `product_name`, `quantity`,
`status`, `created_at`, `events`. Missing, foreign and unowned legacy orders all return the
same 404 `Commande introuvable (NOT_FOUND).` No owner or operation ID is exposed.
Missing/invalid JWT returns 401, missing auth configuration 503, and unexpected
technical errors use the existing logged/sanitized 500 middleware.

```sh
TOKEN=$(python scripts/create_customer_token.py local-client-a)
ORDER_ID='replace-with-owned-order-id'
curl --fail-with-body "http://localhost:8000/orders/$ORDER_ID" \
  -H "Authorization: Bearer $TOKEN"
```

The Vue route `/orders/:orderId` renders the current detail and recorded event timeline,
with manual refresh and 15-second polling while non-terminal. CANCELLED has a
separate cancellation view rather than implying later lifecycle stages occurred.
Event dates come only from the stored events; legacy orders show current status
and an unavailable-history notice, with no inferred earlier steps.
Forward lifecycle transitions remain back-office-only;
customers can cancel their own eligible orders as described below.

### Embedded order events

The domain `Order` has an immutable `events` tuple of `OrderEvent(status,
occurred_at, source)`. Mongo persists it as an ordered array inside the same
order document. The API returns it as an `events` array in `OrderResponse`,
including owned detail, list and mutation responses.

Sources are SYSTEM for insertion, BACKOFFICE for lifecycle PATCH and back-office
cancellation, CUSTOMER for JWT-scoped cancellation. Sources are selected by the
server, never accepted from a request body. Timestamps are server-generated UTC
ISO 8601 values; the creation event uses the actual `created_at`.

All insertion paths share `MongoOrderRepository.insert`: exactly one
CREATED/SYSTEM event is persisted with the new order. Replaying an operation
returns the stored order rather than reinserting events. A status transition uses
one conditional Mongo update with `$set` status and `$push` event, so they cannot
be partially applied. Same-status use-case calls perform no mutation; a direct
repository same-status call also performs no append.

CANCELLED is appended within the existing stock-restoration transaction and its
conditional order update. Rollback removes both status and event changes along
with stock changes. Transaction retries can rerun the callback, but only a
committed append survives; replay of a committed cancellation appends nothing.
Chronology is the persisted append order, not reconstructed from lifecycle.

Legacy documents without `events` read as `[]`; no migration/backfill is run.
A subsequent real transition appends only its new event, not invented historical
CREATED or intermediate events. Invalid recorded event fields fail explicitly
through existing error handling instead of being silently discarded.

Example detail fragment:

```json
{
  "events": [
    {"status": "CREATED", "occurred_at": "2026-10-10T10:02:00Z", "source": "SYSTEM"},
    {"status": "CANCELLED", "occurred_at": "2026-10-10T10:08:00Z", "source": "CUSTOMER"}
  ]
}
```

This is a minimal status history, not event sourcing or a tamper-proof audit.
It depends on synchronized application server clocks. No actor IDs, metadata,
separate collection, notifications or new index are introduced.

### Customer cancellation

`POST /customer/orders/{order_id}/cancel` accepts no body and uses the existing
customer JWT dependency. The separate `/orders/{order_id}/cancel` back-office
route and its authentication are unchanged; the client never invokes it.

Both routes share `CancelOrderUseCase` and the same Mongo cancellation transaction.
The client passes the verified token's `customer_id` as an explicit scope. Initial
lookup, transaction read and conditional order update all filter by
`order_id` plus `customer_id`. Missing, foreign and ownerless legacy orders return
the same 404; request bodies, query parameters and headers cannot override owner.
The scope is rechecked inside the transaction before any stock restoration.

200 returns the existing `OrderResponse`, with CANCELLED (also for an idempotent
replay). CREATED and PROCESSING restore exactly the ordered quantity; SHIPPED,
DELIVERED and concurrent status changes return 409. Stock restoration and status
update remain atomic, with the same rollback and retry behavior. Missing/invalid
JWT returns 401, unavailable JWT configuration 503, technical failures sanitized
500 with the existing request ID and server logging.

Order Detail offers an inline confirmation only for CREATED/PROCESSING. Opening
the confirmation pauses polling; returning refreshes detail and resumes polling
when applicable. Mutation and refresh cannot overlap. Success replaces the
detail immediately with CANCELLED and stops polling. No mutation is automatically
retried on error: a 409 or ambiguous technical response triggers a detail GET.
Until that GET succeeds, cancellation stays unavailable. If reconciliation fails,
the user must explicitly refresh. Aborting a browser request does not roll back
an already committed server transaction; reopening detail reads the true state.

`GET /orders?limit=20&offset=0` uses the same customer JWT dependency as
`/assistant`. Only the verified `customer_id` claim determines the owner;
query parameters and `X-Customer-Id` cannot select another customer.
Legacy orders with missing/null owners are excluded. There is no global listing
or back-office token alternative for this client endpoint.

`ListCustomerOrdersUseCase` calls `OrderRepositoryPort.find_by_customer_id`.
Mongo filters by the exact owner before sorting, skipping and limiting;
the application never loads all customers' orders to filter them.
Sorting is `created_at` descending, then `order_id` descending for equal dates.
Existing timestamps are stored as ISO 8601 strings; ordering uses their stored
representation (normal application creation uses UTC timestamps).

Pagination: limit defaults to 20, minimum 1, maximum 100; offset defaults to 0
and must be nonnegative. Invalid values return 422. Missing/invalid JWT returns
401; missing/invalid signing-secret configuration returns 503.
Unexpected database errors use the existing logged/sanitized 500 handling.
An empty page is a 200 response, not 404.

Response:

```json
{
  "items": [
    {
      "order_id": "example-order-id",
      "product_name": "Bosch X100",
      "quantity": 2,
      "status": "PROCESSING",
      "created_at": "2026-10-10T00:00:00Z"
    }
  ],
  "limit": 20,
  "offset": 0,
  "count": 1
}
```

`count` is the number of items in this page, not a total count. Response items
omit `customer_id` and `operation_id`.

```sh
# CUSTOMER_JWT_SECRET must match the private secret used by the running API.
TOKEN=$(python scripts/create_customer_token.py local-client-a)
curl --fail-with-body 'http://localhost:8000/orders' \
  -H "Authorization: Bearer $TOKEN"
curl --fail-with-body 'http://localhost:8000/orders?limit=20&offset=20' \
  -H "Authorization: Bearer $TOKEN"
```

Offset pagination is not a snapshot: concurrent inserts can shift page boundaries.
The [compound Mongo index](database.md#order-indexes) supports owner filtering and
sorting, but large offsets still require scanning skipped index entries.
There is no cursor pagination or total-count query. The local JWT security
limitations above still apply; never disclose signing secrets or bearer tokens.

## Technical order lifecycle

### Read-only administrative orders

`GET /backoffice/orders` and `GET /backoffice/orders/{order_id}` accept either a
validated server-side admin session or the existing `BackofficeBearer`.
An explicit Authorization header always goes through the original technical
checks; a customer JWT never grants administrative access, even with a cookie.
Technical callers retain their existing 401/503 behavior. These are global reads
in the current development environment, not multi-tenant endpoints.

The list accepts `limit` (default 20, 1..100) and `offset` (default 0, >=0).
`ListBackofficeOrdersUseCase` delegates to `OrderRepositoryPort.find_all`;
Mongo sorts by `created_at DESC, order_id DESC` before skip/limit, with no
owner filter. Response: `items`, `limit`, `offset`, `count = len(items)`,
without a total or `has_more`.

`GetBackofficeOrderUseCase` reuses `find_by_order_id`; absent orders return 404.
The separate `BackofficeOrderResponse` contains the existing order fields
(`order_id`, `product_name`, `quantity`, `status`, `created_at`, `events`) plus
nullable `customer_id`. Legacy owners remain null and absent events remain
empty; malformed events still fail explicitly. No `_id`, `reserved_product_id`
or `operation_id` is exposed. Timeline data is returned, not synthesized.

Neither read performs stock/order/event/index writes or invokes the workflow.
Customer GET routes retain their JWT/ownership checks and unchanged response
schemas. Vue `/admin/orders` and `/admin/orders/:orderId` render these reads
with customer IDs, nullable legacy owners, recorded timestamps, existing badge
and timeline, loading/error/empty states and offset pagination. The list has no
`has_more`; a full page can lead to an empty next page. The existing `/orders/*`
PATCH status and POST cancellation remain technical Bearer-only; the separate
cookie-only operational endpoints below reuse exactly the same business use cases.

### Operational administrator orders (V3)

`PATCH /backoffice/orders/{order_id}/status` accepts `{"status":"PROCESSING"}`;
`POST /backoffice/orders/{order_id}/cancel` has no body. Both require a live
server-side admin cookie session, exactly one trusted Origin and the existing V2
session-bound `X-Admin-CSRF` token. Authorization headers are rejected even with
a valid cookie; neither customer JWTs nor the technical Bearer grant access.
Reads retain their session/technical compatibility. Missing, expired and revoked
sessions return 401; Origin/CSRF violations 403; absent orders 404; forbidden or
concurrent changes 409; unsafe session configuration 503.

The routes call only `UpdateOrderStatusUseCase` and `CancelOrderUseCase`.
Progression is CREATED → PROCESSING → SHIPPED → DELIVERED, without jumps or
reverse transitions. Cancellation uses its separate endpoint, never PATCH to
move to CANCELLED. CREATED/PROCESSING can be cancelled, SHIPPED/DELIVERED cannot.
Same-status updates and repeated cancellations preserve the existing no-op
semantics (including an already CANCELLED status); no extra event/restock occurs.
The existing Mongo compare-and-swap and cancellation transaction remain the
only write implementations. Mutation responses use `BackofficeOrderResponse`,
including nullable customer ID and actual events, never internal identifiers.
Administrative events have source BACKOFFICE; customer behavior is unchanged.

Vue detail offers only the next transition, plus explicit confirm/decline
cancellation for CREATED/PROCESSING. Pending operations disable all mutations
and refresh, preventing duplicate/cross submissions. Status/events are replaced
only by validated server responses, never optimistic events or compensation.
Network/20-second timeout, invalid response, 409 and server failures block
actions and trigger a GET reconciliation. Failed reconciliation remains blocked;
only a reliable reread re-enables actions. No mutation is retried automatically.
403 remains blocked until manual refresh; 404 removes the detail; 401 removes it
and redirects to login. Route/unmount/logout abort pending requests and ignore
late responses; logout failure requires a fresh detail read before acting again.
Transport generations discard responses from a prior login/logout/expired
session, including late JSON parsing.

The real browser test creates orders through the authenticated customer
assistant preparation/confirmation flow, then changes them through cookie-only
admin UI and verifies stored BACKOFFICE events and stock restoration exactly
once with the existing read-only Mongo probe. Mongo/PostgreSQL/checkpoints remain
strictly `stockpilot_e2e`; no artificial order seeding, dev writes or global
cleanup. Because delivery consumes stock permanently, the `chromium-admin`
project depends on the existing `chromium` customer/catalogue project. A full
E2E run executes those regressions first; an isolated admin-only run uses
`npm run test:e2e -- --project=chromium-admin --no-deps admin.spec.ts`.

### Demo administrator sessions (V2)

`POST /backoffice/session/login` accepts only JSON `{username, password}`.
`GET /backoffice/session/me` verifies the server session and returns
`{username, csrf_token}`; `POST /backoffice/session/logout` invalidates it and
expires the cookie (204). Missing/expired sessions return 401, unsafe/missing
admin configuration 503, origin/CSRF violations 403, throttling 429. Validation
errors never echo submitted input. All `/backoffice/*` responses use `no-store`.

The server configuration contains an admin username and a salted PBKDF2-SHA256
verifier, not a plaintext password. Format:
`pbkdf2_sha256$600000$<16..64-byte lowercase hex salt>$<32-byte lowercase hex digest>`.
Only that algorithm/work factor is accepted. UTF-8 password verification and
username comparisons are constant-time. Login accepts 16..256 characters;
configuration is rejected if the verifier matches `BACKOFFICE_API_TOKEN` or
`CUSTOMER_JWT_SECRET`. These technical secrets are never admin login credentials.
See [setup](getting-started.md#demo-administrator).

Cookies contain 256-bit opaque random tokens, never JWTs/roles/passwords.
`ADMIN_SESSION_STORE=memory` keeps sessions and independent random CSRF tokens in
a locked process-local store for development/test only. Explicitly selecting
`mongo` stores hashed cookie identifiers with shared expiry, logout, rotation,
capacity and throttling on the existing Mongo replica set. See the
[durable local operator session contract](security/admin-sessions.md), including
configuration rollback and mixed-worker limitations. Default TTL is 1,800 seconds
(fixed expiry, no read-based renewal), maximum
100 active sessions; settings cap TTL at 86,400 and capacity at 1,000. Successful
login replaces the supplied session and its CSRF token; failed login leaves it
intact. Expired entries are pruned on access. Capacity exhaustion returns 503
without evicting another administrator. Memory configuration changes clear
sessions; Mongo fingerprint mismatches deny access under the current fleet
configuration and do not constitute permanent revocation.

Default cookie is `__Host-stockpilot_admin`, HttpOnly, Secure, SameSite=Strict,
Path=/, no Domain. HTTPS is required for admin session operations. An explicit
`ADMIN_ALLOW_LOCAL_HTTP=true` exception uses `stockpilot_admin_local` (HttpOnly,
non-Secure), only with a loopback HTTP trusted origin, loopback request host and
`DEPLOYMENT_ENVIRONMENT=development`. It is rejected in production; it cannot be
enabled for a non-loopback site. SameSite is defense-in-depth, **not** the CSRF
boundary.

Login, logout and V3 mutations require exactly one Origin matching `ADMIN_TRUSTED_ORIGIN`
(scheme, host and port; no trailing slash/path/query/userinfo). Missing, null,
duplicate and foreign origins fail. Login additionally requires JSON and exactly
one non-simple `X-Admin-CSRF: login` header: this pre-session sentinel is not a
secret, and cross-origin browsers cannot send it without a CORS preflight. No
admin CORS allowance is added. Logout and V3 mutations require `X-Admin-CSRF` equal to the
random token of that exact live session, returned by login/me. Tokens from another
session or an earlier login are rejected. Browser fetch alone does not override
these server checks.

Vue uses a separate fixed same-origin `/api/backoffice` fetch transport with
`credentials: include`, no Bearer and no configurable VITE remote target.
Customer fetch keeps `credentials: omit`. Guards call `/me` on every protected
navigation; a 401 during page reads redirects to login and removes displayed
data. Reload revalidates the cookie; logout failures stay visible rather than
claiming success. Password input is cleared on submission. Passwords/session
credentials are never put in browser storage or URLs; the HttpOnly cookie is
browser-managed and only the CSRF token is held in transient module memory.

Login throttling counts successful and failed credential checks: five attempts
per client IP per rolling 60 seconds, plus fifty globally. Request client identity
is supplied by the server, never a custom frontend identity header. Bounded
attempt buckets (maximum 1,024) and global deque reject new attempts at capacity;
old entries expire, successful login does not clear counters. Configure trusted
proxy forwarding narrowly; never trust arbitrary forwarded addresses.

**Demo/production limits:** one configured administrator, global orders, no
tenant/RBAC/MFA/recovery/audit trail. With `memory`, restarts invalidate sessions
and reset throttling, and each worker has independent sessions/counters. Mongo
shares those controls and preserves sessions across restarts, but does not define
SaaS permissions. Its import preview signatures remain process-local. SaaS
activation requires managed identity and tenant authorization. Enforce TLS and
trusted proxy settings, body/request
limits, CSP/XSS controls, durable audit and tenant authorization. XSS on the
same origin can still use a session; HttpOnly is not an XSS solution.

Persisted business statuses follow a strict sequence:

```text
CREATED -> PROCESSING -> SHIPPED -> DELIVERED
```

`domain/services/order_lifecycle.py` validates transitions. Skipping a stage,
moving backwards or leaving DELIVERED is rejected with
`InvalidOrderStatusTransitionError`. Repeating the current status returns the
existing order without writing. `PreparedOrderStatus` remains separate.

The technical entry point is `UpdateOrderStatusUseCase(order_repository)`.
A local caller can invoke
`await use_case.execute(order_id, OrderStatus.PROCESSING)`, using the
`OrderStatus` enum rather than plain strings. The HTTP back-office route below
delegates to this same use case; no user intent is added. It simulates business progression,
not real shipping or delivery.

An absent order returns `None`. Mongo atomically sets `status` and appends one
BACKOFFICE event when both `order_id` and the previously read status match. A concurrent change
or deletion raises `OrderStatusConflictError`: reread Mongo before deciding
whether to retry; there is no unconditional overwrite or automatic retry.
Other persistence errors propagate.

Existing string `"CREATED"` documents are compatible without migration.
Creation still starts at CREATED; idempotent creation replays return the existing
order with its current status instead of resetting it. ORDER_STATUS reads the
updated enum value directly from Mongo. Quantity, stock, operation ID, creation
time and conversation state are not changed by lifecycle updates.

Business cancellation uses the separate transactional path described below.
The existing conversational cancellation before confirmation is unchanged.

### Demo back-office HTTP endpoint

**`PATCH /orders/{order_id}/status` requires a shared Bearer token.**
Use only on trusted local/demo infrastructure; do not expose it as a
production back-office endpoint.
It performs no ownership, role or payment check and triggers no real delivery.

#### Back-office authentication

Export `BACKOFFICE_API_TOKEN` before starting/restarting the API. Settings reads
the environment explicitly, not an env file automatically. Generate a separate
secret rather than using a published example value:

```sh
export BACKOFFICE_API_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
# Start/restart the API in this environment; retain the same variable for curl.
```

Both technical order routes require
`Authorization: Bearer $BACKOFFICE_API_TOKEN`. The dependency compares token
bytes with a constant-time comparison, before invoking a business use case.
The setting uses Pydantic `SecretStr`; errors/logs never include the token.
Do not print it, commit it, use `curl -v` with it or enable Authorization header
capture in telemetry. If operationally enabling HTTP header capture, explicitly
sanitize `authorization`. Keep env files private and outside Git.

Missing/blank configuration disables only back-office routes with 503
(`Back-office authentication is not configured.`); there is no open fallback.
Missing, malformed or wrong credentials return 401 with `WWW-Authenticate: Bearer`
and generic detail. `/health` remains public; `/chat`, `/classify`, catalogue
and `/assistant` use the separate customer JWT authentication in explicit local
modes. Production modes close every sensitive API pending SaaS identity and
tenant isolation. See [the security architecture](security/saas-auth-architecture.md).
This is a shared local/demo API key, not user authentication: no identity,
ownership, roles, expiration or automated rotation. Use TLS on any non-loopback
connection to avoid exposing the credential in transit.

Request: `{"status":"PROCESSING"}`. Accepted values are CREATED, PROCESSING,
SHIPPED, DELIVERED and CANCELLED; valid values are still subject to domain transitions.
PATCH cannot transition into CANCELLED (409): use the dedicated cancel endpoint.
PATCH on an already CANCELLED order accepts only CANCELLED as a no-op.
Success (200) returns `order_id`, `product_name`, `quantity`, `status` and
`created_at` (ISO 8601). The operation identifier is not included.

| HTTP status | Meaning |
|---|---|
| 200 | Updated stored order, or no-op when status already matches |
| 401 | Missing or invalid Bearer credentials |
| 404 | Order identifier not found |
| 409 | Forbidden transition or concurrent change; reread before retrying |
| 422 | Missing, malformed or unsupported status/body |
| 500 | Unexpected technical error, logged and sanitized by the existing middleware |
| 503 | Back-office token configuration missing or blank |

The route owns HTTP validation/error mapping only. It does not implement
transitions, access Mongo directly, invoke the LLM or alter stock.

For an existing demo order initially at CREATED, replace the example ID with its
real `order_id`. Run from the project root:

```sh
ORDER_ID='replace-with-real-order-id'
curl --fail-with-body -X PATCH "http://localhost:8000/orders/$ORDER_ID/status" \
  -H "Authorization: Bearer $BACKOFFICE_API_TOKEN" \
  -H 'Content-Type: application/json' -d '{"status":"PROCESSING"}'
curl --fail-with-body -X PATCH "http://localhost:8000/orders/$ORDER_ID/status" \
  -H "Authorization: Bearer $BACKOFFICE_API_TOKEN" \
  -H 'Content-Type: application/json' -d '{"status":"SHIPPED"}'
curl --fail-with-body -X PATCH "http://localhost:8000/orders/$ORDER_ID/status" \
  -H "Authorization: Bearer $BACKOFFICE_API_TOKEN" \
  -H 'Content-Type: application/json' -d '{"status":"DELIVERED"}'
# ORDER_STATUS remains POST /assistant, not GET:
curl --fail-with-body -X POST http://localhost:8000/assistant -H "Authorization: Bearer $CUSTOMER_TOKEN" \
  -H 'Content-Type: application/json' \
  -d "{\"message\":\"Quel est le statut de la commande $ORDER_ID ?\",\"thread_id\":\"demo-order\"}"
```

The last response's `queried_order_status` should be DELIVERED, read from Mongo.
These PATCH calls mutate the real demo order: no automatic scheduler advances it.
Concurrent updates can conflict; do not bypass the transition policy to retry.

### Atomic business cancellation

`POST /orders/{order_id}/cancel` has no request body. Like the status PATCH,
it is a **Bearer-protected local/demo back-office endpoint**, not a production
customer-facing operation.

| Stored status | Cancellation |
|---|---|
| CREATED / PROCESSING | Restore reserved quantity and set CANCELLED atomically |
| CANCELLED | Return the existing order; no second stock increment |
| SHIPPED / DELIVERED | Reject with 409; no changes |

The route delegates to `CancelOrderUseCase`, then
`OrderRepositoryPort.cancel`. The Mongo adapter uses a session with
`with_transaction`, snapshot reads, majority writes and PRIMARY read preference:

1. Reread the order and validate its status inside each transaction attempt.
2. Return immediately if CANCELLED; reject a changed status as a conflict.
3. Increment the reserved product by its saved `_id` and stored order quantity;
   legacy orders require the safe identity resolution described above.
4. Conditionally set CANCELLED and append its event in that same session, then commit.

Missing products, invalid stored quantities, failed writes or unmatched conditional
updates abort the transaction. The stock increment cannot commit alone.
PyMongo owns transient transaction retries: each retry rereads the order, and
concurrent cancellations cannot commit a second restoration.
There is no fallback from a missing saved product identity to a name/SKU.
Ambiguous legacy identities fail before mutation.

200 returns the same `OrderResponse` fields as PATCH, including status CANCELLED.
Unknown order -> 404; non-cancellable status or concurrency conflict -> 409;
unexpected technical errors -> logged/sanitized 500. An exhausted ambiguous
commit is not evidence of rollback: reread Mongo before interpreting the result.
Retrying cancellation is safe because CANCELLED prevents another stock increment.
There is no separate compensation write, refund or payment logic.

Example for an existing CREATED or PROCESSING demo order:

```sh
ORDER_ID='replace-with-real-order-id'
curl --fail-with-body -X POST "http://localhost:8000/orders/$ORDER_ID/cancel" \
  -H "Authorization: Bearer $BACKOFFICE_API_TOKEN"
# Replay: same response, no second stock restitution.
curl --fail-with-body -X POST "http://localhost:8000/orders/$ORDER_ID/cancel" \
  -H "Authorization: Bearer $BACKOFFICE_API_TOKEN"
curl --fail-with-body -X POST http://localhost:8000/assistant -H "Authorization: Bearer $CUSTOMER_TOKEN" \
  -H 'Content-Type: application/json' \
  -d "{\"message\":\"Quel est le statut de la commande $ORDER_ID ?\",\"thread_id\":\"demo-order\"}"
```

ORDER_STATUS reads CANCELLED from Mongo. No LLM cancellation intent, LangGraph
transition or modification of the existing order-creation transaction is added.

## Administrative stock adjustments (V4)

These operations are independent of ORDER, lifecycle, embedded OrderEvents and
LLM/checkpoints. Metadata POST still creates stock zero; metadata PATCH remains
restricted to name/category/price, with immutable unique SKU. Stock adjustment
does not rename/rewrite products or orders.

| Endpoint | Contract |
|---|---|
| `POST /backoffice/products/{sku}/stock-adjustments` | Cookie session only, exact Origin and session CSRF; `operation_id`, `delta`, `reason` |
| `GET /backoffice/products/{sku}/stock-movements` | Protected session or existing technical read credential; limit 1..100, offset >=0 |
| `GET /backoffice/stock-adjustments/{operation_id}` | Cookie session only; reconciliation without a mutation |

Mutation/reconciliation reject explicit Authorization even with a cookie.
Customer JWT and technical credentials never grant adjustment access.
Delta is a strict nonzero integer in -1,000,000..1,000,000 (no bool/string/float);
reason is trimmed and 1..500 characters, and operation ID is canonical UUIDv4.
Extra fields, client timestamps/source/reference/stock values are rejected.
POST returns 200 with the stored StockMovement for both commit and matching
replay. Different SKU/delta/normalized reason on an existing operation is 409,
without a second write. Insufficient stock or ambiguous SKU is 409; missing
product 404; missing prerequisite index or unknown outcome 503; technical
transaction failure sanitized 500; invalid requests sanitized 422.

History returns items, limit, offset, count (page size), has_more, ordered by
occurred_at/movement_id descending. It reads limit+1 records, has no total and
offset pages are not a cross-request snapshot. Reconciliation 404 means only
**no visible movement**, not proof that an uncertain commit failed.

The dedicated Mongo adapter owns all sessions and writes through the repository
port. It rechecks the stable operation inside the transaction, increments exact
product stock conditionally and inserts its movement atomically. It shares the
existing Mongo error-label strategy: at most three attempts for a known
uncommitted `TransientTransactionError`; an unknown commit label takes
precedence. Ambiguous commit/connection failures reconcile the original
operation from the primary with majority read concern and validate payload.
They never blindly reapply the delta. Unresolved results remain unknown; there
is no compensation, automatic new operation or nontransactional fallback.
See [index activation](database.md#administrative-stock-movements-v4).

Vue `/admin/products/:sku/stock` previews delta, reason and estimated stock, then
requires explicit confirmation. Server response/history/current product replace
the estimate. Double submission, route/session stale responses and logout are
guarded. Before sending, the tab retains its stable operation ID and normalized
payload in sessionStorage (no credentials). It survives refresh/navigation and
an uncertain result blocks even another product's adjustment in that tab.
Reconciliation reads the operation **and current product**, never reposts.
Found matching operation proves commit; a definitive 409 plus no movement and
a successful product read allows a new preview. Unknown result plus 404 stays
blocked; manual verification reuses the same ID. Corrupt/unavailable persistence
fails closed. Browser abort/20-second timeout does not roll back server work.
Operators must resolve pending unknown operations before clearing local state.
No stock polling, admin identity/RBAC/audit hardening or new workflow is added.

## Atomic confirmation

`FinalizeOrderUseCase` constructs an order and delegates to `OrderTransactionPort`.
The infrastructure adapter owns all PyMongo sessions; domain/application code
does not import PyMongo. Mongo must be a healthy replica set, not standalone.

Confirmation first checks for an existing matching operation without a session.
For a new operation, `ClientSession.with_transaction()`:

1. Checks idempotency again inside each transaction attempt.
2. Reserves with one conditional `find_one_and_update`, requiring
   `stock >= quantity` and applying `$inc: {"stock": -quantity}`.
3. Inserts the order using the same session.
4. Commits both writes together with majority write concern.

Insufficient stock aborts without an order. Insertion failure rolls back the
decrement. A unique-key loser aborts before retrieving the winning order.
A matching replay returns the original order without another stock decrement;
different product/quantity/customer (including `None` versus a nonempty owner)
raises `IdempotencyConflictError` before returning the existing order. This
invariant applies to creation lookup, duplicate insertion, transaction retry and
commit reconciliation; a cross-owner replay never returns a foreign order or
commits an additional stock decrement.

**Atomicity** prevents partial stock/order writes; **idempotency** prevents
replaying a business operation as another order. Concurrent same-operation
transactions are protected by write-conflict retries, the in-transaction lookup
and the unique partial `orders_operation_id_unique` startup index.
Historical orders without string operation IDs are excluded by
`{"operation_id": {"$type": "string"}}`. Duplicate string IDs require manual
reconciliation and fail startup; no historical order is deleted.

## Retry and unknown outcome

PyMongo's recommended `with_transaction()` implementation retries
`TransientTransactionError` and `UnknownTransactionCommitResult`; there is no
additional application-level transaction retry loop.

If retries end with an uncertain commit, the adapter reconciles by `operation_id`.
An existing matching order is success. An unresolved outcome returns
`ORDER_OUTCOME_UNKNOWN`, **not a claim of rollback**. Manual reconciliation is
necessary; do not blindly start a new operation or compensate stock while the
original outcome is unknown. No stock compensation is performed.

`ReleaseStockUseCase`, `ReserveStockUseCase` and standalone `CreateOrderUseCase`
remain independently tested utilities, but are not wired into ORDER confirmation.
There is **no nontransactional fallback**. `COMPENSATION_FAILED` remains for
compatibility but is no longer emitted by this flow.

## Tests

Standard fakes/mocks do not contact Mongo:

`test_customer_ownership.py` covers header validation and trusted propagation,
same-thread/different-customer checkpoint isolation, old checkpoint exclusion,
explicit/implicit status denial with indistinguishable NOT_FOUND results,
legacy deserialization, and owner-aware duplicate/retry/reconciliation checks.
Lifecycle and cancellation tests also verify owner-preserving status updates.

```sh
.venv/bin/python -m pytest -q
```

Opt-in integration tests require a dedicated disposable test service, never the
development or DEMO stores. Start `docker/compose.ci.yml` under its own project name
(see [ci-cd.md](ci-cd.md#validation-locale-sans-toucher-les-services-devdemo)), export
`MONGO_TEST_URI` and `POSTGRES_TEST_URI` for it, then:

```sh
.venv/bin/python -m pytest -q -m "integration and not integration_optional" tests
```

Without those variables, or with an application endpoint, the fixtures refuse to run.
They require a writable `rs0` replica set with sessions and use a validated,
previously absent `stockpilot_transaction_test_<UUID>` database per test.
They check commit, rollback, concurrent confirmations/cancellations, native
reserved product identity and exact initial events. Cleanup deletes only the
test documents selected by `_id`; no database or collection is dropped.
Empty test databases and indexes remain. Neither `stockpilot` nor
`stockpilot_e2e` is used. The general integration selector can also select PostgreSQL
integration tests; see [checkpoint validation](database.md#checkpoint-validation).

## Limits and diagnostics

Synchronous PyMongo can block the async event loop. Persistence alone does not
serialize concurrent same-thread requests; transactions/idempotency protect
finalization, not every conversation transition. PostgreSQL checkpoints and
Mongo orders are not one distributed transaction.

Replica-set availability and commit reconciliation may fail. Atomicity does
not guarantee that the client immediately knows the result. Product lookup
retains regex matching for advice/preparation, while confirmation resolves a
complete literal name and cancellation uses the saved Mongo identity.
The local single-node configuration has no redundancy or production hardening.

[Database](database.md) retains replica-set status, backup/recovery, network and
index diagnostics. [Observability](observability.md) explains request/thread/
operation/order/trace correlation, replay/unknown-outcome events and metrics.
