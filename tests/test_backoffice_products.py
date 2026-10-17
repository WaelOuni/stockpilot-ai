from customer_auth import customer_headers
import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Lock
from types import SimpleNamespace
from unittest.mock import MagicMock

from bson import ObjectId
import pytest
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError, OperationFailure
from fastapi.testclient import TestClient

from customer_auth import customer_headers
from test_admin_sessions import api, login, ORIGIN
from stockpilot.main import app
from stockpilot.application.use_cases.catalog_products import GetProductUseCase, ListProductsUseCase
from stockpilot.application.use_cases.manage_products import (
    CreateProductUseCase, UpdateProductMetadataUseCase, DuplicateProductSkuError,
    ProductSkuIndexNotReadyError,
)
from stockpilot.domain.entities.product import Product
from stockpilot.domain.enums.product_category import ProductCategory
from stockpilot.infrastructure.persistence.mongodb.product_repository import MongoProductRepository
from stockpilot.infrastructure.persistence.mongodb.product_indexes import (
    require_unique_sku_index, prepare_unique_sku_index, preflight_skus,
)
from scripts.prepare_product_sku_index import validate_target
from scripts import prepare_product_sku_index as preparation


BODY = {"sku": "NEW-SKU", "name": "New drill", "category": "DRILL", "price": 12.5}
INDEX = {"name": "products_sku_unique", "key": {"sku": 1}, "unique": True}


class Cursor:
    def __init__(self, documents):
        self.documents = deepcopy(documents)

    def sort(self, fields):
        self.documents.sort(key=lambda doc: (doc["sku"], str(doc["_id"])))
        return self

    def skip(self, offset):
        self.documents = self.documents[offset:]
        return self

    def limit(self, limit):
        self.documents = self.documents[:limit]
        return self

    def __iter__(self):
        return iter(self.documents)


class Collection:
    """Unique constraint enforced inside insert lock, not by a find-before-insert."""
    def __init__(self):
        self.documents = []
        self.indexes = [deepcopy(INDEX)]
        self.writes = []
        self.lock = Lock()
        self.before_update = None
        self.before_index = None

    def list_indexes(self):
        return deepcopy(self.indexes)

    def find(self, query, projection=None):
        return Cursor([doc for doc in self.documents if all(doc.get(key) == value for key, value in query.items())])

    def find_one(self, query):
        return next(iter(self.find(query)), None)

    def insert_one(self, document):
        with self.lock:
            if any(index.get("unique") for index in self.indexes) and any(
                doc["sku"] == document["sku"] for doc in self.documents
            ):
                raise DuplicateKeyError("private diagnostic")
            doc = deepcopy(document)
            doc["_id"] = ObjectId()
            self.documents.append(doc)
            self.writes.append(("insert", deepcopy(doc)))
            return SimpleNamespace(inserted_id=doc["_id"])

    def find_one_and_update(self, query, update, *, return_document, collation):
        assert return_document == ReturnDocument.AFTER
        assert collation.document == {"locale": "simple"}
        assert set(update) == {"$set"}
        assert set(update["$set"]) <= {"name", "category", "price"}
        if self.before_update:
            self.before_update()
        with self.lock:
            for document in self.documents:
                if all(document.get(key) == value for key, value in query.items()):
                    document.update(update["$set"])
                    self.writes.append(("update", deepcopy(update)))
                    return deepcopy(document)
        return None

    def create_index(self, keys, **options):
        assert keys == [("sku", 1)]
        assert options["unique"] is True
        assert options["collation"].document == {"locale": "simple"}
        if self.before_index:
            self.before_index()
        if preflight_skus(self)[1]:
            raise DuplicateKeyError("racing duplicate")
        self.indexes.append(deepcopy(INDEX))
        self.writes.append(("index", options))


@pytest.fixture
def product_api(api, monkeypatch):
    client, sessions, clock, settings = api
    collection = Collection()
    repository = MongoProductRepository({"products": collection})
    for key, use_case in (
        ("create_product_use_case", CreateProductUseCase(repository)),
        ("update_product_metadata_use_case", UpdateProductMetadataUseCase(repository)),
        ("get_product_use_case", GetProductUseCase(repository)),
        ("list_products_use_case", ListProductsUseCase(repository)),
    ):
        monkeypatch.setattr(app.state, key, use_case)
    headers = {"Origin": ORIGIN, "X-Admin-CSRF": login(client).json()["csrf_token"]}
    return client, collection, headers, sessions, clock, settings


def mutate(client, headers, edit=False, body=None, sku="NEW-SKU"):
    return client.request(
        "PATCH" if edit else "POST",
        f"/backoffice/products/{sku}" if edit else "/backoffice/products",
        headers=headers, json=body if body is not None else ({"name": "Renamed"} if edit else BODY),
    )


