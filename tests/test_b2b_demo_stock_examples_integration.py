"""DEMO adjustment qualification exclusively on UUID disposable test databases."""

import pytest
from bson import ObjectId
from scripts.b2b_demo_stock_examples import build_plan, generated_rows
from test_tenant_stock_operations_mongo_integration import db as db
from test_tenant_stock_operations_mongo_integration import orders as orders
from test_tenant_stock_operations_mongo_integration import stock as stock
from test_tenant_orders_mongo_integration import REQUESTS, context, prepare, confirm
from stockpilot.domain.entities.tenant_product import TenantProductQuery
from stockpilot.domain.entities.tenant_stock_operation import (
    AdjustTenantStock,
    OperationStatus,
)
from stockpilot.infrastructure.persistence.mongodb.tenant_catalogue import (
    MongoTenantCatalogue,
)

pytestmark = pytest.mark.integration


def test_targeted_stock_examples_preserve_orders_reservations_and_tenants(
    db, orders, stock
):
    prepared = prepare(orders, quantity=2)
    confirm(orders, prepared, operation="reserved-demo-control")
    original_orders = list(db.b2b_orders.find({}))
    protected = db.b2b_products.find_one(
        {"organization_id": "org-a", "sku": "DRILL-001"}
    )
    for org, profile in (("org-a", "northline"), ("org-b", "atelierpro")):
        db.b2b_products.insert_many(
            [
                {
                    **row,
                    "_id": ObjectId(),
                    "organization_id": org,
                    "status": "active",
                    "attributes": [],
                    "revision": 1,
                    "reserved": 0,
                    "quantity": 40,
                }
                for row in generated_rows(profile)
            ]
        )
    catalogue = MongoTenantCatalogue(db, orders[0].authorization)
    before_total = db.b2b_products.count_documents({})
    adjusted = 0
    for actor, profile, zeros, lows in (
        ("admin-a", "northline", 25, 20),
        ("admin-b", "atelierpro", 10, 10),
    ):
        ctx = context(orders, actor)
        foreign = list(
            db.b2b_products.find(
                {"organization_id": {"$ne": ctx.organization_id}}
            ).sort("_id", 1)
        )
        plan = build_plan(catalogue, REQUESTS[actor], ctx, profile)
        assert len(plan["adjustments"]) == max(0, zeros - plan["zero_before"]) + max(
            0, lows - plan["low_before"]
        )
        adjusted += len(plan["adjustments"])
        for row in plan["adjustments"]:
            command = AdjustTenantStock(**row["command"])
            result = stock.adjust(REQUESTS[actor], ctx, command)
            assert result.status is OperationStatus.SUCCEEDED
            assert stock.adjust(REQUESTS[actor], ctx, command) == result
        zero_page = catalogue.search_stock(
            REQUESTS[actor],
            ctx,
            TenantProductQuery(status="active", maximum_available=0),
        )
        low_page = catalogue.search_stock(
            REQUESTS[actor],
            ctx,
            TenantProductQuery(status="active", maximum_available=4),
        )
        assert zero_page.total == zeros and low_page.total - zero_page.total == lows
        assert build_plan(catalogue, REQUESTS[actor], ctx, profile)["adjustments"] == []
        assert (
            list(
                db.b2b_products.find(
                    {"organization_id": {"$ne": ctx.organization_id}}
                ).sort("_id", 1)
            )
            == foreign
        )
    assert db.b2b_products.count_documents({}) == before_total
    assert db.b2b_products.find_one({"_id": protected["_id"]}) == protected
    assert list(db.b2b_orders.find({})) == original_orders
    assert (
        db.b2b_stock_movements.count_documents(
            {"reason": {"$regex": "^DEMO availability"}}
        )
        == adjusted
    )
