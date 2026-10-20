"""Failure responses injected around actual Mongo transactions, not memory stock."""

import os
from datetime import UTC, datetime

import pytest
from pymongo import MongoClient
from pymongo.client_session import ClientSession
from pymongo.errors import ConnectionFailure, OperationFailure
from test_tenant_orders_mongo_integration import (
    REQUESTS,
    cancel,
    confirm,
    confirm_command,
    context,
    facts,
    prepare,
)
from test_tenant_orders_mongo_integration import db as db  # noqa: PLC0414
from test_tenant_orders_mongo_integration import orders as orders  # noqa: PLC0414

from stockpilot.application.ports.tenant_authorization import TenantContextUnavailable
from stockpilot.application.ports.tenant_orders import (
    TenantOrderConflict,
    TenantOrderNotFound,
    TenantOrderOutcomeUnknown,
    TenantOrdersUnavailable,
)
from stockpilot.application.services.identity_authorization import AuthorizationDenied
from stockpilot.domain.entities.tenant_order import TenantOrderStatus as S
from stockpilot.infrastructure.persistence.mongodb.schema_migrations import (
    MigrationError,
    MigrationRunner,
    registry,
)
from stockpilot.infrastructure.persistence.mongodb.tenant_orders import (
    MongoBuyerAssociations,
    MongoTenantOrders,
)

pytestmark = pytest.mark.integration


def test_new_client_recovers_durable_ledger(db, orders):
    draft = prepare(orders)
    command = confirm_command(draft, "durable")
    confirm(orders, draft, operation="durable")
    with MongoClient(os.environ["MONGO_TEST_URI"]) as client:
        restarted = MongoTenantOrders(client[db.name], orders[0].authorization)
        assert (
            restarted.confirm(REQUESTS["a1"], context(orders), command).status
            is S.RESERVED
        )
    assert facts(db) == (10, 1, 2)


def test_connection_lost_after_real_commit_reconciles(db, orders, monkeypatch):
    draft = prepare(orders)
    original = ClientSession.commit_transaction
    first = [True]

    def lost(session):
        result = original(session)
        if first[0]:
            first[0] = False
            raise ConnectionFailure("synthetic connection lost after commit")
        return result

    monkeypatch.setattr(ClientSession, "commit_transaction", lost)
    assert confirm(orders, draft).status is S.RESERVED
    assert facts(db) == (10, 1, 2)


def test_unknown_with_no_observed_commit_is_not_assumed_aborted(
    db, orders, monkeypatch
):
    draft = prepare(orders)
    command = confirm_command(draft, "uncertain")

    def unknown(session):
        raise OperationFailure(
            "synthetic unobserved commit",
            details={"errorLabels": ["UnknownTransactionCommitResult"]},
        )

    with monkeypatch.context() as scoped:
        scoped.setattr(ClientSession, "commit_transaction", unknown)
        with pytest.raises(TenantOrderOutcomeUnknown):
            orders[0].confirm(REQUESTS["a1"], context(orders), command)
    # The injected test session was aborted, but the adapter could not infer that.
    assert facts(db) == (10, 0, 1)
    assert (
        orders[0].confirm(REQUESTS["a1"], context(orders), command).status is S.RESERVED
    )
    assert facts(db) == (10, 1, 2)


def test_transient_commit_aborts_all_stock_then_retries_safe(db, orders, monkeypatch):
    draft = prepare(orders)
    original = ClientSession.commit_transaction
    count = [0]

    def conflict(session):
        count[0] += 1
        if count[0] == 1:
            raise OperationFailure(
                "synthetic commit write conflict",
                code=112,
                details={"errorLabels": ["TransientTransactionError"]},
            )
        return original(session)

    monkeypatch.setattr(ClientSession, "commit_transaction", conflict)
    assert confirm(orders, draft).status is S.RESERVED
    assert count[0] == 2
    assert facts(db) == (10, 1, 2)


def test_bounded_transient_failures_leave_no_order_or_stock_mutation(
    db, orders, monkeypatch
):
    draft = prepare(orders)
    count = [0]

    def conflict(principal, session):
        count[0] += 1
        raise OperationFailure(
            "synthetic conflict",
            code=112,
            details={"errorLabels": ["TransientTransactionError"]},
        )

    monkeypatch.setattr(orders[0], "_identity", conflict)
    with pytest.raises(TenantOrdersUnavailable):
        confirm(orders, draft)
    assert count[0] == 4 and facts(db) == (10, 0, 1)


def test_storage_and_identity_outages_are_technical_not_denials(
    db, orders, monkeypatch
):
    draft = prepare(orders)
    with MongoClient(
        "mongodb://127.0.0.1:1", serverSelectionTimeoutMS=100, connectTimeoutMS=100
    ) as client:
        service = MongoTenantOrders(client[db.name], orders[0].authorization)
        with pytest.raises(TenantOrdersUnavailable) as error:
            service.confirm(REQUESTS["a1"], context(orders), confirm_command(draft))
        assert "127.0.0.1" not in str(error.value)
    ctx = context(orders)

    def outage(request):
        raise TenantContextUnavailable("private synthetic provider details")

    monkeypatch.setattr(orders[1], "current", outage)
    with pytest.raises(TenantOrdersUnavailable) as error:
        orders[0].confirm(REQUESTS["a1"], ctx, confirm_command(draft))
    assert "private" not in str(error.value)
    assert facts(db) == (10, 0, 1)