def test_create_edit_public_identity_stock_and_persisted_response(product_api):
    client, collection, headers, *_ = product_api
    result = mutate(client, headers)
    assert result.status_code == 201
    assert result.json() == {
        "sku": "NEW-SKU", "product_name": "New drill", "category": "DRILL", "price": 12.5, "stock": 0,
    }
    identity = collection.documents[0]["_id"]
    order_reference = {"reserved_product_id": identity, "product_name": "New drill"}
    collection.documents[0]["stock"] = 8
    # Reservation occurs between request and atomic metadata update.
    collection.before_update = lambda: collection.documents[0].update(stock=7)
    response = mutate(client, headers, True, {"name": "Renamed", "price": 15, "category": "SAW"})
    assert response.status_code == 200
    assert response.json()["stock"] == 7
    assert collection.documents[0]["_id"] == order_reference["reserved_product_id"]
    assert order_reference["product_name"] == "New drill"
    assert collection.writes[-1] == ("update", {"$set": {"name": "Renamed", "price": 15, "category": "SAW"}})
    assert client.get("/products/NEW-SKU", headers=customer_headers()).json() == response.json()
    assert client.get("/backoffice/products/NEW-SKU").json() == response.json()
    assert mutate(client, headers, True, {"price": 0}).json()["price"] == 0
    assert client.delete("/backoffice/products/NEW-SKU", headers=headers).status_code == 405
    assert len(collection.documents) == 1


def test_duplicate_missing_and_exact_case(product_api):
    client, collection, headers, *_ = product_api
    assert mutate(client, headers).status_code == 201
    before = deepcopy(collection.documents)
    assert mutate(client, headers).status_code == 409
    assert collection.documents == before
    assert mutate(client, headers, True, sku="missing").status_code == 404
    assert client.get("/backoffice/products/missing").status_code == 404
    assert mutate(client, headers, body={**BODY, "sku": "new-sku"}).status_code == 201


def test_admin_list_reuses_has_more_and_public_page(product_api):
    client, collection, headers, *_ = product_api
    for value in range(3):
        assert mutate(client, headers, body={**BODY, "sku": str(value)}).status_code == 201
    for query, count, more in (("?limit=2", 2, True), ("?limit=2&offset=2", 1, False), ("?limit=3", 3, False)):
        response = client.get("/backoffice/products" + query)
        assert response.status_code == 200
        assert response.json()["count"] == count
        assert response.json()["has_more"] is more
        assert response.json() == client.get("/products" + query, headers=customer_headers()).json()
        assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("edit", [False, True])
@pytest.mark.parametrize("change", [
    {"stock": 5}, {"_id": "replacement"}, {"disabled": True}, {"name": ""},
    {"name": " \t"}, {"name": None}, {"name": 42}, {"category": "BOGUS"},
    {"category": None}, {"price": -1}, {"price": "12"}, {"price": True}, {"price": None},
])
def test_invalid_fields_never_mutate(product_api, edit, change):
    client, collection, headers, *_ = product_api
    body = change if edit else {**BODY, **change}
    assert mutate(client, headers, edit, body).status_code == 422
    assert not collection.writes


@pytest.mark.parametrize("body", [{}, {"sku": "OTHER"}, {"sku": "NEW-SKU", "name": "Name"}])
def test_empty_patch_and_even_same_sku_are_forbidden(product_api, body):
    client, collection, headers, *_ = product_api
    assert mutate(client, headers, True, body).status_code == 422
    assert not collection.writes


@pytest.mark.parametrize("sku", ["", " \t", None, 1])
def test_invalid_create_sku(product_api, sku):
    client, collection, headers, *_ = product_api
    assert mutate(client, headers, body={**BODY, "sku": sku}).status_code == 422
    assert not collection.writes


@pytest.mark.parametrize("price", [float("nan"), float("inf"), -float("inf"), -1, True])
def test_use_case_rejects_nonfinite_prices(price):
    collection = Collection()
    with pytest.raises(ValueError):
        asyncio.run(CreateProductUseCase(MongoProductRepository({"products": collection})).execute(
            "SKU", "Name", ProductCategory.DRILL, price,
        ))
    assert not collection.writes


