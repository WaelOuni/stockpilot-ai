"""Pagination totals are computed by Mongo, from the exact same bounded filter."""
import asyncio
from types import SimpleNamespace
from unittest.mock import Mock, AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from stockpilot.infrastructure.persistence.mongodb.pagination import read_page
from stockpilot.infrastructure.persistence.mongodb.product_repository import MongoProductRepository
from stockpilot.infrastructure.persistence.mongodb.order_repository import MongoOrderRepository
from stockpilot.application.use_cases.catalog_products import ListProductsUseCase
from stockpilot.application.use_cases.list_customer_orders import ListCustomerOrdersUseCase
from stockpilot.domain.enums.product_category import ProductCategory
from stockpilot.presentation.api.routes import products
from stockpilot.presentation.api.dependencies import get_list_products_use_case


def run(value):
    return asyncio.run(value)


def test_single_aggregation_keeps_count_and_page_filters_identical():
    collection = Mock()
    collection.aggregate.return_value = [{"items": [{"sku": "A"}], "total": [{"value": 51}]}]
    query = {"category": "DRILL", "stock": {"$gt": 0}}
    assert read_page(collection, query, [("sku", 1), ("_id", 1)], 20, 40) == ([{"sku": "A"}], 51)
    pipeline = collection.aggregate.call_args.args[0]
    assert pipeline == [{"$match": query}, {"$facet": {"items": [
        {"$sort": {"sku": 1, "_id": 1}}, {"$skip": 40}, {"$limit": 20}], "total": [{"$count": "value"}]}}]
    collection.find.assert_not_called()


def test_empty_aggregation_has_exact_zero_total():
    collection = Mock()
    collection.aggregate.return_value = [{"items": [], "total": []}]
    assert read_page(collection, {}, [("sku", 1)], 20, 0) == ([], 0)


def test_product_combined_filters_literal_sku_search_and_exact_total():
    collection = Mock()
    collection.aggregate.return_value = [{"items": [], "total": [{"value": 45}]}]
    repo = MongoProductRepository({"products": collection})
    page = run(repo.list_products(20, 40, q="SKU.*", category=ProductCategory.DRILL, available=False, include_total=True))
    assert page.total == 45 and page.has_more
    assert collection.aggregate.call_args.args[0][0] == {"$match": {
        "$or": [{"sku": {"$regex": r"SKU\.\*", "$options": "i"}}, {"name": {"$regex": r"SKU\.\*", "$options": "i"}}],
        "category": "DRILL", "stock": {"$lte": 0}}}


def test_order_search_preserves_customer_ownership_before_count_and_skip():
    collection = Mock()
    collection.aggregate.return_value = [{"items": [], "total": []}]
    page = run(MongoOrderRepository({"orders": collection}).list_page(20, 20, customer_id="owner", q="a+b"))
    assert page.total == 0 and not page.has_more
    query = collection.aggregate.call_args.args[0][0]["$match"]
    assert query["customer_id"] == "owner"
    assert query["$or"] == [{key: {"$regex": r"a\+b", "$options": "i"}} for key in ("order_id", "product_name", "customer_id")]


@pytest.mark.parametrize('customer', [None, '', '   '])
def test_customer_page_never_counts_without_valid_identity(customer):
    repo = SimpleNamespace(list_page=AsyncMock())
    with pytest.raises(ValueError):
        run(ListCustomerOrdersUseCase(repo).execute_page(customer_id=customer))
    repo.list_page.assert_not_awaited()


def test_public_api_opt_in_preserves_legacy_shape():
    collection = Mock()
    collection.find.return_value.sort.return_value.skip.return_value.limit.return_value = []
    collection.aggregate.return_value = [{"items": [], "total": []}]
    app = FastAPI()
    app.include_router(products.router)
    app.dependency_overrides[get_list_products_use_case] = lambda: ListProductsUseCase(MongoProductRepository({"products": collection}))
    with TestClient(app) as client:
        assert client.get('/products').json() == {"items": [], "limit": 20, "offset": 0, "count": 0, "has_more": False}
        assert client.get('/products?include_total=true&q=SKU&category=DRILL&available=true').json() == {
            "items": [], "limit": 20, "offset": 0, "count": 0, "has_more": False, "total": 0}
        assert client.get('/products?include_total=true&limit=0').status_code == 422
