import asyncio
from dataclasses import replace
from datetime import datetime, timezone

import pytest
from pymongo import ReadPreference, ReturnDocument
from pymongo.errors import ConnectionFailure, DuplicateKeyError, OperationFailure
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

from stockpilot.application.order_idempotency import IdempotencyConflictError
from stockpilot.application.order_transaction_errors import (
    OrderOutcomeUnknownError,
    OrderTransactionFailedError,
)
from stockpilot.domain.entities.order import Order
from stockpilot.domain.enums.order_status import OrderStatus
from stockpilot.infrastructure.persistence.mongodb.order_transaction import (
    MongoOrderTransaction,
)


def make_order() -> Order:
    return Order(
        order_id="order-123",
        operation_id="operation-123",
        product_name="Bosch X100",
        quantity=3,
        status=OrderStatus.CREATED,
        created_at=datetime(2026, 10, 9, 20, 0, tzinfo=timezone.utc),
    )


def document(order):
    return {
        "order_id": order.order_id,
        "operation_id": order.operation_id,
        "product_name": order.product_name,
        "quantity": order.quantity,
        "status": order.status.value,
        "created_at": order.created_at.isoformat(),
    }


def labeled_error(label):
    return OperationFailure("transaction error", details={"errorLabels": [label]})


class FakeCollection:
    def __init__(self, database, name):
        self.database = database
        self.name = name

    def find_one(self, query, **options):
        session = options.get("session")
        self.database.lookups.append((query, options))
        if session is None and self.database.lookup_error is not None:
            raise self.database.lookup_error
        documents = session.documents if session else self.database.documents
        return documents.get(query["operation_id"])

    def find_one_and_update(self, query, update, **options):
        assert self.name == "products"
        assert query["name"] == {"$regex": "Bosch X100", "$options": "i"}
        assert options["projection"] == {"stock": 1}
        assert options["return_document"] == ReturnDocument.AFTER
        session = options["session"]
        self.database.writes.append(("reserve", session, query, update))
        if session.stock < query["stock"]["$gte"]:
            return None
        session.stock += update["$inc"]["stock"]
        return {"stock": session.stock}

    def insert_one(self, order, **options):
        session = options["session"]
        self.database.writes.append(("insert", session))
        if self.database.insert_errors:
            raise self.database.insert_errors.pop(0)
        session.documents[order["operation_id"]] = order


class FakeSession:
    def __init__(self, database):
        self.database = database
        self.commits = 0
        self.aborts = 0
        self.attempts = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def with_transaction(self, callback, **options):
        self.options = options
        while True:
            self.attempts += 1
            self.stock = self.database.stock
            self.documents = dict(self.database.documents)
            if self.database.winner_on_attempt is not None:
                winner = self.database.winner_on_attempt
                self.documents[winner.operation_id] = document(winner)
                self.database.documents[winner.operation_id] = document(winner)
            try:
                result = callback(self)
            except Exception as error:
                self.aborts += 1
                if self.database.winner_after_abort is not None:
                    winner = self.database.winner_after_abort
                    self.database.documents[winner.operation_id] = document(winner)
                self.database.lookup_error = self.database.lookup_error_after_abort
                if (
                    isinstance(error, OperationFailure)
                    and error.has_error_label("TransientTransactionError")
                    and self.database.retry_transient
                    and self.attempts == 1
                ):
                    continue
                raise
            if self.database.commit_error is None or self.database.persist_on_error:
                self.database.stock = self.stock
                self.database.documents = self.documents
                self.commits += 1
            if self.database.commit_error is not None:
                self.database.lookup_error = self.database.lookup_error_after_abort
                raise self.database.commit_error
            return result


class FakeDatabase:
    def __init__(self, stock=10):
        self.stock = stock
        self.documents = {}
        self.writes = []
        self.lookups = []
        self.insert_errors = []
        self.lookup_error = None
        self.lookup_error_after_abort = None
        self.winner_after_abort = None
        self.winner_on_attempt = None
        self.commit_error = None
        self.persist_on_error = False
        self.retry_transient = False
        self.session = FakeSession(self)
        self.sessions_started = 0
        self.client = self

    def __getitem__(self, name):
        return FakeCollection(self, name)

    def start_session(self):
        self.sessions_started += 1
        return self.session


def execute(database, order=None):
    return asyncio.run(MongoOrderTransaction(database).execute(order or make_order()))


