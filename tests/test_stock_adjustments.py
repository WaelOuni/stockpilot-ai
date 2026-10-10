import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
from threading import RLock
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import MagicMock

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient
from pymongo import ReturnDocument
from pymongo.errors import OperationFailure, PyMongoError

from stockpilot.application.use_cases.adjust_product_stock import (
    AdjustProductStockUseCase,
    GetStockAdjustmentUseCase,
    InsufficientStockError,
    StockAdjustmentConflictError,
    StockAdjustmentOutcomeUnknownError,
)
from stockpilot.domain.entities.product import Product
from stockpilot.domain.enums.product_category import ProductCategory
from stockpilot.infrastructure.persistence.mongodb.product_repository import MongoProductRepository
from stockpilot.infrastructure.persistence.mongodb.stock_adjustment_transaction import (
    MongoStockAdjustmentTransaction,
)
from stockpilot.infrastructure.persistence.mongodb.stock_movement_indexes import (
    HISTORY_INDEX_NAME,
    OPERATION_INDEX_NAME,
    prepare_stock_movement_indexes,
    preflight_stock_movements,
    require_unique_operation_index,
)
from stockpilot.application.use_cases.adjust_product_stock import StockMovementIndexNotReadyError
from stockpilot.main import app
from customer_auth import customer_headers
from test_admin_sessions import ORIGIN, api, login
from scripts import prepare_stock_movement_indexes as preparation


OPERATION_INDEX = {
    "name": OPERATION_INDEX_NAME,
    "key": {"operation_id": 1},
    "unique": True,
}


class LabeledError(PyMongoError):
    def __init__(self, label):
        super().__init__("private transaction details")
        self.label = label

    def has_error_label(self, label):
        return label in self.label.split(",")


class Cursor:
    def __init__(self, documents):
        self.documents = deepcopy(documents)

    def limit(self, count):
        self.documents = self.documents[:count]
        return self

    def sort(self, fields):
        for key, direction in reversed(fields):
            self.documents.sort(key=lambda value: value[key], reverse=direction < 0)
        return self

    def skip(self, offset):
        self.documents = self.documents[offset:]
        return self

    def __iter__(self):
        return iter(self.documents)


class Session:
    def __init__(self, database):
        self.database = database
        self.in_transaction = False
        self.documents = {}

    def start_transaction(self, **_):
        self.database.lock.acquire()
        self.documents = {
            name: deepcopy(collection.documents)
            for name, collection in self.database.collections.items()
        }
        self.in_transaction = True

    def commit_transaction(self):
        mode = self.database.client.commit_modes.pop(0) if self.database.client.commit_modes else None
        self.in_transaction = False
        if mode != "unknown_abort" and mode != "transient_abort":
            for name, documents in self.documents.items():
                self.database.collections[name].documents = documents
        self.database.lock.release()
        if mode == "unknown_commit":
            raise LabeledError("UnknownTransactionCommitResult")
        if mode == "unknown_abort":
            raise LabeledError("UnknownTransactionCommitResult")
        if mode == "unknown_and_transient":
            raise LabeledError("UnknownTransactionCommitResult,TransientTransactionError")
        if mode == "transient_abort":
            raise LabeledError("TransientTransactionError")

    def abort_transaction(self):
        if self.in_transaction:
            self.in_transaction = False
            self.database.lock.release()

    def end_session(self):
        self.abort_transaction()


class Client:
    def __init__(self, database):
        self.database = database
        self.commit_modes = []

    def start_session(self):
        return Session(self.database)