@pytest.mark.parametrize("edit", [False, True])
@pytest.mark.parametrize("violation", [
    "missing", "expired", "revoked", "technical", "jwt", "no-origin", "wrong-origin",
    "duplicate-origin", "no-csrf", "wrong-csrf", "duplicate-csrf", "other-session", "unsafe",
])
def test_cookie_origin_session_bound_csrf_guards(product_api, monkeypatch, edit, violation):
    client, collection, headers, sessions, clock, settings = product_api
    headers = dict(headers)
    status = 403
    if violation == "missing":
        client.cookies.clear()
        status = 401
    elif violation == "expired":
        clock[0] += settings.admin_session_ttl_seconds
        status = 401
    elif violation == "revoked":
        sessions.sessions.clear()
        status = 401
    elif violation == "technical":
        headers["Authorization"] = "Bearer technical-only-secret"
        status = 401
    elif violation == "jwt":
        headers.update(customer_headers())
        status = 401
    elif violation == "no-origin":
        headers.pop("Origin")
    elif violation == "wrong-origin":
        headers["Origin"] += "/"
    elif violation == "no-csrf":
        headers.pop("X-Admin-CSRF")
    elif violation == "wrong-csrf":
        headers["X-Admin-CSRF"] = "wrong"
    elif violation == "other-session":
        headers["X-Admin-CSRF"] = login(TestClient(app, base_url=ORIGIN)).json()["csrf_token"]
    elif violation.startswith("duplicate-"):
        key = "Origin" if violation == "duplicate-origin" else "X-Admin-CSRF"
        headers = [*headers.items(), (key, headers[key])]
    elif violation == "unsafe":
        monkeypatch.setattr(app.state, "settings", settings.model_copy(update={"admin_username": None}))
        status = 503
    assert mutate(client, headers, edit).status_code == status
    assert not collection.writes


def test_read_auth_conventions_unchanged(product_api):
    client, _, headers, *_ = product_api
    assert mutate(client, headers).status_code == 201
    for path in ("/backoffice/products", "/backoffice/products/NEW-SKU"):
        assert login(client).status_code == 200
        assert client.get(path).status_code == 200
        assert client.get(path, headers={"Authorization": "Bearer technical-only-secret"}).status_code == 200
        assert client.get(path, headers=customer_headers()).status_code == 401
        client.cookies.clear()
        assert client.get(path).status_code == 401
        assert client.get(path, headers={"Authorization": "Bearer technical-only-secret"}).status_code == 200
    assert client.get("/products", headers=customer_headers()).status_code == 200
    assert client.get("/orders").status_code == 401


@pytest.mark.parametrize("price", ["NaN", "Infinity", "-Infinity"])
@pytest.mark.parametrize("edit", [False, True])
def test_json_nonfinite_validation_is_sanitized(product_api, price, edit):
    client, collection, headers, *_ = product_api
    body = '{"price":' + price + '}' if edit else (
        '{"sku":"SKU","name":"Name","category":"OTHER","price":' + price + '}'
    )
    response = client.request("PATCH" if edit else "POST",
                              "/backoffice/products/SKU" if edit else "/backoffice/products",
                              content=body, headers={**headers, "Content-Type": "application/json"})
    assert response.status_code == 422
    assert response.json() == {"detail": "Informations produit invalides."}
    assert not collection.writes


@pytest.mark.parametrize("edit", [False, True])
def test_unprovisioned_index_fails_closed_and_sanitized_failure(product_api, edit, monkeypatch):
    client, collection, headers, *_ = product_api
    collection.indexes = []
    assert mutate(client, headers, edit).status_code == 503
    assert not collection.writes
    def fail():
        raise OperationFailure("private uri credentials diagnostics")
    monkeypatch.setattr(collection, "list_indexes", fail)
    response = mutate(client, headers, edit)
    assert response.status_code == 500
    assert "private" not in response.text and "credentials" not in response.text
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("index", [
    {"key": {"sku": 1}}, {**INDEX, "sparse": True},
    {**INDEX, "partialFilterExpression": {"sku": {"$type": "string"}}},
    {**INDEX, "key": {"sku": 1, "_id": 1}}, {**INDEX, "key": {"sku": -1}},
    {**INDEX, "collation": {"locale": "en", "strength": 2}},
])
def test_exact_index_required(index):
    collection = Collection()
    collection.indexes = [index]
    with pytest.raises(ProductSkuIndexNotReadyError):
        require_unique_sku_index(collection)
    assert not collection.writes


@pytest.mark.parametrize("sku", [None, "", " ", [], 4])
def test_index_preflight_blocks_invalid_data_without_writes(sku):
    collection = Collection()
    collection.indexes = []
    collection.documents = [{"sku": sku}, {}]
    assert preflight_skus(collection) == (2, 0)
    with pytest.raises(ValueError):
        prepare_unique_sku_index(collection)
    assert not collection.writes


def test_index_duplicate_preflight_and_authoritative_build_race():
    collection = Collection()
    collection.indexes = []
    collection.documents = [{"sku": "same"}, {"sku": "same"}]
    with pytest.raises(ValueError):
        prepare_unique_sku_index(collection)
    assert not collection.writes
    collection.documents = [{"sku": "same"}]
    collection.before_index = lambda: collection.documents.append({"sku": "same"})
    with pytest.raises(DuplicateKeyError):
        prepare_unique_sku_index(collection)
    assert not collection.indexes and len(collection.documents) == 2


