import asyncio
import re
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from stockpilot.application.use_cases.catalog_products import ListProductsUseCase
from stockpilot.infrastructure.persistence.mongodb.product_repository import MongoProductRepository
from stockpilot.main import app


DOCUMENTS = [
    {"sku": "BX", "name": "Bosch X100", "category": "DRILL", "price": 99, "stock": 10},
    {"sku": "MD", "name": "Makita D200", "category": "DRILL", "price": 169, "stock": 0},
    {"sku": "BS", "name": "Bosch S500", "category": "SAW", "price": 199, "stock": 5},
    {"sku": "NEG", "name": "Spare [part]", "category": "OTHER", "price": 1, "stock": -1},
]


def matches(document, query):
    for key, value in query.items():
        if key == "$or":
            if not any(any(re.search(regex["$regex"], document[field], re.I) for field, regex in clause.items()) for clause in value):
                return False
        elif key == "stock":
            if "$gt" in value and not document[key] > value["$gt"]:
                return False
            if "$lte" in value and not document[key] <= value["$lte"]:
                return False
        elif document[key] != value:
            return False
    return True


class Cursor:
    def __init__(self, rows):
        self.rows = rows

    def sort(self, fields):
        assert fields == [("sku", 1), ("_id", 1)]
        self.rows.sort(key=lambda row: row["sku"])
        return self

    def skip(self, offset):
        self.rows = self.rows[offset:]
        return self

    def limit(self, limit):
        return self.rows[:limit]


@pytest.fixture
def catalogue(monkeypatch):
    collection = MagicMock()
    collection.find.side_effect = lambda query: Cursor([dict(row) for row in DOCUMENTS if matches(row, query)])
    database = MagicMock()
    database.__getitem__.return_value = collection
    repository = MongoProductRepository(database)
    monkeypatch.setattr(app.state, "list_products_use_case", ListProductsUseCase(repository))
    return TestClient(app), collection


@pytest.mark.parametrize("query,names", [
    ("q=x100", ["Bosch X100"]),
    ("q=bx", ["Bosch X100"]),
    ("q=BoScH", ["Bosch S500", "Bosch X100"]),
    ("category=DRILL", ["Bosch X100", "Makita D200"]),
    ("available=true", ["Bosch S500", "Bosch X100"]),
    ("available=false", ["Makita D200", "Spare [part]"]),
    ("q=bosch&category=DRILL&available=true", ["Bosch X100"]),
    ("q=bosch&limit=1&offset=1", ["Bosch X100"]),
    ("q=makita&available=true", []),
    ("q=%5Bpart%5D", ["Spare [part]"]),
    ("q=.*", []),
    ("q=%20%20", ["Bosch S500", "Bosch X100", "Makita D200", "Spare [part]"]),
])
def test_filters_combine_in_mongo_before_pagination_without_writes(catalogue, query, names):
    client, collection = catalogue
    response = client.get("/products?" + query)
    assert response.status_code == 200
    assert [item["product_name"] for item in response.json()["items"]] == names
    assert response.json()["count"] == len(names)
    for method in ("insert_one", "update_one", "delete_many", "find_one_and_update"):
        getattr(collection, method).assert_not_called()


def test_query_is_literal_and_combined(catalogue):
    client, collection = catalogue
    assert client.get("/products?q=%20%5Bpart%5D%20&category=OTHER&available=false").status_code == 200
    collection.find.assert_called_once_with({
        "$or": [
            {"sku": {"$regex": r"\[part\]", "$options": "i"}},
            {"name": {"$regex": r"\[part\]", "$options": "i"}},
        ],
        "category": "OTHER", "stock": {"$lte": 0},
    })


@pytest.mark.parametrize("query", ["category=INVALID", "category=drill", "available=invalid", "q=" + "x" * 101])
def test_invalid_filters_return_422_before_read(catalogue, query):
    client, collection = catalogue
    assert client.get("/products?" + query).status_code == 422
    collection.find.assert_not_called()


@pytest.mark.parametrize("filters", [{"q": 1}, {"q": "x" * 101}, {"category": "DRILL"}, {"available": 1}])
def test_use_case_rejects_invalid_filter_types(filters):
    repo = SimpleNamespace()
    with pytest.raises(ValueError):
        asyncio.run(ListProductsUseCase(repo).execute(**filters))
