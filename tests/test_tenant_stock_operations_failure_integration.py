"""Failure/concurrency evidence on real B3 transactions, not memory stock."""

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import Event

import pytest
from pymongo import MongoClient
from pymongo.client_session import ClientSession
from pymongo.errors import ConnectionFailure, OperationFailure
from test_tenant_orders_mongo_integration import (
    REQUESTS,
    cancel,
    confirm,
    context,
    facts,
    prepare,
)
from test_tenant_stock_operations_mongo_integration import (
    adjustment,
    apply,
    apply_command,
    call,
    preview,
    product,
)
from test_tenant_stock_operations_mongo_integration import db as db  # noqa: PLC0414
from test_tenant_stock_operations_mongo_integration import orders as orders  # noqa: PLC0414
from test_tenant_stock_operations_mongo_integration import stock as stock  # noqa: PLC0414

from stockpilot.application.ports.tenant_orders import (
    TenantOrderOutcomeUnknown,
    TenantOrdersUnavailable,
)
from stockpilot.application.services.identity_authorization import AuthorizationDenied
from stockpilot.domain.entities.tenant_stock_operation import OperationStatus as S
from stockpilot.infrastructure.persistence.mongodb.schema_migrations import (
    MigrationError,
    MigrationRunner,
    registry,
)
from stockpilot.infrastructure.persistence.mongodb.tenant_stock_operations import (
    MongoTenantStockOperations,
)

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("transient", [False, True])
def test_failure_after_first_stock_and_movement_rolls_back_whole_lot(
    db, orders, stock, monkeypatch, transient
):
    view = preview(stock, orders, b"sku,quantity\nDRILL-001,8\nSAW-001,2\n")
    command = apply_command(view, "multi")
    original, first = stock._movement, [True]

    def fail(*args):
        result = original(*args)
        if first[0]:
            first[0] = False
            raise OperationFailure(
                "synthetic mid-batch failure",
                details={
                    "errorLabels": ["TransientTransactionError"] if transient else []
                },
            )
        return result

    with monkeypatch.context() as scoped:
        scoped.setattr(stock, "_movement", fail)
        if transient:
            assert call(stock, orders, "apply", command).status is S.SUCCEEDED
        else:
            with pytest.raises(TenantOrdersUnavailable):
                call(stock, orders, "apply", command)
            assert facts(db) == (10, 0, 1) and facts(db, sku="SAW-001") == (3, 0, 1)
            assert db.b2b_stock_movements.count_documents({}) == 0
            assert (
                db.b2b_imports.find_one({"import_id": view.import_id})["status"]
                == "preview"
            )
            pending = call(stock, orders, "result", "import", "multi")
            assert pending.status is S.PENDING
            with pytest.raises(TypeError):
                bool(pending)
    result = call(stock, orders, "apply", command)
    assert result.status is S.SUCCEEDED and len(result.movement_ids) == 2
    assert facts(db) == (8, 0, 2) and facts(db, sku="SAW-001") == (2, 0, 2)
    assert db.b2b_stock_movements.count_documents({}) == 2


@pytest.mark.parametrize("kind", ["connection", "unknown"])
@pytest.mark.parametrize("action", ["adjust", "import"])
def test_lost_response_after_actual_commit_reconciles_once(
    db, orders, stock, monkeypatch, kind, action
):
    command = (
        adjustment(db, 8, "commit")
        if action == "adjust"
        else apply_command(preview(stock, orders), "commit")
    )
    method = "adjust" if action == "adjust" else "apply"
    original, count = ClientSession.commit_transaction, [0]

    def lost(session):
        result = original(session)
        count[0] += 1
        if count[0] == 2 and kind == "connection":
            raise ConnectionFailure("synthetic lost response")
        if kind == "unknown" and 2 <= count[0] <= 5:
            raise OperationFailure(
                "synthetic lost commit response",
                details={"errorLabels": ["UnknownTransactionCommitResult"]},
            )
        return result

    monkeypatch.setattr(ClientSession, "commit_transaction", lost)
    result = call(stock, orders, method, command)
    assert result.status is S.SUCCEEDED
    assert facts(db) == (8, 0, 2) and db.b2b_stock_movements.count_documents({}) == 1
    assert call(stock, orders, method, command) == result
    assert facts(db) == (8, 0, 2)