def test_index_preparation_is_nondestructive_idempotent_and_explicit():
    collection = Collection()
    collection.indexes = []
    collection.documents = [{"sku": "SKU"}, {"sku": "sku"}]
    before = deepcopy(collection.documents)
    prepare_unique_sku_index(collection)
    prepare_unique_sku_index(collection)
    assert len(collection.writes) == 1
    assert collection.documents == before
    for confirmation in (None, "", "stockpilot_e2e"):
        with pytest.raises(ValueError):
            validate_target("mongodb://localhost", "stockpilot", confirmation, True)
    validate_target("mongodb://localhost", "stockpilot_e2e", "stockpilot_e2e", True)
    validate_target("mongodb://localhost", "stockpilot", None, False)
    with pytest.raises(ValueError):
        validate_target("mongodb://localhost/stockpilot", "stockpilot_e2e", "stockpilot_e2e", True)


def test_racing_creates_actual_adapter_enforces_constraint_without_prelookup():
    collection = Collection()
    repository = MongoProductRepository({"products": collection})
    product = Product("RACE", "Name", ProductCategory.OTHER, 1, 999)
    def create():
        try:
            return asyncio.run(repository.create_product(product))
        except DuplicateProductSkuError:
            return None
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: create(), range(20)))
    assert sum(result is not None for result in results) == 1
    assert len(collection.documents) == 1
    assert collection.documents[0]["stock"] == 0


def test_metadata_adapter_preserves_p0_reference_and_cancellation_after_rename(monkeypatch):
    from test_order_product_identity import Database, Collection as IdentityCollection, product, confirm, cancel
    original = {**product("Bosch X100", 10, sku="IDENTITY"), "category": "DRILL", "price": 10, "extra": "preserved"}
    database = Database([original])
    monkeypatch.setattr(IdentityCollection, "list_indexes", lambda self: [deepcopy(INDEX)], raising=False)
    confirm(database)
    orders = deepcopy(database.state["orders"])
    result = asyncio.run(UpdateProductMetadataUseCase(MongoProductRepository(database)).execute(
        "IDENTITY", {"name": "Renamed", "category": ProductCategory.SAW, "price": 20},
    ))
    assert result.name == "Renamed" and result.stock == 8
    assert database.state["orders"] == orders
    assert database.state["products"][0]["_id"] == original["_id"]
    assert database.state["products"][0]["extra"] == "preserved"
    cancel(database)
    assert database.state["products"][0]["stock"] == 10
    cancel(database)
    assert database.state["products"][0]["stock"] == 10


@pytest.mark.parametrize("apply", [False, True])
def test_preparation_command_default_read_only_or_explicit_opt_in(monkeypatch, apply, capsys):
    collection = Collection()
    collection.indexes = []
    collection.documents = [{"sku": "SAFE"}]
    database = SimpleNamespace(name="stockpilot_e2e", products=collection)
    client = MagicMock()
    client.__enter__.return_value.__getitem__.return_value = database
    monkeypatch.setattr(preparation, "Settings", lambda: SimpleNamespace(mongo_uri="mongodb://localhost"))
    monkeypatch.setattr(preparation, "MongoClient", MagicMock(return_value=client))
    arguments = ["--database", "stockpilot_e2e"]
    if apply:
        arguments.extend(["--apply", "--confirm-database", "stockpilot_e2e"])
    assert preparation.main(arguments) == 0
    assert len(collection.writes) == int(apply)
    assert collection.documents == [{"sku": "SAFE"}]
    if not apply:
        assert "Activation blocked" in capsys.readouterr().out


def test_preparation_command_refuses_unconfirmed_target_before_connecting(monkeypatch):
    connect = MagicMock()
    monkeypatch.setattr(preparation, "Settings", lambda: SimpleNamespace(mongo_uri="mongodb://localhost"))
    monkeypatch.setattr(preparation, "MongoClient", connect)
    assert preparation.main(["--database", "stockpilot", "--apply"]) == 1
    connect.assert_not_called()


def test_preparation_command_sanitizes_authoritative_index_conflict(monkeypatch, capsys):
    collection = Collection()
    collection.indexes = []
    collection.before_index = lambda: collection.documents.extend([{"sku": "DUP"}, {"sku": "DUP"}])
    client = MagicMock()
    client.__enter__.return_value.__getitem__.return_value = SimpleNamespace(
        name="stockpilot_e2e", products=collection,
    )
    monkeypatch.setattr(preparation, "Settings", lambda: SimpleNamespace(mongo_uri="mongodb://localhost"))
    monkeypatch.setattr(preparation, "MongoClient", MagicMock(return_value=client))
    assert preparation.main(["--database", "stockpilot_e2e", "--apply",
                             "--confirm-database", "stockpilot_e2e"]) == 1
    assert "racing duplicate" not in capsys.readouterr().err
    assert not collection.indexes
