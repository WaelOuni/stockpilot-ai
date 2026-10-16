from customer_auth import configure_customer_auth, customer_headers
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
        self.sort_fields = fields
        assert fields == [("sku", 1), ("_id", 1)]
        self.rows.sort(key=lambda row: (row["sku"], row.get("_id", "")))
        return self

    def skip(self, offset):
        self.offset = offset
        self.rows = self.rows[offset:]
        return self

    def limit(self, limit):
        self.requested_limit = limit
        return self.rows[:limit]


@pytest.fixture
def catalogue(monkeypatch):
    collection = MagicMock()
    collection.find.side_effect = lambda query: Cursor([dict(row) for row in DOCUMENTS if matches(row, query)])
    database = MagicMock()
    database.__getitem__.return_value = collection
    repository = MongoProductRepository(database)
    monkeypatch.setattr(app.state, "list_products_use_case", ListProductsUseCase(repository))
    configure_customer_auth(monkeypatch, app)
    return TestClient(app, headers=customer_headers()), collection


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


@pytest.mark.parametrize("total,offset,count,has_more", [
    (0, 0, 0, False),
    (2, 0, 2, False),
    (3, 0, 3, False),
    (4, 0, 3, True),
    (6, 3, 3, False),
    (7, 3, 3, True),
    (4, 3, 1, False),
    (4, 10, 0, False),
])
def test_mongo_page_boundaries_in_public_response(catalogue, total, offset, count, has_more):
    client, collection = catalogue
    rows = [
        {**DOCUMENTS[0], "_id": index, "sku": f"SKU-{index:03d}"}
        for index in reversed(range(total))
    ]
    cursors = []

    def find(query):
        cursor = Cursor([dict(row) for row in rows if matches(row, query)])
        cursors.append(cursor)
        return cursor

    collection.find.side_effect = find
    response = client.get(f"/products?limit=3&offset={offset}")
    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "items": [
            {"sku": f"SKU-{index:03d}", "product_name": "Bosch X100",
             "category": "DRILL", "price": 99.0, "stock": 10}
            for index in range(offset, min(total, offset + 3))
        ],
        "limit": 3, "offset": offset, "count": count, "has_more": has_more,
    }
    collection.find.assert_called_once_with({})
    assert cursors[0].sort_fields == [("sku", 1), ("_id", 1)]
    assert cursors[0].offset == offset
    assert cursors[0].requested_limit == 4
    collection.count_documents.assert_not_called()
    for method in ("insert_one", "update_one", "delete_many", "find_one_and_update"):
        getattr(collection, method).assert_not_called()


@pytest.mark.parametrize("offset,names,has_more", [
    (0, ["Bosch A"], True),
    (1, ["Bosch B"], False),
    (2, [], False),
])
def test_has_more_uses_only_combined_filter_matches(catalogue, offset, names, has_more):
    client, collection = catalogue
    rows = [
        {**DOCUMENTS[0], "_id": index, "sku": f"SKU-{index}", "name": name,
         "category": category, "stock": stock}
        for index, (name, category, stock) in enumerate([
            ("Bosch A", "DRILL", 10), ("Bosch B", "DRILL", 1),
            ("Bosch C", "DRILL", 0), ("Bosch D", "SAW", 10),
            ("Makita E", "DRILL", 10),
        ])
    ]
    collection.find.side_effect = lambda query: Cursor([row for row in rows if matches(row, query)])
    payload = client.get(
        f"/products?q=bosch&category=DRILL&available=true&limit=1&offset={offset}",
    ).json()
    assert [item["product_name"] for item in payload["items"]] == names
    assert payload["count"] == len(names)
    assert payload["has_more"] is has_more
    collection.find.assert_called_once()
    collection.count_documents.assert_not_called()


def test_stable_sku_and_id_sort_across_pages(catalogue):
    client, collection = catalogue
    rows = [
        {**DOCUMENTS[0], "_id": identity, "sku": sku, "name": name}
        for identity, sku, name in [
            (3, "A", "Third"), (0, "B", "Fourth"),
            (2, "A", "Second"), (1, "A", "First"),
        ]
    ]
    collection.find.side_effect = lambda query: Cursor([dict(row) for row in rows])
    pages = [client.get(f"/products?limit=2&offset={offset}").json() for offset in (0, 2)]
    assert [[item["product_name"] for item in page["items"]] for page in pages] == [
        ["First", "Second"], ["Third", "Fourth"],
    ]
    assert [page["has_more"] for page in pages] == [True, False]


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