def test_unknown_does_not_assume_abort_and_new_client_replays(
    db, orders, stock, monkeypatch
):
    command = adjustment(db, 8, "unknown")
    original, count = ClientSession.commit_transaction, [0]

    def lost(session):
        result = original(session)
        count[0] += 1
        if count[0] > 1:
            raise OperationFailure(
                "synthetic lost response",
                details={"errorLabels": ["UnknownTransactionCommitResult"]},
            )
        return result

    with monkeypatch.context() as scoped:
        scoped.setattr(ClientSession, "commit_transaction", lost)
        with pytest.raises(TenantOrderOutcomeUnknown):
            call(stock, orders, "adjust", command)
    assert facts(db) == (8, 0, 2)
    with MongoClient(os.environ["MONGO_TEST_URI"]) as client:
        restarted = MongoTenantStockOperations(client[db.name], stock.authorization)
        assert call(restarted, orders, "adjust", command).status is S.SUCCEEDED
    assert facts(db) == (8, 0, 2) and db.b2b_stock_movements.count_documents({}) == 1


def test_unobserved_commit_leaves_pending_not_false_rejection(
    db, orders, stock, monkeypatch
):
    command = adjustment(db, 8, "uncertain")
    original, count = ClientSession.commit_transaction, [0]

    def uncertain(session):
        count[0] += 1
        if 2 <= count[0] <= 5:
            raise OperationFailure(
                "synthetic unknown before observed commit",
                details={"errorLabels": ["UnknownTransactionCommitResult"]},
            )
        return original(session)

    with monkeypatch.context() as scoped:
        scoped.setattr(ClientSession, "commit_transaction", uncertain)
        result = call(stock, orders, "adjust", command)
        assert result.status is S.PENDING
    assert facts(db) == (10, 0, 1)
    assert call(stock, orders, "adjust", command).status is S.SUCCEEDED
    assert facts(db) == (8, 0, 2)


@pytest.mark.parametrize(
    "target", ["b2b_users", "b2b_memberships", "b2b_organizations"]
)
def test_revocation_during_mutation_after_pending_declaration(
    db, orders, stock, monkeypatch, target
):
    command, ctx = adjustment(db, 8, "revocation"), context(orders, "admin-a")
    entered, finish, calls = Event(), Event(), [0]
    original = stock._identity

    def pause(principal, session):
        calls[0] += 1
        if calls[0] == 2:
            db.b2b_products.find_one({"organization_id": "org-a"}, session=session)
            entered.set()
            assert finish.wait(10)
        return original(principal, session)

    monkeypatch.setattr(stock, "_identity", pause)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(stock.adjust, REQUESTS["admin-a"], ctx, command)
        assert entered.wait(10)
        try:
            query = (
                {"organization_id": "org-a"}
                if target == "b2b_organizations"
                else {"user_id": "admin-a"}
            )
            db[target].update_one(
                query,
                {
                    "$set": {
                        "status": "disabled" if target == "b2b_users" else "suspended"
                    }
                },
            )
        finally:
            finish.set()
        with pytest.raises(AuthorizationDenied):
            future.result(timeout=20)
    assert facts(db) == (10, 0, 1)
    assert (
        db.b2b_stock_operations.find_one({"operation_id": "revocation"})["status"]
        == "pending"
    )
    assert db.b2b_stock_movements.count_documents({}) == 0