class Collection:
    def __init__(self, database, name, indexes=None):
        self.database = database
        self.name = name
        self.documents = []
        self.indexes = indexes or [{"name": "_id_", "key": {"_id": 1}}]
        self.fail_insert = False

    def _documents(self, session=None):
        return session.documents[self.name] if session is not None else self.documents

    @staticmethod
    def _matches(document, query):
        for key, expected in query.items():
            actual = document.get(key)
            if isinstance(expected, dict):
                if "$type" in expected and type(actual) is not int:
                    return False
                if "$gte" in expected and (not isinstance(actual, int) or actual < expected["$gte"]):
                    return False
                if "$lte" in expected and (not isinstance(actual, int) or actual > expected["$lte"]):
                    return False
            elif actual != expected:
                return False
        return True

    @staticmethod
    def _project(document, projection):
        if not projection:
            return deepcopy(document)
        if any(value == 1 for value in projection.values()):
            result = {key: deepcopy(document[key]) for key, value in projection.items()
                      if value == 1 and key in document}
            if projection.get("_id", 1) and "_id" in document:
                result["_id"] = deepcopy(document["_id"])
            return result
        return {key: deepcopy(value) for key, value in document.items() if not projection.get(key) == 0}

    def find(self, query, projection=None, **kwargs):
        session = kwargs.get("session")
        return Cursor([
            self._project(document, projection)
            for document in self._documents(session)
            if self._matches(document, query)
        ])

    def find_one(self, query, projection=None, **kwargs):
        return next(iter(self.find(query, projection, **kwargs)), None)

    def find_one_and_update(self, query, update, *, return_document, session, **_):
        assert return_document == ReturnDocument.AFTER
        assert set(update) == {"$inc"} and set(update["$inc"]) == {"stock"}
        for document in self._documents(session):
            if self._matches(document, query):
                document["stock"] += update["$inc"]["stock"]
                return deepcopy(document)
        return None

    def insert_one(self, document, session=None):
        if self.fail_insert:
            raise OperationFailure("private forced insert failure", code=121)
        target = self._documents(session)
        if any(
            index.get("unique") and index["key"] == {"operation_id": 1}
            and item.get("operation_id") == document.get("operation_id")
            for index in self.indexes for item in target
        ):
            raise OperationFailure("duplicate operation")
        value = deepcopy(document)
        value["_id"] = ObjectId()
        target.append(value)
        return SimpleNamespace(inserted_id=value["_id"])

    def list_indexes(self):
        return deepcopy(self.indexes)

    def with_options(self, **_):
        return self

    def create_index(self, keys, **options):
        if "collation" in options:
            options["collation"] = options["collation"].document
        self.indexes.append({"name": options["name"], "key": dict(keys), **options})


class Database:
    def __init__(self):
        self.lock = RLock()
        self.collections = {}
        self.client = Client(self)
        self.collections["products"] = Collection(self, "products")
        self.collections["stock_movements"] = Collection(self, "stock_movements", [OPERATION_INDEX])
        self.collections["products"].documents.append({
            "_id": ObjectId(), "sku": "SKU-1", "name": "Drill", "category": "DRILL",
            "price": 12.5, "stock": 5,
        })

    def __getitem__(self, name):
        return self.collections[name]


def run(coro):
    return asyncio.run(coro)


def create_movement(repo, operation_id=None, delta=2, reason="stock count"):
    return run(AdjustProductStockUseCase(repo).execute(
        "SKU-1", delta, reason, operation_id or str(uuid4()),
    ))


def test_adjustment_uses_atomic_inc_and_records_snapshots():
    database = Database()
    repo = MongoProductRepository(database)
    result = create_movement(repo)
    assert result.stock_before == 5
    assert result.stock_after == 7
    assert result.source == "BACKOFFICE"
    assert database["products"].documents[0]["stock"] == 7
    stored = database["stock_movements"].documents[0]
    assert stored["product_id"] == database["products"].documents[0]["_id"]
    assert stored["_id"] not in result.__dict__.values()
    assert "product_id" not in result.__dict__
    assert stored["occurred_at"].tzinfo == timezone.utc


@pytest.mark.parametrize("delta,expected", [(2, 7), (-3, 2)])
def test_positive_and_negative_adjustments(delta, expected):
    database = Database()
    repo = MongoProductRepository(database)
    result = create_movement(repo, delta=delta)
    assert result.stock_after == expected
    assert database["products"].documents[0]["stock"] == expected