def test_commits_quantity_three_and_order_with_the_same_session():
    database = FakeDatabase()
    order = make_order()

    assert execute(database, order) == order
    assert database.stock == 7
    assert database.documents == {order.operation_id: document(order)}
    assert database.session.commits == 1
    assert database.session.aborts == 0
    assert [write[:2] for write in database.writes] == [
        ("reserve", database.session), ("insert", database.session)
    ]
    assert database.writes[0][2]["stock"] == {"$gte": 3}
    assert database.writes[0][3] == {"$inc": {"stock": -3}}
    assert database.lookups == [
        ({"operation_id": order.operation_id}, {}),
        ({"operation_id": order.operation_id}, {"session": database.session}),
    ]
    assert database.session.options == {
        "read_concern": ReadConcern("snapshot"),
        "write_concern": WriteConcern("majority"),
        "read_preference": ReadPreference.PRIMARY,
    }


def test_existing_order_returns_original_without_starting_a_session(caplog):
    database = FakeDatabase()
    original = make_order()
    database.documents[original.operation_id] = document(original)
    with caplog.at_level("INFO"):
        assert execute(database, replace(original, order_id="retry-id")) == original
    assert database.sessions_started == 0
    assert database.writes == []
    assert database.stock == 10
    assert "Order replay recovered" in caplog.text


@pytest.mark.parametrize("inside", [False, True])
@pytest.mark.parametrize("changes", [{"quantity": 4}, {"product_name": "Makita"}])
def test_conflict_never_changes_stock_or_inserts(inside, changes, caplog):
    database = FakeDatabase()
    original = make_order()
    if inside:
        database.winner_on_attempt = original
    else:
        database.documents[original.operation_id] = document(original)
    with pytest.raises(IdempotencyConflictError):
        execute(database, replace(original, **changes))
    assert database.writes == []
    assert database.stock == 10
    assert database.session.aborts == int(inside)
    assert "Idempotency conflict" in caplog.text


def test_transaction_rechecks_for_a_concurrent_existing_order_before_reserving():
    database = FakeDatabase()
    database.winner_on_attempt = make_order()
    assert execute(database, replace(make_order(), order_id="retry-id")) == make_order()
    assert database.writes == []
    assert database.stock == 10


def test_insufficient_stock_aborts_instead_of_committing_and_returns_none():
    database = FakeDatabase(stock=2)
    assert execute(database) is None
    assert database.stock == 2
    assert database.documents == {}
    assert database.session.aborts == 1
    assert database.session.commits == 0
    assert len(database.lookups) == 3
    assert len(database.writes) == 1


def test_insufficient_stock_race_recovers_the_winner_after_abort():
    database = FakeDatabase(stock=0)
    database.winner_after_abort = make_order()
    assert execute(database, replace(make_order(), order_id="loser-id")) == make_order()
    assert database.stock == 0
    assert database.session.aborts == 1


def test_duplicate_key_is_not_swallowed_inside_transaction_and_recovers_after_abort():
    database = FakeDatabase()
    database.insert_errors = [DuplicateKeyError("duplicate operation")]
    database.winner_after_abort = make_order()
    assert execute(database, replace(make_order(), order_id="loser-id")) == make_order()
    assert database.session.aborts == 1
    assert database.session.commits == 0
    assert database.stock == 10
    assert len(database.documents) == 1


def test_duplicate_key_without_a_matching_order_is_an_explicit_failure():
    database = FakeDatabase()
    database.insert_errors = [DuplicateKeyError("another unique index")]
    with pytest.raises(OrderTransactionFailedError):
        execute(database)
    assert database.stock == 10
    assert database.documents == {}
    assert database.session.aborts == 1


def test_duplicate_recovery_validates_payload_after_abort():
    database = FakeDatabase()
    database.insert_errors = [DuplicateKeyError("duplicate operation")]
    database.winner_after_abort = replace(make_order(), quantity=4)
    with pytest.raises(IdempotencyConflictError):
        execute(database)
    assert database.stock == 10
    assert database.session.aborts == 1


def test_insert_failure_rolls_back_stock_without_manual_compensation():
    database = FakeDatabase()
    failure = OperationFailure("insert rejected")
    database.insert_errors = [failure]
    with pytest.raises(OrderTransactionFailedError) as error:
        execute(database)
    assert error.value.__cause__ is failure
    assert database.stock == 10
    assert database.documents == {}
    assert database.session.aborts == 1
    assert [write[0] for write in database.writes] == ["reserve", "insert"]


