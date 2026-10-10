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
`status`, `created_at`. Missing, foreign and unowned legacy orders all return the
same 404 `Commande introuvable (NOT_FOUND).` No owner or operation ID is exposed.
Missing/invalid JWT returns 401, missing auth configuration 503, and unexpected
technical errors use the existing logged/sanitized 500 middleware.

```sh
TOKEN=$(python scripts/create_customer_token.py local-client-a)
ORDER_ID='replace-with-owned-order-id'
curl --fail-with-body "http://localhost:8000/orders/$ORDER_ID" \
  -H "Authorization: Bearer $TOKEN"
```

The Vue route `/orders/:orderId` renders the current detail and status timeline,
with manual refresh and 15-second polling while non-terminal. CANCELLED has a
separate cancellation view rather than implying later lifecycle stages occurred.
No stage timestamps are stored/exposed, so the timeline shows only progress,
not invented event dates. Only back-office routes can change lifecycle status.

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

An absent order returns `None`. Mongo atomically updates only the `status` field
when both `order_id` and the previously read status match. A concurrent change
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
and generic detail. `/health`, `/chat` and `/classify` remain public;
`/assistant` uses separate customer JWT authentication.
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
3. Increment the existing product stock by the stored order quantity.
4. Conditionally set the order to CANCELLED in that same session, then commit.

Missing products, invalid stored quantities, failed writes or unmatched conditional
updates abort the transaction. The stock increment cannot commit alone.
PyMongo owns transient transaction retries: each retry rereads the order, and
concurrent cancellations cannot commit a second restoration.
Product matching retains the existing canonical-name lookup convention; unique,
unambiguous product names remain an application assumption.

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

After manually starting a healthy demo replica set, opt-in integration tests:

```sh
export MONGO_TEST_URI='mongodb://localhost:27017/?replicaSet=rs0&directConnection=true'
.venv/bin/python -m pytest -q -m integration
```

They use a unique generated database to check commit, rollback and concurrent
confirmations, then drop **only their own database**, never `stockpilot` or
historical data. The general integration selector can also select PostgreSQL
integration tests; see [checkpoint validation](database.md#checkpoint-validation).

## Limits and diagnostics

Synchronous PyMongo can block the async event loop. Persistence alone does not
serialize concurrent same-thread requests; transactions/idempotency protect
finalization, not every conversation transition. PostgreSQL checkpoints and
Mongo orders are not one distributed transaction.

Replica-set availability and commit reconciliation may fail. Atomicity does
not guarantee that the client immediately knows the result. Product lookup
assumes an unambiguous canonical name under the existing regex strategy.
The local single-node configuration has no redundancy or production hardening.

[Database](database.md) retains replica-set status, backup/recovery, network and
index diagnostics. [Observability](observability.md) explains request/thread/
operation/order/trace correlation, replay/unknown-outcome events and metrics.
