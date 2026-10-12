import asyncio
import re
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from bson import ObjectId
from pymongo.errors import OperationFailure

from stockpilot.application.use_cases.cancel_order import CancelOrderUseCase
from stockpilot.domain.entities.order import Order
from stockpilot.domain.enums.order_status import OrderStatus
from stockpilot.infrastructure.persistence.mongodb.order_repository import MongoOrderRepository
from stockpilot.infrastructure.persistence.mongodb.order_transaction import MongoOrderTransaction
from stockpilot.presentation.api.schemas.order import OrderResponse


def matches(document, query):
    for key, condition in query.items():
        value = document.get(key)
        if not isinstance(condition, dict):
            if value != condition:
                return False
        elif "$regex" in condition:
            # Mongo PCRE's absolute-end anchor is \z; Python 3.12 uses \Z.
            pattern = condition["$regex"].replace(r"\z", r"\Z")
            if re.search(pattern, value or "", re.I) is None:
                return False
        elif "$gte" in condition:
            if value < condition["$gte"]:
                return False
        elif "$eq" in condition:
            if value != condition["$eq"]:
                return False
        else:
            raise AssertionError(f"Unsupported test query: {condition}")
    return True


class Cursor:
    def __init__(self, documents):
        self.documents = documents

    def limit(self, count):
        self.documents = self.documents[:count]
        return self

    def __iter__(self):
        return iter(deepcopy(self.documents))


class Collection:
    def __init__(self, database, name):
        self.database = database
        self.name = name

    def documents(self, session=None):
        return (session.state if session else self.database.state)[self.name]

    def find(self, query, projection=None, *, session=None):
        self.database.reads.append((self.name, query, session))
        return Cursor([doc for doc in self.documents(session) if matches(doc, query)])

    def find_one(self, query, *, session=None):
        return next(iter(self.find(query, session=session)), None)

    def insert_one(self, document, *, session=None):
        self.documents(session).append(deepcopy(document))

    def update_one(self, query, update, *, session=None):
        self.database.writes.append((self.name, deepcopy(query)))
        for doc in self.documents(session):
            if matches(doc, query):
                doc["stock"] += update["$inc"]["stock"]
                if self.database.fail_stock:
                    raise OperationFailure("restock failed")
                return SimpleNamespace(matched_count=1)
        return SimpleNamespace(matched_count=0)

    def find_one_and_update(self, query, update, *, session=None, **options):
        self.database.writes.append((self.name, deepcopy(query)))
        if self.name == "orders" and self.database.fail_status:
            raise OperationFailure("status write failed")
        for doc in self.documents(session):
            if matches(doc, query):
                for key, amount in update.get("$inc", {}).items():
                    doc[key] += amount
                doc.update(update.get("$set", {}))
                for key, item in update.get("$push", {}).items():
                    doc.setdefault(key, []).append(deepcopy(item))
                return deepcopy(doc)
        return None


class Session:
    def __init__(self, database):
        self.database = database

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def with_transaction(self, callback, **options):
        self.state = deepcopy(self.database.state)
        result = callback(self)
        self.database.state = self.state
        return result


class Database:
    def __init__(self, products):
        self.state = {"products": deepcopy(products), "orders": []}
        self.client = self
        self.writes = []
        self.reads = []
        self.fail_stock = False
        self.fail_status = False

    def __getitem__(self, name):
        return Collection(self, name)

    def start_session(self):
        return Session(self)

    def stocks(self):
        return {doc["_id"]: doc["stock"] for doc in self.state["products"]}


def product(name, stock, *, sku="duplicate-sku"):
    return {"_id": ObjectId(), "sku": sku, "name": name, "stock": stock}


def order(name="Bosch X100"):
    return Order(
        order_id="identity-order", operation_id="identity-operation",
        product_name=name, quantity=2, status=OrderStatus.CREATED,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc), customer_id="owner",
    )


def confirm(database, name="Bosch X100"):
    return asyncio.run(MongoOrderTransaction(database).execute(order(name)))


def cancel(database, customer_id="owner"):
    return asyncio.run(CancelOrderUseCase(MongoOrderRepository(database)).execute(
        "identity-order", customer_id=customer_id,
    ))