def test_insufficient_stock_missing_product_and_zero_stock_refusal():
    database = Database()
    repo = MongoProductRepository(database)
    with pytest.raises(InsufficientStockError):
        create_movement(repo, delta=-6)
    assert database["products"].documents[0]["stock"] == 5
    assert database["stock_movements"].documents == []
    database["products"].documents[0]["stock"] = 0
    with pytest.raises(InsufficientStockError):
        create_movement(repo, delta=-1)
    with pytest.raises(Exception) as missing:
        run(AdjustProductStockUseCase(repo).execute("missing", 1, "correction", str(uuid4())))
    assert type(missing.value).__name__ == "ProductStockNotFoundError"
    assert database["stock_movements"].documents == []


@pytest.mark.parametrize(("sku", "delta", "reason", "operation_id"), [
    ("SKU-1", True, "reason", str(uuid4())),
    ("SKU-1", 0, "reason", str(uuid4())),
    ("SKU-1", 1, "   ", str(uuid4())),
    ("SKU-1", 1, "x" * 501, str(uuid4())),
    ("SKU-1", 1, "reason", "not-a-uuid"),
])
def test_use_case_rejects_invalid_payload_before_writing(sku, delta, reason, operation_id):
    database = Database()
    repo = MongoProductRepository(database)
    with pytest.raises(ValueError):
        run(AdjustProductStockUseCase(repo).execute(sku, delta, reason, operation_id))
    assert database["products"].documents[0]["stock"] == 5
    assert database["stock_movements"].documents == []


def test_insert_failure_rolls_back_stock_and_movement():
    database = Database()
    database["stock_movements"].fail_insert = True
    with pytest.raises(Exception):
        create_movement(MongoProductRepository(database))
    assert database["products"].documents[0]["stock"] == 5
    assert database["stock_movements"].documents == []


def test_operation_replay_conflict_and_concurrent_identical_requests():
    database = Database()
    repo = MongoProductRepository(database)
    operation_id = str(uuid4())
    first = create_movement(repo, operation_id)
    replay = create_movement(repo, operation_id)
    assert replay == first
    assert database["products"].documents[0]["stock"] == 7
    assert len(database["stock_movements"].documents) == 1
    with pytest.raises(StockAdjustmentConflictError):
        create_movement(repo, operation_id, delta=3)

    other_db = Database()
    other_repo = MongoProductRepository(other_db)
    concurrent_id = str(uuid4())
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(
            lambda _: create_movement(other_repo, concurrent_id),
            range(16),
        ))
    assert all(value == results[0] for value in results)
    assert other_db["products"].documents[0]["stock"] == 7
    assert len(other_db["stock_movements"].documents) == 1


def test_bounded_known_uncommitted_retry_and_ambiguous_reconciliation():
    database = Database()
    repo = MongoProductRepository(database)
    database.client.commit_modes = ["transient_abort"]
    movement = create_movement(repo)
    assert movement.stock_after == 7
    assert database["products"].documents[0]["stock"] == 7
    assert len(database["stock_movements"].documents) == 1

    committed = Database()
    committed.client.commit_modes = ["unknown_commit"]
    recovered = create_movement(MongoProductRepository(committed))
    assert recovered.stock_after == 7
    assert committed["products"].documents[0]["stock"] == 7
    assert len(committed["stock_movements"].documents) == 1

    unknown = Database()
    unknown.client.commit_modes = ["unknown_abort"]
    with pytest.raises(StockAdjustmentOutcomeUnknownError):
        create_movement(MongoProductRepository(unknown))
    assert unknown["products"].documents[0]["stock"] == 5
    assert unknown["stock_movements"].documents == []


def test_missing_operation_index_fails_closed():
    database = Database()
    database["stock_movements"].indexes = [{"name": "_id_", "key": {"_id": 1}}]
    with pytest.raises(StockMovementIndexNotReadyError):
        create_movement(MongoProductRepository(database))
    assert database["products"].documents[0]["stock"] == 5
    assert database["stock_movements"].documents == []