@pytest.mark.parametrize("other", ["confirm", "cancel"])
def test_import_concurrent_with_order_uses_same_revision_boundary(
    db, orders, stock, other
):
    order = prepare(orders, quantity=6)
    if other == "cancel":
        confirm(orders, order)
    view = preview(stock, orders, b"sku,quantity\nDRILL-001,8\n")

    def order_change():
        return confirm(orders, order) if other == "confirm" else cancel(orders, order)

    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = pool.submit(apply, stock, orders, view), pool.submit(order_change)
        result, _ = a.result(timeout=20), b.result(timeout=20)
    physical, reserved, _ = facts(db)
    assert 0 <= reserved <= physical
    assert reserved == (6 if other == "confirm" else 0)
    assert physical == (8 if result.status is S.SUCCEEDED else 10)
    assert facts(db, "org-b") == (2, 0, 1)


@pytest.mark.parametrize("cause", ["product", "preview", "rules", "ledger"])
def test_corruption_is_technical_no_stock_or_movement_success(db, orders, stock, cause):
    view = preview(stock, orders)
    if cause == "product":
        db.b2b_products.update_one(
            {"organization_id": "org-a", "sku": "DRILL-001"},
            {"$set": {"reserved": 11}},
            bypass_document_validation=True,
        )
    elif cause == "preview":
        db.b2b_imports.update_one(
            {"import_id": view.import_id}, {"$set": {"rows.0.values.quantity": 9}}
        )
    elif cause == "rules":
        db.b2b_imports.update_one(
            {"import_id": view.import_id},
            {"$set": {"rules_version": "unexpected"}},
            bypass_document_validation=True,
        )
    else:
        # Create a rejected durable adjustment, then corrupt its state contradiction.
        bad = call(
            stock,
            orders,
            "adjust",
            replace(adjustment(db, 8, "ledger"), expected_revision=99),
        )
        assert bad.status is S.REJECTED
        db.b2b_stock_operations.update_one(
            {"operation_id": "ledger"}, {"$set": {"movement_ids": ["uncommitted"]}}
        )
        with pytest.raises(TenantOrdersUnavailable):
            call(stock, orders, "result", "adjust", "ledger")
        return
    with pytest.raises(TenantOrdersUnavailable):
        apply(stock, orders, view)
    assert product(db)["quantity"] == 10
    assert db.b2b_stock_movements.count_documents({}) == 0
    assert db.b2b_imports.find_one({"import_id": view.import_id})["status"] == "preview"


def test_mongo_unavailable_cannot_acknowledge_or_register_success(db, orders, stock):
    with MongoClient(
        "mongodb://127.0.0.1:1", serverSelectionTimeoutMS=100, connectTimeoutMS=100
    ) as client:
        broken = MongoTenantStockOperations(client[db.name], stock.authorization)
        with pytest.raises(TenantOrdersUnavailable):
            call(broken, orders, "adjust", adjustment(db, 8))
    assert facts(db) == (10, 0, 1)
    assert db.b2b_stock_operations.count_documents({}) == 0


def test_session_rotation_and_expired_unconsumed_preview(
    db, orders, stock, monkeypatch
):
    view = preview(stock, orders)
    original = orders[1].current
    monkeypatch.setattr(
        orders[1],
        "current",
        lambda request: replace(
            original(request), authorization_context_id="new-session"
        ),
    )
    assert apply(stock, orders, view).code == "preview_invalid"
    assert facts(db) == (10, 0, 1)
    monkeypatch.setattr(orders[1], "current", original)
    fresh = preview(stock, orders)
    monkeypatch.setattr(stock, "clock", lambda: datetime.now(UTC) + timedelta(hours=1))
    assert apply(stock, orders, fresh).code == "expired"
    assert facts(db) == (10, 0, 1)


