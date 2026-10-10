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
Keep the same `thread_id` for preparation and confirmation; PostgreSQL restores
pending state across restarts. The confirmation resolver runs before ordinary
intent routing. Preparation does not itself finalize stock/order writes.

```sh
curl -X POST http://localhost:8000/assistant \
  -H 'Content-Type: application/json' \
  -d '{"message":"Commande-moi 2 Bosch X100","thread_id":"demo-order"}'
curl -X POST http://localhost:8000/assistant \
  -H 'Content-Type: application/json' \
  -d '{"message":"Oui, je confirme","thread_id":"demo-order"}'
```

These commands modify **demo** inventory on confirmation. A new valid preparation
is a new operation; idempotency does not equate every order from one thread.
`operation_id` identifies the business operation, while `order_id` identifies
the persisted order.

## ORDER_STATUS read-only lookup

Ask "Où en est ma commande ?" or "Quel est le statut de ma dernière commande ?"
in the same `thread_id`, or supply an explicit order identifier:

```sh
# Replace this illustrative UUID with a real stored order_id:
curl -X POST http://localhost:8000/assistant \
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
| `FOUND` | `queried_order_id` and `queried_order_status` come from the stored order |
| `NOT_FOUND` | No document for the selected ID; response explicitly includes NOT_FOUND |
| `NO_ORDER_ASSOCIATED` | No explicit ID or known created order in this conversation |

`queried_order_status` is null for absent orders. The existing conversation
`order_status` is not overwritten by lookup results. Mongo stores
`OrderStatus` values; these are not the conversation's `CONFIRMED`.
Statuses change only through the technical lifecycle use case below, never
through LLM inference. Mongo errors propagate through
the existing API error handling rather than masquerading as NOT_FOUND.

There is no global/latest-order search, customer identity or authentication.
An explicit ID can be queried without an ownership check: this remains a local
demo, not a secure customer-facing tracking endpoint. Pre-feature checkpoints
cannot recover a created ID that was already cleared before this feature.

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
and generic detail. `/assistant`, `/health`, `/chat` and `/classify` remain public.
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
curl --fail-with-body -X POST http://localhost:8000/assistant \
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
curl --fail-with-body -X POST http://localhost:8000/assistant \
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
different product/quantity raises `IdempotencyConflictError`.

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
