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
