"""Actual Mongo B3/B2 coexistence; never application or demo databases."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from time import perf_counter
from uuid import uuid4

import pytest
from test_tenant_orders_mongo_integration import (
    ACTORS,
    REQUESTS,
    cancel,
    confirm,
    context,
    facts,
    prepare,
)
from test_tenant_orders_mongo_integration import db as db  # noqa: PLC0414
from test_tenant_orders_mongo_integration import orders as orders  # noqa: PLC0414

from stockpilot.application.ports.tenant_orders import TenantOrderConflict
from stockpilot.application.ports.tenant_stock_operations import (
    TenantStockOperationConflict,
    TenantStockResourceNotFound,
)
from stockpilot.application.services.identity_authorization import AuthorizationDenied
from stockpilot.domain.entities.tenant_stock_operation import (
    AdjustTenantStock,
    ArchiveTenantProduct,
    ApplyTenantImport,
)
from stockpilot.domain.entities.tenant_stock_operation import (
    ImportMode as M,
)
from stockpilot.domain.entities.tenant_stock_operation import (
    OperationStatus as S,
)
from stockpilot.infrastructure.persistence.mongodb.tenant_stock_operations import (
    MongoTenantStockOperations,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def stock(db, orders, monkeypatch):
    now = datetime.now(UTC)
    for actor, org in (("admin-a", "org-a"), ("admin-b", "org-b")):
        monkeypatch.setitem(ACTORS, actor, (org, "ORGANIZATION_ADMIN"))
        monkeypatch.setitem(REQUESTS, actor, object())
        db.b2b_users.insert_one(
            {
                "user_id": actor,
                "external_identity": {
                    "issuer": "https://synthetic.example/realms/b2",
                    "subject": actor,
                },
                "status": "active",
                "created_at": now,
                "updated_at": now,
            }
        )
        db.b2b_memberships.insert_one(
            {
                "membership_id": "m-" + actor,
                "user_id": actor,
                "organization_id": org,
                "role": "ORGANIZATION_ADMIN",
                "status": "active",
                "created_at": now,
                "updated_at": now,
            }
        )
    return MongoTenantStockOperations(db, orders[0].authorization)


def call(service, orders, method, *args, actor="admin-a"):
    return getattr(service, method)(REQUESTS[actor], context(orders, actor), *args)


def product(db, org="org-a", sku="DRILL-001"):
    return db.b2b_products.find_one({"organization_id": org, "sku": sku})


def adjustment(db, value, operation=None, org="org-a", sku="DRILL-001"):
    row = product(db, org, sku)
    return AdjustTenantStock(
        operation or uuid4().hex,
        str(row["_id"]),
        value,
        row["revision"],
        "Physical inventory",
    )


def archive_command(db, operation=None, org="org-a"):
    row = product(db, org)
    return ArchiveTenantProduct(operation or uuid4().hex, str(row["_id"]), row["revision"], True)


def test_archive_preserves_inventory_and_order_history_with_durable_replay(db, orders, stock):
    order = prepare(orders, quantity=2)
    confirm(orders, order, operation="reserve-before-archive")
    denied = call(stock, orders, "archive", archive_command(db))
    assert denied.status is S.REJECTED and denied.code == "reserved_conflict"
    cancel(orders, order, operation="cancel-before-archive")
    before = product(db)
    historical_orders = list(db.b2b_orders.find({}))
    foreign = product(db, "org-b")
    command = archive_command(db)
    result = call(stock, orders, "archive", command)
    assert result.status is S.SUCCEEDED
    assert call(stock, orders, "archive", command) == result
    assert call(stock, orders, "result", "archive", command.operation_id) == result
    after = product(db)
    assert after == {**before, "status": "inactive", "revision": before["revision"] + 1}
    assert list(db.b2b_orders.find({})) == historical_orders
    assert product(db, "org-b") == foreign
    with pytest.raises(TenantStockOperationConflict):
        call(stock, orders, "archive", replace(command, expected_revision=after["revision"]))


def test_archive_checks_tenant_permissions_revision_and_confirmation(db, orders, stock):
    command = archive_command(db)
    with pytest.raises(AuthorizationDenied):
        call(stock, orders, "archive", command, actor="a1")
    foreign = call(stock, orders, "archive", archive_command(db, org="org-b"))
    assert foreign.status is S.REJECTED and foreign.code == "unavailable_resource"
    stale = call(stock, orders, "archive", replace(command, expected_revision=command.expected_revision + 1))
    assert stale.status is S.REJECTED and stale.code == "revision_conflict"
    assert product(db)["status"] == "active"
    with pytest.raises(ValueError):
        replace(command, confirmed=False)


def preview(stock, orders, content=None, mode=M.STOCK_ONLY, actor="admin-a"):
    return call(
        stock,
        orders,
        "preview",
        content or b"sku,quantity\nDRILL-001,8\n",
        mode,
        actor=actor,
    )


def apply_command(view, operation=None):
    return ApplyTenantImport(
        operation or uuid4().hex, view.import_id, view.preview_digest, True
    )


def apply(stock, orders, view, operation=None, actor="admin-a"):
    return call(stock, orders, "apply", apply_command(view, operation), actor=actor)


def test_order_adjustment_cancel_replays_and_foreign_org_unchanged(db, orders, stock):
    foreign = list(db.b2b_products.find({"organization_id": "org-b"}))
    order = prepare(orders, quantity=6)
    confirm(orders, order, operation="reserve")
    rejected = call(stock, orders, "adjust", adjustment(db, 5, "bad"))
    assert rejected.status is S.REJECTED and rejected.code == "reserved_conflict"
    assert facts(db) == (10, 6, 2)
    command = adjustment(db, 8, "good")
    result = call(stock, orders, "adjust", command)
    assert result.status is S.SUCCEEDED and facts(db) == (8, 6, 3)
    assert call(stock, orders, "adjust", command) == result
    cancel(orders, order, operation="cancel")
    cancel(orders, order, operation="cancel")
    assert facts(db) == (8, 0, 4)
    assert call(stock, orders, "adjust", command) == result
    movements = call(stock, orders, "movements", actor="a1")
    assert len(movements) == 1
    assert (
        movements[0].quantity_before,
        movements[0].quantity_after,
        movements[0].reserved_before,
        movements[0].reserved_after,
        movements[0].actor_user_id,
    ) == (10, 8, 6, 6, "admin-a")
    assert list(db.b2b_products.find({"organization_id": "org-b"})) == foreign


def test_same_operation_id_in_two_orgs_and_conflicting_payload(db, orders, stock):
    a, b = adjustment(db, 8, "same"), adjustment(db, 3, "same", org="org-b")
    assert call(stock, orders, "adjust", a).status is S.SUCCEEDED
    assert call(stock, orders, "adjust", b, actor="admin-b").status is S.SUCCEEDED
    with pytest.raises(TenantStockOperationConflict):
        call(stock, orders, "adjust", replace(a, quantity=9))
    assert facts(db) == (8, 0, 2) and facts(db, "org-b") == (3, 0, 2)


def test_user_forged_inputs_and_manager_organization_boundary(db, orders, stock):
    command = adjustment(db, 8)
    with pytest.raises(AuthorizationDenied):
        call(stock, orders, "adjust", command, actor="a1")
    assert call(stock, orders, "adjust", command, actor="a2").status is S.SUCCEEDED
    foreign = call(stock, orders, "adjust", adjustment(db, 1, org="org-b"), actor="a2")
    missing = call(
        stock,
        orders,
        "adjust",
        replace(adjustment(db, 1), product_id="missing"),
        actor="a2",
    )
    assert (
        foreign.status is missing.status is S.REJECTED
        and foreign.code == missing.code == "unavailable_resource"
    )
    with pytest.raises(AuthorizationDenied):
        stock.adjust(
            {"organization_id": "org-a", "role": "ORGANIZATION_ADMIN"},
            context(orders, "a1"),
            adjustment(db, 1),
        )
    assert facts(db, "org-b") == (2, 0, 1)


@pytest.mark.parametrize("actor", ["a1", "a2", "b1"])
def test_non_admin_cannot_preview_apply_or_list_imports(db, orders, stock, actor):
    view = preview(stock, orders)
    for method, args in (
        ("preview", (b"sku,quantity\nDRILL-001,1\n", M.STOCK_ONLY)),
        ("apply", (apply_command(view),)),
        ("imports", ()),
    ):
        with pytest.raises(AuthorizationDenied):
            call(stock, orders, method, *args, actor=actor)
    assert facts(db) == (10, 0, 1)


def test_create_import_is_atomic_scoped_and_idempotent(db, orders, stock):
    content = b"sku,name,description,category,price_minor,currency,quantity\nSAW-002,New Saw,Fixture,saw,900,EUR,5\nNEW,New Tool,Fixture,drill,1000,EUR,1\n"
    before = list(db.b2b_products.find())
    view = preview(stock, orders, content, M.CREATE_ONLY)
    assert view.status == "preview" and list(db.b2b_products.find()) == before
    command = apply_command(view, "create")
    result = call(stock, orders, "apply", command)
    assert result.status is S.SUCCEEDED and len(result.movement_ids) == 2
    assert product(db, "org-a", "SAW-002")["quantity"] == 5
    assert product(db, "org-b", "SAW-002")["quantity"] == 7
    assert call(stock, orders, "apply", command) == result
    assert (
        db.b2b_products.count_documents({"organization_id": "org-a", "sku": "NEW"}) == 1
    )
    assert db.b2b_stock_movements.count_documents({"organization_id": "org-a"}) == 2


def test_metadata_only_never_overwrites_physical_or_reserved(db, orders, stock):
    order = prepare(orders, quantity=6)
    confirm(orders, order)
    view = preview(
        stock,
        orders,
        b"sku,name,description,category,price_minor,currency\nDRILL-001,Renamed Drill,Safe metadata,drill,5100,EUR\n",
        M.METADATA_ONLY,
    )
    assert apply(stock, orders, view).status is S.SUCCEEDED
    row = product(db)
    assert (
        row["quantity"],
        row["reserved"],
        row["revision"],
        row["price_minor"],
        row["name"],
    ) == (10, 6, 3, 5100, "Renamed Drill")
    assert db.b2b_stock_movements.count_documents({}) == 0
    cancel(orders, order)
    assert facts(db) == (10, 0, 4)


@pytest.mark.parametrize(
    "content,mode",
    [
        (b"sku,quantity\nSAW-002,5\n", M.STOCK_ONLY),
        (b"sku,quantity\nDRILL-001,5\nDRILL-001,6\n", M.STOCK_ONLY),
        (
            b"sku,name,description,category,price_minor,currency\nDRILL-001,Tool,Fixture,drill,-1,EUR\n",
            M.METADATA_ONLY,
        ),
        (b"sku,quantity\nDRILL-001,-1\n", M.STOCK_ONLY),
        (b"sku,quantity\nDRILL-001,1.5\n", M.STOCK_ONLY),
    ],
)
def test_invalid_preview_has_durable_diagnostics_and_rejected_apply(
    db, orders, stock, content, mode
):
    view = preview(stock, orders, content, mode)
    assert view.status == "invalid" and view.issues
    result = apply(stock, orders, view)
    assert result.status is S.REJECTED and result.code == "invalid_csv"
    assert call(stock, orders, "result", "import", result.operation_id) == result
    assert call(stock, orders, "imports")[0].status == "rejected"
    assert call(stock, orders, "imports")[0].issues
    assert apply(stock, orders, view, result.operation_id) == result
    assert facts(db) == (10, 0, 1)
    assert db.b2b_stock_movements.count_documents({}) == 0


def test_multiline_revision_change_rejects_whole_batch_durably(db, orders, stock):
    view = preview(stock, orders, b"sku,quantity\nDRILL-001,8\nSAW-001,2\n")
    db.b2b_products.update_one(
        {"organization_id": "org-a", "sku": "SAW-001"}, {"$inc": {"revision": 1}}
    )
    result = apply(stock, orders, view, "stale")
    assert result.status is S.REJECTED and result.code == "revision_conflict"
    assert facts(db) == (10, 0, 1) and facts(db, sku="SAW-001") == (3, 0, 2)
    assert (
        db.b2b_imports.find_one({"import_id": view.import_id})["status"] == "rejected"
    )
    assert call(stock, orders, "result", "import", "stale") == result
    assert db.b2b_stock_movements.count_documents({}) == 0


def test_new_reservation_after_preview_does_not_get_overwritten(db, orders, stock):
    view = preview(stock, orders, b"sku,quantity\nDRILL-001,5\n")
    order = prepare(orders, quantity=6)
    confirm(orders, order)
    result = apply(stock, orders, view)
    assert result.status is S.REJECTED
    assert facts(db) == (10, 6, 2)
    fresh = preview(stock, orders, b"sku,quantity\nDRILL-001,8\n")
    assert apply(stock, orders, fresh).status is S.SUCCEEDED
    assert facts(db) == (8, 6, 3)


def test_foreign_preview_digest_expiry_session_and_consumption(
    db, orders, stock, monkeypatch
):
    view = preview(stock, orders)
    assert apply(stock, orders, view, actor="admin-b").code == "unavailable_resource"
    bad = replace(apply_command(view), preview_digest="0" * 64)
    assert call(stock, orders, "apply", bad).code == "preview_invalid"
    assert db.b2b_imports.find_one({"import_id": view.import_id})["status"] == "preview"
    result = apply(stock, orders, view, "good")
    assert result.status is S.SUCCEEDED
    monkeypatch.setattr(stock, "clock", lambda: datetime.now(UTC) + timedelta(hours=1))
    assert apply(stock, orders, view, "good") == result  # Replay after expiry is safe.
    assert apply(stock, orders, view).code == "consumed"


def test_file_changed_same_operation_id_conflicts(db, orders, stock):
    a = preview(stock, orders)
    b = preview(stock, orders, b"sku,quantity\nDRILL-001,9\n")
    assert apply(stock, orders, a, "same").status is S.SUCCEEDED
    with pytest.raises(TenantStockOperationConflict):
        apply(stock, orders, b, "same")
    assert facts(db) == (8, 0, 2)


@pytest.mark.parametrize(
    "target,fields",
    [
        ("b2b_memberships", {"status": "suspended"}),
        ("b2b_users", {"status": "disabled"}),
        ("b2b_organizations", {"status": "disabled"}),
        ("b2b_memberships", {"role": "USER"}),
    ],
)
def test_revocation_after_preview_denies_without_business_mutation(
    db, orders, stock, target, fields
):
    view = preview(stock, orders)
    ctx = context(orders, "admin-a")
    query = (
        {"organization_id": "org-a"}
        if target == "b2b_organizations"
        else {"user_id": "admin-a"}
    )
    db[target].update_one(query, {"$set": fields})
    with pytest.raises(AuthorizationDenied):
        stock.apply(REQUESTS["admin-a"], ctx, apply_command(view))
    assert facts(db) == (10, 0, 1)


def test_concurrent_same_adjustment_and_same_import_apply_at_most_once(
    db, orders, stock
):
    command = adjustment(db, 8, "adj")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda _: call(stock, orders, "adjust", command), range(2))
        )
    assert all(r.status is S.SUCCEEDED for r in results)
    assert len({r.movement_ids for r in results}) == 1 and facts(db) == (8, 0, 2)
    view, operation_id = preview(stock, orders, b"sku,quantity\nDRILL-001,7\n"), "imp"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda _: apply(stock, orders, view, operation_id), range(2))
        )
    assert all(r.status is S.SUCCEEDED for r in results)
    assert facts(db) == (7, 0, 3)
    assert db.b2b_stock_movements.count_documents({}) == 2


@pytest.mark.parametrize("other", ["confirm", "cancel", "import", "adjust"])
def test_order_and_admin_concurrency_preserves_reserved_bounds(
    db, orders, stock, other
):
    order = prepare(orders, quantity=6)
    if other == "cancel":
        confirm(orders, order)
    command = adjustment(db, 5 if other == "confirm" else 8, "adjust")
    view = (
        preview(stock, orders, b"sku,quantity\nDRILL-001,7\n")
        if other == "import"
        else None
    )

    def alternate():
        if other == "confirm":
            try:
                return confirm(orders, order)
            except TenantOrderConflict:
                return None
        if other == "cancel":
            return cancel(orders, order)
        if other == "import":
            return apply(stock, orders, view)
        return call(
            stock, orders, "adjust", replace(command, operation_id="other", quantity=9)
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(call, stock, orders, "adjust", command)
        second = pool.submit(alternate)
        first.result(timeout=20)
        second.result(timeout=20)
    row = product(db)
    assert 0 <= row["reserved"] <= row["quantity"]
    assert product(db, "org-b")["quantity"] == 2
    assert product(db, "org-b")["reserved"] == 0
    if other == "confirm":
        state = db.b2b_orders.find_one({"order_id": order.order_id})["status"]
        assert (state == "reserved" and row["reserved"] == 6) or (
            state == "prepared" and row["reserved"] == 0
        )
    elif other == "cancel":
        assert row["reserved"] == 0


def test_import_vs_import_only_one_consumes_preview(db, orders, stock):
    view = preview(stock, orders)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: apply(stock, orders, view), range(2)))
    assert sum(r.status is S.SUCCEEDED for r in results) == 1
    assert sum(r.status is S.REJECTED for r in results) == 1
    assert facts(db) == (8, 0, 2) and db.b2b_stock_movements.count_documents({}) == 1


def test_history_pagination_and_result_ownership_are_scoped(db, orders, stock):
    for i in range(3):
        call(stock, orders, "adjust", adjustment(db, 9 - i))
    preview(stock, orders)
    assert len(call(stock, orders, "movements", 2, 0, actor="a1")) == 2
    assert len(call(stock, orders, "movements", 2, 2, actor="a1")) == 1
    assert call(stock, orders, "movements", actor="b1") == ()
    assert call(stock, orders, "imports", actor="admin-b") == ()
    assert len(call(stock, orders, "imports")) == 1
    with pytest.raises(TenantStockResourceNotFound):
        call(stock, orders, "result", "adjust", "missing")
    with pytest.raises(ValueError):
        call(stock, orders, "movements", 101)


def test_low_concurrency_latency_and_conflict_sample(db, orders, stock):
    import json
    from pathlib import Path
    from statistics import median
    from threading import Lock

    durations, rejections = [], 0
    attempts, lock = [0], Lock()
    original = stock._identity

    def count(principal, session):
        with lock:
            attempts[0] += 1
        return original(principal, session)

    stock._identity = count
    for i in range(5):
        command = adjustment(db, 12 + i)

        def run(offset, command=command):
            start = perf_counter()
            result = call(
                stock,
                orders,
                "adjust",
                replace(
                    command,
                    operation_id=uuid4().hex,
                    quantity=command.quantity + offset,
                ),
            )
            return result, (perf_counter() - start) * 1000

        with ThreadPoolExecutor(max_workers=2) as pool:
            pair = list(pool.map(run, (0, 1)))
        assert sum(r.status is S.SUCCEEDED for r, _ in pair) == 1
        rejections += sum(r.status is S.REJECTED for r, _ in pair)
        durations.extend(duration for _, duration in pair)
    assert rejections == 5 and db.b2b_stock_movements.count_documents({}) == 5
    report = {
        "operations": 10,
        "concurrency": 2,
        "successes": 5,
        "revision_rejections": rejections,
        "identity_attempts_including_retries": attempts[0],
        "latency_ms_median": median(durations),
        "latency_ms_max": max(durations),
    }
    Path("tmp").mkdir(exist_ok=True)
    Path("tmp/b3-contention.json").write_text(json.dumps(report, indent=2))