def test_upgrade0002_to0003_preserves_checksums_and_b2_collections(db):
    from test_tenant_catalogue_mongo_integration import document

    before = registry()[:2]
    MigrationRunner(db, before).apply(confirmation=db.name)
    row = document()
    db.b2b_products.insert_one(row)
    checksums = [
        r["checksum"] for r in db.stockpilot_schema_migrations.find().sort("_id", 1)
    ]
    runner = MigrationRunner(db, registry()[:3])
    assert [m.version for m in runner.plan()] == [3]
    runner.apply(confirmation=db.name)
    for version in (1, 2, 3):
        runner.require_version(version)
    assert db.b2b_products.find_one({"_id": row["_id"]}) == row
    assert [
        r["checksum"] for r in db.stockpilot_schema_migrations.find().sort("_id", 1)
    ][:2] == checksums
    for name in ("b2b_stock_operations", "b2b_imports", "b2b_stock_movements"):
        assert all("expireAfterSeconds" not in i for i in db[name].list_indexes())


def test_upgrade0003_to0004_is_additive_and_keeps_historical_validators(db):
    from test_tenant_catalogue_mongo_integration import document
    MigrationRunner(db, registry()[:3]).apply(confirmation=db.name)
    row = document()
    db.b2b_products.insert_one(row)
    names = db.list_collection_names()
    options = {name: db[name].options() for name in names}
    history = list(db.stockpilot_schema_migrations.find().sort("_id", 1))
    runner = MigrationRunner(db)
    assert [m.version for m in runner.plan()] == [4]
    runner.apply(confirmation=db.name)
    runner.require_version(4)
    assert {name: db[name].options() for name in names} == options
    assert list(db.stockpilot_schema_migrations.find().sort("_id", 1))[:3] == history
    assert db.b2b_products.find_one({"_id": row["_id"]}) == row
    assert db.b2b_product_archives.count_documents({}) == 0


def test_partial0003_blocks_all_gates_without_destructive_rollback(db):
    MigrationRunner(db, registry()[:2]).apply(confirmation=db.name)

    class Partial(MigrationRunner):
        def _execute_collection(self, definition, owner):
            super()._execute_collection(definition, owner)
            if definition["collection"] == "b2b_stock_movements":
                raise RuntimeError("synthetic interrupted DDL")

    with pytest.raises(MigrationError):
        Partial(db).apply(confirmation=db.name)
    assert db.stockpilot_schema_migrations.find_one({"_id": 3})["status"] == "failed"
    assert "b2b_stock_movements" in db.list_collection_names()
    for version in (1, 2, 3):
        with pytest.raises(MigrationError):
            MigrationRunner(db).require_version(version)


def test_order_fence_retry_yields_until_competing_commit(
    db, orders, stock, monkeypatch
):
    """A real Mongo WriteConflict must yield time for the fence holder to finish."""
    from time import sleep

    from pymongo.errors import PyMongoError

    order = prepare(orders, quantity=4)
    held, collision, release = Event(), Event(), Event()
    original, first, errors = orders[0]._identity, [True], []

    def fenced(principal, session):
        try:
            original(principal, session)
        except PyMongoError as error:
            errors.append(
                (
                    type(error).__name__,
                    getattr(error, "code", None),
                    error.has_error_label("TransientTransactionError"),
                )
            )
            collision.set()
            raise
        if first[0]:
            first[0] = False
            held.set()
            assert release.wait(10)

    monkeypatch.setattr(orders[0], "_identity", fenced)
    with ThreadPoolExecutor(max_workers=2) as pool:
        reservation = pool.submit(confirm, orders, order)
        assert held.wait(10)
        cancellation = pool.submit(cancel, orders, order)
        try:
            assert collision.wait(10)
            sleep(0.025)
        finally:
            release.set()
        reservation.result(timeout=20)
        try:
            result = cancellation.result(timeout=20)
        except TenantOrdersUnavailable:
            pytest.fail(f"Fence holder released but retries exhausted: {errors}")
    assert errors and any(code == 112 and transient for _, code, transient in errors)
    assert result.status.value == "cancelled"
    assert facts(db)[1] == 0
    assert db.b2b_orders.find_one({"order_id": order.order_id})["status"] == "cancelled"
    assert db.b2b_order_operations.count_documents({"namespace": "cancel"}) == 1
