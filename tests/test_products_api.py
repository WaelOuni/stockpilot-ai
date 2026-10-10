import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from stockpilot.application.use_cases.catalog_products import ListProductsUseCase, GetProductUseCase
from stockpilot.infrastructure.persistence.mongodb.product_repository import MongoProductRepository
from stockpilot.main import app
from test_order_conversation import make_product


@pytest.fixture
def catalogue(monkeypatch):
    products = [make_product("Bosch X100", stock=3), make_product("Makita D200", stock=0)]
    repository = SimpleNamespace(
        list_products=AsyncMock(side_effect=lambda limit, offset, **filters: products[offset:offset+limit]),
        find_by_sku=AsyncMock(side_effect=lambda sku: next((p for p in products if p.sku == sku), None)),
    )
    monkeypatch.setattr(app.state, "list_products_use_case", ListProductsUseCase(repository))
    monkeypatch.setattr(app.state, "get_product_use_case", GetProductUseCase(repository))
    return TestClient(app), repository, products


def test_public_catalogue_contract_and_pagination(catalogue):
    client, repository, _ = catalogue
    response = client.get("/products?limit=1&offset=1")
    assert response.status_code == 200
    payload = response.json()
    assert payload["limit"] == 1 and payload["offset"] == 1 and payload["count"] == 1
    assert set(payload["items"][0]) == {"sku", "product_name", "category", "price", "stock"}
    assert payload["items"][0]["stock"] == 0
    repository.list_products.assert_awaited_once_with(1, 1)
    assert client.get("/products?offset=100").json()["items"] == []
    assert client.get("/products").json()["limit"] == 20


def test_product_detail_public_exact_key_and_missing(catalogue):
    client, repository, products = catalogue
    response = client.get(f"/products/{products[0].sku}")
    assert response.status_code == 200
    assert response.json()["product_name"] == "Bosch X100"
    repository.find_by_sku.assert_awaited_once_with(products[0].sku)
    assert client.get("/products/unknown").status_code == 404
    assert client.get("/products/bosch-x100").status_code == 404


@pytest.mark.parametrize("query", ["limit=0", "limit=101", "limit=x", "offset=-1", "offset=x"])
def test_catalogue_validation(catalogue, query):
    client, repository, _ = catalogue
    assert client.get("/products?"+query).status_code == 422
    repository.list_products.assert_not_awaited()


@pytest.mark.parametrize("limit,offset", [(0,0), (101,0), (True,0), (20,-1), (20,False)])
def test_use_case_pagination_validation(limit, offset):
    repository = SimpleNamespace(list_products=AsyncMock())
    with pytest.raises(ValueError):
        asyncio.run(ListProductsUseCase(repository).execute(limit, offset))
    repository.list_products.assert_not_awaited()


def test_mongo_catalogue_queries_pagination_and_never_writes():
    collection = MagicMock()
    database = MagicMock()
    database.__getitem__.return_value = collection
    repository = MongoProductRepository(database)
    document = {"_id": "internal", "sku": "BOSCH-X100", "name": "Bosch X100",
                "category": "DRILL", "price": 99, "stock": 10, "private": "ignored"}
    cursor = collection.find.return_value
    cursor.sort.return_value.skip.return_value.limit.return_value = [document]
    result = asyncio.run(repository.list_products(20, 5))
    assert result[0].name == "Bosch X100"
    collection.find.assert_called_once_with({})
    cursor.sort.assert_called_once_with([("sku", 1), ("_id", 1)])
    cursor.sort.return_value.skip.assert_called_once_with(5)
    cursor.sort.return_value.skip.return_value.limit.assert_called_once_with(20)
    cursor.limit.return_value = [document]
    assert asyncio.run(repository.find_by_sku("BOSCH-X100")).stock == 10
    collection.find.assert_called_with({"sku": "BOSCH-X100"})
    cursor.limit.return_value = []
    assert asyncio.run(repository.find_by_sku("absent")) is None
    cursor.limit.return_value = [document, document]
    with pytest.raises(ValueError, match="Duplicate"):
        asyncio.run(repository.find_by_sku("BOSCH-X100"))
    for method in ("insert_one", "update_one", "find_one_and_update", "delete_many"):
        getattr(collection, method).assert_not_called()


def test_catalogue_unexpected_failure_is_sanitized(catalogue):
    client, repository, _ = catalogue
    repository.list_products.side_effect = RuntimeError("private")
    response = client.get("/products")
    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error"}