def test_stock_movement_history_is_stably_ordered_and_paginated():
    database = Database()
    timestamp = datetime(2026, 10, 10, tzinfo=timezone.utc)
    database["stock_movements"].documents = [
        {
            "movement_id": f"movement-{index:02d}",
            "operation_id": str(uuid4()),
            "product_sku": "SKU-1",
            "delta": 1,
            "stock_before": index,
            "stock_after": index + 1,
            "reason": "count",
            "occurred_at": timestamp,
            "source": "BACKOFFICE",
            "product_id": ObjectId(),
            "_id": ObjectId(),
        }
        for index in range(23)
    ]
    repo = MongoProductRepository(database)
    first = run(repo.list_stock_movements("SKU-1", 20, 0))
    second = run(repo.list_stock_movements("SKU-1", 20, 20))
    assert first.has_more and len(first.items) == 20
    assert not second.has_more and len(second.items) == 3
    assert first.items[0].movement_id == "movement-22"
    assert second.items[0].movement_id == "movement-02"
    assert all(not hasattr(item, "product_id") for item in first.items)


def test_index_preflight_is_nondestructive_and_idempotent():
    database = Database()
    collection = database["stock_movements"]
    collection.indexes = [{"name": "_id_", "key": {"_id": 1}}]
    assert preflight_stock_movements(collection) == (0, 0)
    before = deepcopy(collection.documents)
    prepare_stock_movement_indexes(collection)
    prepare_stock_movement_indexes(collection)
    assert collection.documents == before
    assert require_unique_operation_index(collection) is None
    assert {index["name"] for index in collection.indexes} >= {
        OPERATION_INDEX_NAME, HISTORY_INDEX_NAME,
    }
    collection.documents = [{"operation_id": "same"}, {"operation_id": "same"}]
    assert preflight_stock_movements(collection) == (0, 1)


@pytest.fixture
def stock_api(api, monkeypatch):
    client, *_ = api
    database = Database()
    repository = MongoProductRepository(database)
    monkeypatch.setattr(app.state, "adjust_product_stock_use_case", AdjustProductStockUseCase(repository))
    monkeypatch.setattr(app.state, "get_stock_adjustment_use_case", GetStockAdjustmentUseCase(repository))
    from stockpilot.application.use_cases.adjust_product_stock import ListStockMovementsUseCase
    monkeypatch.setattr(app.state, "list_stock_movements_use_case", ListStockMovementsUseCase(repository))
    csrf = login(client).json()["csrf_token"]
    return client, database, {"Origin": ORIGIN, "X-Admin-CSRF": csrf}


def test_protected_adjustment_history_and_operation_lookup_contract(stock_api):
    client, database, headers = stock_api
    operation_id = str(uuid4())
    response = client.post(
        "/backoffice/products/SKU-1/stock-adjustments",
        headers=headers, json={"operation_id": operation_id, "delta": 3, "reason": "  recount  "},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "movement_id": payload["movement_id"],
        "operation_id": operation_id,
        "product_sku": "SKU-1",
        "delta": 3,
        "stock_before": 5,
        "stock_after": 8,
        "reason": "recount",
        "occurred_at": payload["occurred_at"],
        "source": "BACKOFFICE",
    }
    assert "_id" not in payload and "product_id" not in payload
    assert client.get("/backoffice/stock-adjustments/" + operation_id).json() == payload
    history = client.get("/backoffice/products/SKU-1/stock-movements")
    assert history.status_code == 200
    assert history.json()["items"] == [payload]
    assert history.json()["has_more"] is False
    assert history.headers["cache-control"] == "no-store"
    assert client.get("/backoffice/products/missing/stock-movements").status_code == 404
    assert client.get("/backoffice/stock-adjustments/" + operation_id,
                      headers={"Authorization": "Bearer technical-only-secret"}).status_code == 401
    assert client.get("/backoffice/products/SKU-1/stock-movements",
                      headers=customer_headers()).status_code == 401
    assert client.post("/backoffice/products/SKU-1/stock-adjustments",
                       headers={**headers, "Authorization": "Bearer technical-only-secret"},
                       json={"operation_id": str(uuid4()), "delta": 1, "reason": "manual"}).status_code == 401