def test_driver_callback_retry_rechecks_idempotency_and_reserves_only_once_effectively():
    database = FakeDatabase()
    database.insert_errors = [labeled_error("TransientTransactionError")]
    database.retry_transient = True
    assert execute(database) == make_order()
    assert database.session.attempts == 2
    assert database.session.aborts == 1
    assert database.session.commits == 1
    assert database.stock == 7
    assert len(database.documents) == 1
    assert len([lookup for lookup in database.lookups if lookup[1]]) == 2


def test_driver_retry_finds_concurrent_winner_before_a_second_reservation():
    database = FakeDatabase()
    database.insert_errors = [labeled_error("TransientTransactionError")]
    database.retry_transient = True
    database.winner_after_abort = make_order()
    assert execute(database, replace(make_order(), order_id="loser-id")) == make_order()
    assert database.session.attempts == 2
    assert [write[0] for write in database.writes] == ["reserve", "insert"]
    assert database.stock == 10


def test_exhausted_transient_callback_error_is_conservatively_unknown():
    database = FakeDatabase()
    database.insert_errors = [labeled_error("TransientTransactionError")]
    with pytest.raises(OrderOutcomeUnknownError):
        execute(database)
    assert database.session.aborts == 1
    assert database.stock == 10
    assert database.documents == {}


@pytest.mark.parametrize(
    "failure",
    [
        labeled_error("UnknownTransactionCommitResult"),
        labeled_error("TransientTransactionError"),
        ConnectionFailure("connection lost"),
    ],
)
def test_unreconciled_ambiguous_or_exhausted_driver_error_is_unknown(failure):
    database = FakeDatabase()
    database.commit_error = failure
    with pytest.raises(OrderOutcomeUnknownError) as error:
        execute(database)
    assert error.value.__cause__ is failure
    assert database.documents == {}


def test_ambiguous_commit_recovers_committed_order_and_logs_recovery(caplog):
    database = FakeDatabase()
    database.commit_error = labeled_error("UnknownTransactionCommitResult")
    database.persist_on_error = True
    with caplog.at_level("INFO"):
        assert execute(database) == make_order()
    assert database.stock == 7
    assert len(database.documents) == 1
    assert "Ambiguous order outcome recovered" in caplog.text
    # A caller retry after an ambiguous successful commit never decrements again.
    assert execute(database) == make_order()
    assert database.sessions_started == 1
    assert database.stock == 7


@pytest.mark.parametrize("failure_kind", ["insert", "duplicate", "commit", "stock"])
def test_failed_reconciliation_is_unknown_even_when_original_error_was_aborted(failure_kind):
    database = FakeDatabase(stock=0 if failure_kind == "stock" else 10)
    database.lookup_error_after_abort = ConnectionFailure("lookup unavailable")
    if failure_kind == "insert":
        database.insert_errors = [OperationFailure("insert rejected")]
    elif failure_kind == "duplicate":
        database.insert_errors = [DuplicateKeyError("duplicate")]
    elif failure_kind == "commit":
        database.commit_error = labeled_error("UnknownTransactionCommitResult")
    with pytest.raises(OrderOutcomeUnknownError) as error:
        execute(database)
    assert error.value.__cause__ is database.lookup_error_after_abort


def test_precheck_lookup_failure_is_unknown_without_starting_a_session():
    database = FakeDatabase()
    database.lookup_error = ConnectionFailure("lookup unavailable")
    with pytest.raises(OrderOutcomeUnknownError):
        execute(database)
    assert database.sessions_started == 0
    assert database.writes == []


def test_failure_starting_a_session_is_reported_without_writes():
    database = FakeDatabase()

    def start_session():
        raise OperationFailure("transactions unavailable")

    database.start_session = start_session
    with pytest.raises(OrderTransactionFailedError):
        execute(database)
    assert database.writes == []
    assert database.stock == 10


def test_unexpected_callback_error_propagates_after_abort():
    database = FakeDatabase()
    database.insert_errors = [ValueError("unexpected serialization failure")]
    with pytest.raises(ValueError, match="unexpected"):
        execute(database)
    assert database.session.aborts == 1
    assert database.stock == 10
    assert database.documents == {}
