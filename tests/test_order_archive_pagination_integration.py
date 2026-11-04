"""Personal archive and all three lists on isolated real Mongo transactions."""
from dataclasses import replace
from copy import deepcopy
from uuid import uuid4
import pytest
from test_tenant_stock_operations_mongo_integration import db, orders, stock, call, adjustment, preview
from test_tenant_orders_mongo_integration import REQUESTS, context, prepare, confirm, cancel
from test_b2b_http_mongo_integration import http, prepare as http_prepare, confirm as http_confirm
from stockpilot.domain.entities.tenant_order_archive import OrderListQuery, SetOrderArchive
from stockpilot.application.ports.tenant_orders import TenantOrderConflict, TenantOrderNotFound
from stockpilot.application.services.identity_authorization import AuthorizationDenied

pytestmark = pytest.mark.integration


def page(orders, actor="a1", **kwargs):
    return orders[0].page(REQUESTS[actor], context(orders, actor), OrderListQuery(**kwargs))


def archive(orders, order, value=True, actor="a1", key=None):
    command = SetOrderArchive(key or uuid4().hex, order.order_id, value, True)
    return orders[0].set_archive(REQUESTS[actor], context(orders, actor), command)


def business(db):
    return {n: list(db[n].find({}).sort("_id", 1)) for n in
            ("b2b_orders", "b2b_products", "b2b_order_operations", "b2b_stock_movements", "b2b_stock_operations")}


def test_personal_archive_restore_replay_never_changes_order_or_stock(db, orders):
    order = prepare(orders)
    confirm(orders, order)
    cancelled = cancel(orders, order)
    before = business(db)
    result = archive(orders, cancelled, key="archive")
    assert result["archived"] is True
    assert archive(orders, cancelled, key="archive") == result
    assert page(orders).total == 0
    assert page(orders, archived=True).items[0].order_id == order.order_id
    assert page(orders, actor="a2").total == 1  # manager's list is independent
    assert archive(orders, cancelled, False, key="restore")["archived"] is False
    assert archive(orders, cancelled, False, key="restore")["archived"] is False
    assert page(orders).total == 1 and page(orders, archived=True).total == 0
    # A replay of the old archive receipt must not undo the later restore.
    assert archive(orders, cancelled, key="archive") == result
    assert page(orders).total == 1
    assert db.b2b_order_archives.count_documents({}) == 1
    assert db.b2b_order_archive_operations.count_documents({}) == 2
    assert business(db) == before


def test_archive_refuses_active_order_unauthorized_owner_tenant_and_changed_key(db, orders):
    order = prepare(orders)
    with pytest.raises(TenantOrderConflict):
        archive(orders, order)
    confirm(orders, order)
    with pytest.raises(TenantOrderConflict):
        archive(orders, order)
    cancelled = cancel(orders, order)
    for actor in ("a3", "b1"):
        with pytest.raises(TenantOrderNotFound):
            archive(orders, cancelled, actor=actor)
    with pytest.raises(TenantOrderNotFound):
        archive(orders, replace(cancelled, order_id="missing"))
    archive(orders, cancelled, key="same")
    with pytest.raises(TenantOrderConflict):
        archive(orders, cancelled, False, key="same")
    ctx = context(orders)
    db.b2b_memberships.update_one({"membership_id": "m-a1"}, {"$set": {"status": "suspended"}})
    with pytest.raises(AuthorizationDenied):
        orders[0].set_archive(REQUESTS["a1"], ctx, SetOrderArchive("revoked", cancelled.order_id, False, True))


def test_counts_sort_filters_and_last_page_after_removal(db, orders):
    from stockpilot.infrastructure.persistence.mongodb.tenant_orders import _proof
    order = prepare(orders)
    # Use the repository UTC codec when recomputing the persisted proof.
    template = orders[0].db.b2b_orders.find_one({"order_id": order.order_id})
    rows = []
    for i in range(104):
        row = {**template, "order_id": f"ORDER-{i:03}", "operation_id": f"seed-{i}"}
        row.pop("_id");row["preparation_digest"] = _proof(row);rows.append(row)
    db.b2b_orders.insert_many(rows)
    expected = sorted([order.order_id] + [r["order_id"] for r in rows], reverse=True)
    actual = []
    for offset in range(0, 110, 10):
        result = page(orders, limit=10, offset=offset)
        assert result.total == 105 and result.has_more == (offset < 100)
        actual.extend(o.order_id for o in result.items)
    assert actual == expected
    assert page(orders, actor="a3").total == 0
    assert page(orders, actor="b1").total == 0
    assert page(orders, status="reserved").total == 0
    confirm(orders, order)
    assert page(orders, status="reserved").total == 1
    cancel(orders, order);archive(orders, order)
    assert page(orders, status="prepared").total == 104
    assert page(orders, status="cancelled").total == 0
    assert page(orders, status="cancelled", archived=True).total == 1
    db.b2b_buyers.update_one({"association_id": "buyer-a1"}, {"$set": {"status": "disabled"}})
    assert page(orders, actor="a2").total == 0  # grant filtering before count