@pytest.mark.parametrize("change", [
    {"delta": True}, {"delta": 0}, {"delta": "2"}, {"delta": 1.5},
    {"delta": 1_000_001}, {"reason": "  "}, {"reason": "x" * 501},
    {"operation_id": "not-uuid"}, {"extra": "not-allowed"},
])
def test_api_validation_never_writes(stock_api, change):
    client, database, headers = stock_api
    body = {"operation_id": str(uuid4()), "delta": 2, "reason": "inventory"}
    response = client.post(
        "/backoffice/products/SKU-1/stock-adjustments",
        headers=headers, json={**body, **change},
    )
    assert response.status_code == 422
    assert response.json() == {"detail": "Ajustement de stock invalide."}
    assert database["products"].documents[0]["stock"] == 5
    assert database["stock_movements"].documents == []


@pytest.mark.parametrize("violation,status", [
    ("unauthenticated", 401), ("customer", 401), ("technical", 401),
    ("no-origin", 403), ("wrong-origin", 403), ("no-csrf", 403),
])
def test_adjustment_requires_cookie_session_origin_and_csrf(stock_api, violation, status):
    client, database, headers = stock_api
    request_headers = dict(headers)
    if violation == "unauthenticated":
        client.cookies.clear()
        request_headers = {"Origin": ORIGIN, "X-Admin-CSRF": headers["X-Admin-CSRF"]}
    elif violation == "customer":
        request_headers = customer_headers()
    elif violation == "technical":
        request_headers["Authorization"] = "Bearer technical-only-secret"
    elif violation == "no-origin":
        request_headers.pop("Origin")
    elif violation == "wrong-origin":
        request_headers["Origin"] += "/"
    elif violation == "no-csrf":
        request_headers.pop("X-Admin-CSRF")
    response = client.post(
        "/backoffice/products/SKU-1/stock-adjustments",
        headers=request_headers,
        json={"operation_id": str(uuid4()), "delta": 1, "reason": "count"},
    )
    assert response.status_code == status
    assert database["products"].documents[0]["stock"] == 5
    assert database["stock_movements"].documents == []


def test_client_idempotency_lookup_is_not_public_or_technical_token_access(stock_api):
    client, _, headers = stock_api
    operation_id = str(uuid4())
    assert client.get("/backoffice/stock-adjustments/" + operation_id).status_code == 404
    client.cookies.clear()
    assert client.get(
        "/backoffice/stock-adjustments/" + operation_id,
        headers={"Authorization": "Bearer technical-only-secret"},
    ).status_code == 401
    assert client.get("/backoffice/stock-adjustments/" + operation_id,
                      headers=customer_headers()).status_code == 401


@pytest.mark.parametrize("index", [
    {"key": {"operation_id": 1}},
    {**OPERATION_INDEX, "sparse": True},
    {**OPERATION_INDEX, "partialFilterExpression": {"operation_id": {"$type": "string"}}},
    {**OPERATION_INDEX, "key": {"operation_id": 1, "product_sku": 1}},
    {**OPERATION_INDEX, "key": {"operation_id": -1}},
    {**OPERATION_INDEX, "expireAfterSeconds": 10},
    {**OPERATION_INDEX, "collation": {"locale": "en", "strength": 2}},
])
def test_operation_index_must_enforce_all_exact_ids(index):
    database = Database()
    database["stock_movements"].indexes = [index]
    with pytest.raises(StockMovementIndexNotReadyError):
        create_movement(MongoProductRepository(database))
    assert database["products"].documents[0]["stock"] == 5
    assert database["stock_movements"].documents == []


def test_absent_collection_is_unprovisioned_not_a_technical_error(monkeypatch):
    collection = Database()["stock_movements"]
    def absent():
        raise OperationFailure("NamespaceNotFound", code=26)
    monkeypatch.setattr(collection, "list_indexes", absent)
    with pytest.raises(StockMovementIndexNotReadyError):
        require_unique_operation_index(collection)