def test_untrusted_request_missing_or_forged_context_refused(db, orders):
    draft = prepare(orders)
    ctx = context(orders)
    for request, supplied in [
        (REQUESTS["a1"], None),
        ({"organization_id": "org-a"}, ctx),
        (REQUESTS["b1"], ctx),
    ]:
        with pytest.raises(AuthorizationDenied):
            orders[0].confirm(request, supplied, confirm_command(draft))
    assert facts(db) == (10, 0, 1)


def test_conflicting_confirm_and_cancel_fingerprints_do_not_mutate(db, orders):
    a, b = prepare(orders), prepare(orders)
    confirm(orders, a, operation="same")
    with pytest.raises(TenantOrderConflict):
        confirm(orders, b, operation="same")
    cancel(orders, a, operation="same")
    with pytest.raises(TenantOrderConflict):
        cancel(orders, b, operation="same")
    assert facts(db) == (10, 0, 3)


@pytest.mark.parametrize("target", ["user", "membership", "organization", "buyer"])
def test_revocation_race_invalidates_transaction_snapshot(
    db, orders, monkeypatch, target
):
    """Real write conflict when revocation wins after the snapshot starts."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    draft, ctx = prepare(orders), context(orders)
    entered, finish = Event(), Event()
    names = {
        "user": "b2b_users",
        "membership": "b2b_memberships",
        "organization": "b2b_organizations",
        "buyer": "b2b_buyers",
    }
    collection = db[names[target]]
    query = (
        {"organization_id": "org-a"} if target == "organization" else {"user_id": "a1"}
    )
    original = orders[0]._identity
    first = [True]

    def pause(principal, session):
        if first[0]:
            first[0] = False
            # Establish a real snapshot without locking target authorization docs.
            db.b2b_products.find_one({"organization_id": "org-a"}, session=session)
            entered.set()
            assert finish.wait(10)
        return original(principal, session)

    monkeypatch.setattr(orders[0], "_identity", pause)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            orders[0].confirm, REQUESTS["a1"], ctx, confirm_command(draft)
        )
        assert entered.wait(10)
        try:
            collection.update_one(
                query,
                {
                    "$set": {
                        "status": "disabled"
                        if target in ("user", "buyer")
                        else "suspended"
                    }
                },
            )
        finally:
            finish.set()
        with pytest.raises((AuthorizationDenied, TenantOrderNotFound)):
            future.result(timeout=15)
    assert facts(db) == (10, 0, 1)


def test_inactive_product_can_still_release_existing_reservation(db, orders):
    draft = prepare(orders)
    confirm(orders, draft)
    db.b2b_products.update_one(
        {"organization_id": "org-a", "sku": "DRILL-001"},
        {"$set": {"status": "inactive"}, "$inc": {"revision": 1}},
    )
    assert cancel(orders, draft).status is S.CANCELLED
    assert facts(db)[1] == 0


@pytest.mark.parametrize("bad", ["digest", "lifecycle", "line", "currency"])
def test_corrupt_persisted_order_fails_closed(db, orders, bad):
    draft = prepare(orders)
    changes = {
        "digest": {"preparation_digest": "f" * 64},
        "lifecycle": {"reserved_at": datetime.now(UTC)},
        "line": {"lines.0.quantity": 0},
        "currency": {"lines.0.currency": "bad"},
    }
    db.b2b_orders.update_one(
        {"organization_id": "org-a", "order_id": draft.order_id},
        {"$set": changes[bad]},
        bypass_document_validation=True,
    )
    with pytest.raises(TenantOrdersUnavailable):
        confirm(orders, draft)
    assert facts(db) == (10, 0, 1)


def test_partial_second_migration_blocks_b1_and_b2_without_rollback(db):
    first = registry()[0]
    MigrationRunner(db, [first]).apply(confirmation=db.name)
    original = db.b2b_products.options()

    class Partial(MigrationRunner):
        def _execute_collection(self, definition, owner):
            super()._execute_collection(definition, owner)
            if definition["collection"] == "b2b_buyers":
                raise RuntimeError("synthetic crash after first DDL")

    with pytest.raises(MigrationError):
        Partial(db).apply(confirmation=db.name)
    assert db.b2b_buyers.name in db.list_collection_names()
    assert db.stockpilot_schema_migrations.find_one({"_id": 2})["status"] == "failed"
    for version in (1, 2):
        with pytest.raises(MigrationError):
            MigrationRunner(db).require_version(version)
    assert db.b2b_products.options() == original


def test_buyer_adapter_returns_explicit_inactive_record_and_no_inference(db, orders):
    adapter = MongoBuyerAssociations(db)
    assert adapter.find("org-b", "a1", "customer-org-a") == ()
    record = adapter.find("org-a", "a1", "customer-org-a")[0]
    assert record.user_id == "a1"
    db.b2b_buyers.update_one(
        {"association_id": record.association_id},
        {"$set": {"status": "disabled"}, "$inc": {"revision": 1}},
    )
    assert adapter.find("org-a", "a1", "customer-org-a")[0].status.value == "disabled"