def test_movement_import_totals_are_independent_scoped_and_stable(db, orders, stock):
    for i in range(12):
        call(stock, orders, "adjust", adjustment(db, 10 + i, f"adjust-{i}"))
        preview(stock, orders)
    call(stock, orders, "adjust", adjustment(db, 3, "foreign", org="org-b"), actor="admin-b")
    preview(stock, orders, actor="admin-b")
    movements = call(stock, orders, "movements_page", 10, 0, actor="a1")
    imports = call(stock, orders, "imports_page", 10, 0)
    assert movements.total == imports.total == 12 and movements.has_more and imports.has_more
    next_movements = call(stock, orders, "movements_page", 10, 10, actor="a1")
    next_imports = call(stock, orders, "imports_page", 10, 10)
    assert len(next_movements.items) == len(next_imports.items) == 2
    assert not next_movements.has_more and not next_imports.has_more
    assert len({x.movement_id for x in movements.items + next_movements.items}) == 12
    assert len({x.import_id for x in imports.items + next_imports.items}) == 12
    assert call(stock, orders, "movements_page", actor="b1").total == 1
    with pytest.raises(AuthorizationDenied):
        call(stock, orders, "imports_page", actor="a1")


def test_archive_http_csrf_confirmation_and_count_contract(http):
    c = http.browser()
    order = http_prepare(c).json()
    http_confirm(c, order)
    assert c.post("/b2b/api/orders/" + order["order_id"] + "/cancel", json={"operation_id": "cancel"}).status_code == 200
    url = "/b2b/api/orders/" + order["order_id"] + "/archive"
    body = {"operation_id": "archive", "confirmed": True}
    assert c.post(url, json={**body, "confirmed": 1}).status_code == 422
    assert c.post(url, json=body, headers={"X-BFF-CSRF": "bad"}).status_code == 403
    assert c.post(url, json=body, headers={"Origin": "https://evil.invalid"}).status_code == 403
    assert http.browser("b1").post(url, json=body).status_code == 404
    assert c.post(url, json=body).status_code == 200
    assert c.get("/b2b/api/orders").json()["total"] == 0
    archived = c.get("/b2b/api/orders?archived=true&status=cancelled").json()
    assert archived["total"] == 1 and archived["has_more"] is False
    assert http.browser("a2").get("/b2b/api/orders").json()["total"] == 1
    assert c.get("/b2b/api/orders?user_id=other").status_code in (400, 422)


def test_upgrade0004_to0005_preserves_existing_data_validators_and_indexes(db):
    from test_tenant_catalogue_mongo_integration import document
    from stockpilot.infrastructure.persistence.mongodb.schema_migrations import MigrationRunner, registry
    MigrationRunner(db, registry()[:4]).apply(confirmation=db.name)
    product = document()
    db.b2b_products.insert_one(product)
    names = db.list_collection_names()
    options = {name: db[name].options() for name in names}
    indexes = {name: list(db[name].list_indexes()) for name in names}
    history = list(db.stockpilot_schema_migrations.find().sort("_id", 1))
    runner = MigrationRunner(db)
    assert [m.version for m in runner.plan()] == [5]
    runner.apply(confirmation=db.name)
    runner.require_version(5)
    assert {name: db[name].options() for name in names} == options
    assert {name: list(db[name].list_indexes()) for name in names} == indexes
    assert list(db.stockpilot_schema_migrations.find().sort("_id", 1))[:4] == history
    assert db.b2b_products.find_one({"_id": product["_id"]}) == product
    assert set(db.list_collection_names()) - set(names) == {
        "b2b_order_archives", "b2b_order_archive_operations"
    }
    assert db.b2b_order_archives.count_documents({}) == 0
    assert db.b2b_order_archive_operations.count_documents({}) == 0
    completed = list(db.stockpilot_schema_migrations.find().sort("_id", 1))
    assert runner.plan() == ()
    runner.apply(confirmation=db.name)
    assert list(db.stockpilot_schema_migrations.find().sort("_id", 1)) == completed