def test_unknown_commit_label_takes_precedence_over_transient_retry():
    database = Database()
    database.client.commit_modes = ["unknown_and_transient", "unknown_abort"]
    movement = create_movement(MongoProductRepository(database))
    assert movement.stock_after == 7
    assert database.client.commit_modes == ["unknown_abort"]
    assert len(database["stock_movements"].documents) == 1


def test_known_abort_retry_limit_does_not_write_or_invent_success():
    database = Database()
    database.client.commit_modes = ["transient_abort"] * 4
    from stockpilot.application.use_cases.adjust_product_stock import StockAdjustmentTransactionFailedError
    with pytest.raises(StockAdjustmentTransactionFailedError):
        create_movement(MongoProductRepository(database))
    assert database.client.commit_modes == ["transient_abort"]
    assert database["products"].documents[0]["stock"] == 5
    assert database["stock_movements"].documents == []


@pytest.mark.parametrize("stock", [True, "5", 5.5, None, -1, 9_223_372_036_854_775_807])
def test_invalid_or_overflowing_stored_stock_is_never_changed(stock):
    database = Database()
    database["products"].documents[0]["stock"] = stock
    from stockpilot.application.use_cases.adjust_product_stock import StockAdjustmentTransactionFailedError
    with pytest.raises(StockAdjustmentTransactionFailedError):
        create_movement(MongoProductRepository(database))
    assert database["products"].documents[0]["stock"] == stock
    assert database["stock_movements"].documents == []


def test_ambiguous_sku_refused_before_any_mutation():
    database = Database()
    database["products"].documents.append({
        **database["products"].documents[0], "_id": ObjectId(), "stock": 0,
    })
    from stockpilot.application.use_cases.adjust_product_stock import StockAdjustmentAmbiguousProductError
    with pytest.raises(StockAdjustmentAmbiguousProductError):
        create_movement(MongoProductRepository(database))
    assert [item["stock"] for item in database["products"].documents] == [5, 0]
    assert database["stock_movements"].documents == []


def test_replay_normalizes_reason_and_rejects_other_sku_or_reason():
    database = Database()
    repository = MongoProductRepository(database)
    operation_id = str(uuid4())
    first = create_movement(repository, operation_id, reason="  count  ")
    assert create_movement(repository, operation_id, reason="count") == first
    for sku, reason in [("SKU-2", "count"), ("SKU-1", "different")]:
        with pytest.raises(StockAdjustmentConflictError):
            run(AdjustProductStockUseCase(repository).execute(sku, 2, reason, operation_id))
    assert database["products"].documents[0]["stock"] == 7
    assert len(database["stock_movements"].documents) == 1


def test_api_replay_conflict_insufficient_and_index_failclosed(stock_api):
    client, database, headers = stock_api
    operation_id = str(uuid4())
    path = "/backoffice/products/SKU-1/stock-adjustments"
    body = {"operation_id": operation_id, "delta": -5, "reason": "count"}
    first = client.post(path, headers=headers, json=body)
    assert first.status_code == 200 and first.json()["stock_after"] == 0
    assert client.post(path, headers=headers, json=body).json() == first.json()
    assert client.post(path, headers=headers, json={**body, "reason": "other"}).status_code == 409
    assert client.post(path, headers=headers, json={**body, "operation_id": str(uuid4())}).status_code == 409
    database["stock_movements"].indexes = []
    response = client.post(path, headers=headers, json={**body, "delta": 1})
    assert response.status_code == 503 and response.headers["cache-control"] == "no-store"
    assert database["products"].documents[0]["stock"] == 0
    assert len(database["stock_movements"].documents) == 1


@pytest.mark.parametrize("query", ["limit=0", "limit=101", "offset=-1", "limit=bad"])
def test_history_rejects_invalid_pagination(stock_api, query):
    client, *_ = stock_api
    assert client.get("/backoffice/products/SKU-1/stock-movements?" + query).status_code == 422