@pytest.mark.parametrize("similar_stock", [0, 8])
def test_original_wrong_document_restock_regression(similar_stock):
    similar = product("Bosch X100 Pro", similar_stock)
    target = product("Bosch X100", 10)
    db = Database([similar, target])
    created = confirm(db)
    assert created.status == OrderStatus.CREATED
    assert db.stocks() == {similar["_id"]: similar_stock, target["_id"]: 8}
    assert db.state["orders"][0]["reserved_product_id"] == target["_id"]
    assert cancel(db).status == OrderStatus.CANCELLED
    assert db.stocks() == {similar["_id"]: similar_stock, target["_id"]: 10}
    stock_queries = [query for collection, query in db.writes if collection == "products"]
    assert stock_queries == [
        {"_id": target["_id"], "stock": {"$gte": 2}}, {"_id": target["_id"]},
    ]


def test_unique_product_replay_and_repeated_cancellation():
    target = product("Bosch X100", 10)
    db = Database([target])
    first = confirm(db)
    writes = deepcopy(db.writes)
    assert confirm(db) == first
    assert db.writes == writes
    assert db.stocks() == {target["_id"]: 8}
    cancelled = cancel(db)
    writes = deepcopy(db.writes)
    assert cancel(db) == cancelled
    assert db.writes == writes
    assert db.stocks() == {target["_id"]: 10}
    assert [event["status"] for event in db.state["orders"][0]["events"]] == [
        "CREATED", "CANCELLED",
    ]


def test_saved_reference_survives_rename_and_duplicate_name_and_sku():
    target = product("Bosch X100", 10)
    db = Database([target])
    confirm(db)
    db.state["products"][0]["name"] = "Renamed product"
    replacement = product("Bosch X100", 4)
    db.state["products"].append(replacement)
    cancel(db)
    assert db.stocks() == {target["_id"]: 10, replacement["_id"]: 4}


def test_processing_transition_preserves_reference_and_atomic_event_history():
    target = product("Bosch X100", 10)
    similar = product("Bosch X100 Pro", 0)
    db = Database([similar, target])
    confirm(db)
    repository = MongoOrderRepository(db)
    updated = asyncio.run(repository.update_status(
        "identity-order", expected_status=OrderStatus.CREATED,
        status=OrderStatus.PROCESSING,
    ))
    assert updated.status == OrderStatus.PROCESSING
    assert db.state["orders"][0]["reserved_product_id"] == target["_id"]
    cancel(db)
    assert db.stocks() == {similar["_id"]: 0, target["_id"]: 10}
    assert [event["status"] for event in db.state["orders"][0]["events"]] == [
        "CREATED", "PROCESSING", "CANCELLED",
    ]


def test_deleted_reserved_document_never_falls_back_to_replacement():
    db = Database([product("Bosch X100", 10)])
    confirm(db)
    replacement = product("Bosch X100", 4)
    db.state["products"] = [replacement]
    before = deepcopy(db.state)
    with pytest.raises(RuntimeError, match="intervention manuelle"):
        cancel(db)
    assert db.state == before


@pytest.mark.parametrize("failure", ["fail_stock", "fail_status"])
def test_restock_or_status_failure_rolls_back_each_product(failure):
    db = Database([product("Bosch X100 Pro", 0), product("Bosch X100", 10)])
    confirm(db)
    before = deepcopy(db.state)
    setattr(db, failure, True)
    with pytest.raises(OperationFailure):
        cancel(db)
    assert db.state == before


def test_legacy_unique_name_without_document_identity_requires_manual_intervention():
    target = product("Bosch X100", 10)
    db = Database([target])
    confirm(db)
    del db.state["orders"][0]["reserved_product_id"]
    before = deepcopy(db.state)
    db.writes.clear()
    with pytest.raises(RuntimeError, match="intervention manuelle"):
        cancel(db)
    assert db.state == before
    assert db.writes == []