@pytest.mark.parametrize("header,value", [("Origin", ORIGIN), ("X-Admin-CSRF", "duplicate")])
def test_duplicate_security_headers_never_write(stock_api, header, value):
    client, database, headers = stock_api
    response = client.post(
        "/backoffice/products/SKU-1/stock-adjustments",
        headers=[*headers.items(), (header, value)],
        json={"operation_id": str(uuid4()), "delta": 1, "reason": "count"},
    )
    assert response.status_code == 403
    assert database["stock_movements"].documents == []


@pytest.mark.parametrize("apply", [False, True])
def test_provision_command_is_readonly_without_explicit_opt_in(monkeypatch, apply):
    collection = Database()["stock_movements"]
    collection.indexes = []
    collection.documents = [{"operation_id": str(uuid4())}]
    before = deepcopy(collection.documents)
    database = MagicMock()
    database.name = "stockpilot_e2e"
    database.__getitem__.return_value = collection
    client = MagicMock()
    client.__enter__.return_value.__getitem__.return_value = database
    monkeypatch.setattr(preparation, "Settings", lambda: SimpleNamespace(mongo_uri="mongodb://localhost"))
    monkeypatch.setattr(preparation, "MongoClient", MagicMock(return_value=client))
    arguments = ["--database", "stockpilot_e2e"]
    if apply:
        arguments.extend(["--apply", "--confirm-database", "stockpilot_e2e"])
    assert preparation.main(arguments) == 0
    assert bool(collection.indexes) is apply
    assert collection.documents == before


@pytest.mark.parametrize("uri,database,confirmation", [
    ("mongodb://localhost", "stockpilot", None),
    ("mongodb://localhost", "stockpilot", "stockpilot_e2e"),
    ("mongodb://localhost/stockpilot", "stockpilot_e2e", "stockpilot_e2e"),
    ("mongodb://localhost", " stockpilot", " stockpilot"),
])
def test_provision_command_requires_exact_target_confirmation(uri, database, confirmation):
    with pytest.raises(ValueError):
        preparation.validate_target(uri, database, confirmation, True)


def test_unconfirmed_provision_refused_before_connecting(monkeypatch):
    connect = MagicMock()
    monkeypatch.setattr(preparation, "Settings", lambda: SimpleNamespace(mongo_uri="mongodb://localhost"))
    monkeypatch.setattr(preparation, "MongoClient", connect)
    assert preparation.main(["--database", "stockpilot", "--apply"]) == 1
    connect.assert_not_called()


def test_valid_technical_token_can_read_history_but_never_adjust_or_reconcile(stock_api, api):
    client, database, headers = stock_api
    token = api[3].backoffice_api_token.get_secret_value()
    technical_headers = {"Authorization": "Bearer " + token}
    assert client.get("/backoffice/products/SKU-1/stock-movements", headers=technical_headers).status_code == 200
    body = {"operation_id": str(uuid4()), "delta": 1, "reason": "count"}
    assert client.post(
        "/backoffice/products/SKU-1/stock-adjustments",
        headers={**headers, **technical_headers}, json=body,
    ).status_code == 401
    assert client.get(
        "/backoffice/stock-adjustments/" + body["operation_id"],
        headers=technical_headers,
    ).status_code == 401
    client.cookies.clear()
    assert client.get("/backoffice/products/SKU-1/stock-movements").status_code == 401
    assert database["stock_movements"].documents == []


@pytest.mark.parametrize("mode", ["expired", "revoked"])
def test_expired_or_revoked_cookie_cannot_adjust(stock_api, api, mode):
    client, database, headers = stock_api
    if mode == "expired":
        api[2][0] += api[3].admin_session_ttl_seconds
    else:
        api[1].sessions.clear()
    assert client.post(
        "/backoffice/products/SKU-1/stock-adjustments", headers=headers,
        json={"operation_id": str(uuid4()), "delta": 1, "reason": "count"},
    ).status_code == 401
    assert database["stock_movements"].documents == []