@pytest.mark.parametrize("extra", ["Bosch X100", "bosch x100", "Bosch X100 Pro"])
def test_legacy_ambiguous_name_is_rejected_without_writes(extra):
    db = Database([product("Bosch X100", 10)])
    confirm(db)
    del db.state["orders"][0]["reserved_product_id"]
    db.state["products"].append(product(extra, 0))
    before = deepcopy(db.state)
    db.writes.clear()
    with pytest.raises(RuntimeError, match="intervention manuelle"):
        cancel(db)
    assert db.state == before
    assert db.writes == []
    assert asyncio.run(MongoOrderRepository(db).find_by_order_id("identity-order")) is not None


def test_legacy_without_exact_match_does_not_choose_similar_name():
    db = Database([product("Bosch X100", 10)])
    confirm(db)
    del db.state["orders"][0]["reserved_product_id"]
    db.state["products"][0]["name"] = "Bosch X100 Pro"
    before = deepcopy(db.state)
    db.writes.clear()
    with pytest.raises(RuntimeError, match="intervention manuelle"):
        cancel(db)
    assert db.state == before
    assert db.writes == []


@pytest.mark.parametrize("bad_id", [None, {}, []])
def test_invalid_saved_reference_never_uses_legacy_fallback(bad_id):
    db = Database([product("Bosch X100", 10)])
    confirm(db)
    db.state["orders"][0]["reserved_product_id"] = bad_id
    before = deepcopy(db.state)
    db.writes.clear()
    with pytest.raises(RuntimeError, match="intervention manuelle"):
        cancel(db)
    assert db.state == before
    assert db.writes == []


def test_ambiguous_reservation_rejected_even_if_only_one_has_stock():
    db = Database([product("Bosch X100", 0), product("Bosch X100", 10)])
    before = deepcopy(db.state)
    with pytest.raises(RuntimeError, match="ambigu"):
        confirm(db)
    assert db.state == before
    assert db.writes == []


@pytest.mark.parametrize("name", ["Bosch X[100].*", "bosch x100"])
def test_reservation_treats_name_as_literal_case_insensitive_full_match(name):
    target = product(name, 10)
    similar = product(name + " Pro", 0)
    db = Database([similar, target])
    confirm(db, name)
    cancel(db)
    assert db.stocks() == {similar["_id"]: 0, target["_id"]: 10}


def test_absolute_end_anchor_does_not_accept_trailing_newline():
    db = Database([product("Bosch X100\n", 10)])
    assert confirm(db) is None
    assert db.state["products"][0]["stock"] == 10
    assert db.state["orders"] == []


def test_out_of_stock_exact_product_does_not_reserve_available_similar_product():
    similar = product("Bosch X100 Pro", 10)
    target = product("Bosch X100", 0)
    db = Database([similar, target])
    assert confirm(db) is None
    assert db.stocks() == {similar["_id"]: 10, target["_id"]: 0}
    assert db.state["orders"] == []


def test_legacy_missing_product_fails_before_any_write():
    db = Database([product("Bosch X100", 10)])
    confirm(db)
    del db.state["orders"][0]["reserved_product_id"]
    db.state["products"] = []
    before = deepcopy(db.state)
    db.writes.clear()
    with pytest.raises(RuntimeError, match="intervention manuelle"):
        cancel(db)
    assert db.state == before
    assert db.writes == []


def test_legacy_regex_metacharacters_cannot_hide_historical_ambiguity():
    target = product("Bosch X.100", 10)
    db = Database([target])
    confirm(db, "Bosch X.100")
    del db.state["orders"][0]["reserved_product_id"]
    db.state["products"].append(product("Bosch XZ100", 4))
    before = deepcopy(db.state)
    db.writes.clear()
    with pytest.raises(RuntimeError, match="intervention manuelle"):
        cancel(db)
    assert db.state == before
    assert db.writes == []


def test_foreign_customer_cannot_cancel_and_internal_identity_is_not_public():
    target = product("Bosch X100", 10)
    db = Database([target])
    created = confirm(db)
    before = deepcopy(db.state)
    db.writes.clear()
    assert cancel(db, "foreign") is None
    assert db.state == before
    assert db.writes == []
    assert "reserved_product_id" not in OrderResponse.model_validate(created).model_dump()
    assert not hasattr(created, "reserved_product_id")
