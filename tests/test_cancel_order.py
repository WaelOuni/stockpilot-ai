import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from pymongo import ReadPreference, ReturnDocument
from pymongo.errors import OperationFailure
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

from stockpilot.application.use_cases.cancel_order import CancelOrderUseCase
from stockpilot.application.use_cases.get_order_status import GetOrderStatusUseCase
from stockpilot.application.use_cases.update_order_status import OrderStatusConflictError
from stockpilot.domain.entities.order import Order
from stockpilot.domain.enums.order_status import OrderStatus
from stockpilot.domain.services.order_lifecycle import (
    InvalidOrderStatusTransitionError,
    validate_order_cancellation,
)
from stockpilot.infrastructure.persistence.mongodb.order_repository import MongoOrderRepository
from stockpilot.infrastructure.persistence.mongodb.product_repository import MongoProductRepository
from stockpilot.infrastructure.config.settings import Settings
from stockpilot.main import app, order_repository


def make_order(status=OrderStatus.CREATED, **changes):
    return replace(Order(
        order_id="order-cancel", operation_id="operation-cancel",
        product_name="Bosch X100", quantity=3, status=status,
        created_at=datetime(2026, 10, 10, tzinfo=timezone.utc),
    ), **changes)


def document(order):
    return {
        "order_id": order.order_id, "operation_id": order.operation_id,
        "product_name": order.product_name, "quantity": order.quantity,
        "status": order.status.value, "created_at": order.created_at.isoformat(),
        **({"customer_id": order.customer_id} if order.customer_id is not None else {}),
    }


def labeled_error(label):
    return OperationFailure("transaction error", details={"errorLabels": [label]})


@pytest.mark.parametrize("status", list(OrderStatus))
def test_cancellation_rules(status):
    if status in (OrderStatus.CREATED, OrderStatus.PROCESSING, OrderStatus.CANCELLED):
        assert validate_order_cancellation(status) is None
    else:
        with pytest.raises(InvalidOrderStatusTransitionError, match=status.value):
            validate_order_cancellation(status)


@pytest.mark.parametrize("status", [OrderStatus.CREATED, OrderStatus.PROCESSING])
def test_use_case_reads_and_passes_original_status_to_repository(status):
    original = make_order(status)
    cancelled = replace(original, status=OrderStatus.CANCELLED)
    calls = []

    async def read(order_id):
        calls.append(("read", order_id))
        return original

    async def cancel(order_id, *, expected_status):
        calls.append(("cancel", order_id, expected_status))
        return cancelled

    repo = SimpleNamespace(find_by_order_id=AsyncMock(side_effect=read),
                           cancel=AsyncMock(side_effect=cancel))
    assert asyncio.run(CancelOrderUseCase(repo).execute(original.order_id)) is cancelled
    assert calls == [("read", original.order_id), ("cancel", original.order_id, status)]
    repo.cancel.assert_awaited_once_with(original.order_id, expected_status=status)


@pytest.mark.parametrize("status", [OrderStatus.SHIPPED, OrderStatus.DELIVERED])
def test_use_case_forbids_late_cancellation_before_repository_write(status):
    repo = SimpleNamespace(find_by_order_id=AsyncMock(return_value=make_order(status)),
                           cancel=AsyncMock())
    with pytest.raises(InvalidOrderStatusTransitionError, match=status.value):
        asyncio.run(CancelOrderUseCase(repo).execute("order-cancel"))
    repo.find_by_order_id.assert_awaited_once_with("order-cancel")
    repo.cancel.assert_not_awaited()


@pytest.mark.parametrize("order", [None, make_order(OrderStatus.CANCELLED)])
def test_use_case_missing_or_cancelled_order_never_calls_cancel(order):
    repo = SimpleNamespace(find_by_order_id=AsyncMock(return_value=order), cancel=AsyncMock())
    assert asyncio.run(CancelOrderUseCase(repo).execute("order-cancel")) is order
    repo.find_by_order_id.assert_awaited_once_with("order-cancel")
    repo.cancel.assert_not_awaited()


@pytest.mark.parametrize("identifier", ["", " ", "\t\n"])
def test_use_case_empty_id_rejected_before_database(identifier):
    repo = SimpleNamespace(find_by_order_id=AsyncMock(), cancel=AsyncMock())
    with pytest.raises(ValueError):
        asyncio.run(CancelOrderUseCase(repo).execute(identifier))
    repo.find_by_order_id.assert_not_awaited()
    repo.cancel.assert_not_awaited()


@pytest.mark.parametrize("phase", ["read", "cancel"])
def test_use_case_database_errors_propagate(phase):
    failure = OperationFailure("database unavailable")
    repo = SimpleNamespace(find_by_order_id=AsyncMock(return_value=make_order()),
                           cancel=AsyncMock())
    getattr(repo, "find_by_order_id" if phase == "read" else "cancel").side_effect = failure
    with pytest.raises(OperationFailure) as caught:
        asyncio.run(CancelOrderUseCase(repo).execute("order-cancel"))
    assert caught.value is failure
    if phase == "read":
        repo.cancel.assert_not_awaited()


def test_use_case_order_disappearing_before_transaction_returns_none():
    repo = SimpleNamespace(find_by_order_id=AsyncMock(return_value=make_order()),
                           cancel=AsyncMock(return_value=None))
    assert asyncio.run(CancelOrderUseCase(repo).execute("order-cancel")) is None
    repo.cancel.assert_awaited_once_with("order-cancel", expected_status=OrderStatus.CREATED)


class FakeCollection:
    def __init__(self, database, name):
        self.database = database
        self.name = name

    def find_one(self, query, **options):
        assert self.name == "orders"
        self.database.reads.append((query, options))
        session = options.get("session")
        state = session.state if session is not None else self.database.state
        order = state["order"]
        if order is None or any(order.get(key) != value for key, value in query.items()):
            return None
        return deepcopy(order)

    def update_one(self, query, update, **options):
        assert self.name == "products"
        session = options["session"]
        self.database.writes.append(("stock", query, update, options))
        if not self.database.product_exists:
            return SimpleNamespace(matched_count=0)
        session.state["stock"] += update["$inc"]["stock"]
        if self.database.stock_error is not None:
            raise self.database.stock_error
        return SimpleNamespace(matched_count=1)

    def find_one_and_update(self, query, update, **options):
        assert self.name == "orders"
        session = options["session"]
        self.database.writes.append(("order", query, update, options))
        order = session.state["order"]
        if self.database.order_no_match or any(order.get(k) != v for k, v in query.items()):
            return None
        order.update(update["$set"])
        if self.database.order_errors:
            raise self.database.order_errors.pop(0)
        return deepcopy(order)


class FakeSession:
    def __init__(self, database):
        self.database = database
        self.attempts = 0
        self.commits = 0
        self.aborts = 0
        self.exited = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.exited = True
        return False

    def with_transaction(self, callback, **options):
        self.options = options
        while True:
            self.attempts += 1
            if self.database.before_attempt is not None:
                self.database.before_attempt(self.database, self.attempts)
            self.state = deepcopy(self.database.state)
            try:
                result = callback(self)
            except Exception as error:
                self.aborts += 1
                if self.database.after_abort is not None:
                    self.database.after_abort(self.database)
                if (
                    isinstance(error, OperationFailure)
                    and error.has_error_label("TransientTransactionError")
                    and self.database.retry_transient
                    and self.attempts == 1
                ):
                    continue
                raise
            if self.database.commit_error is None or self.database.persist_on_commit_error:
                self.database.state = deepcopy(self.state)
                self.commits += 1
            if self.database.commit_error is not None:
                raise self.database.commit_error
            return result


class FakeDatabase:
    def __init__(self, order=None):
        self.state = {"stock": 7, "order": document(order or make_order())}
        self.reads = []
        self.writes = []
        self.product_exists = True
        self.stock_error = None
        self.order_errors = []
        self.order_no_match = False
        self.before_attempt = None
        self.after_abort = None
        self.retry_transient = False
        self.commit_error = None
        self.persist_on_commit_error = False
        self.sessions = []
        self.client = self

    def __getitem__(self, name):
        return FakeCollection(self, name)

    def start_session(self):
        session = FakeSession(self)
        self.sessions.append(session)
        return session


def cancel(database, expected_status=OrderStatus.CREATED):
    return asyncio.run(MongoOrderRepository(database).cancel(
        "order-cancel", expected_status=expected_status,
    ))


@pytest.mark.parametrize("status", [OrderStatus.CREATED, OrderStatus.PROCESSING])
def test_transaction_restores_stock_and_serializes_cancelled_once(status):
    order = make_order(status, customer_id="customer-a")
    database = FakeDatabase(order)
    assert cancel(database, status) == replace(order, status=OrderStatus.CANCELLED)
    assert database.state == {
        "stock": 10, "order": document(make_order(OrderStatus.CANCELLED, customer_id="customer-a")),
    }
    session = database.sessions[0]
    assert session.options == {
        "read_concern": ReadConcern("snapshot"),
        "write_concern": WriteConcern("majority"),
        "read_preference": ReadPreference.PRIMARY,
    }
    assert database.reads == [({"order_id": order.order_id}, {"session": session})]
    assert database.writes == [
        ("stock", MongoProductRepository._name_filter(order.product_name),
         {"$inc": {"stock": 3}}, {"session": session}),
        ("order", {"order_id": order.order_id, "status": status.value},
         {"$set": {"status": "CANCELLED"}},
         {"return_document": ReturnDocument.AFTER, "session": session}),
    ]
    assert session.commits == 1
    assert session.aborts == 0
    assert session.exited
    assert cancel(database, status).status is OrderStatus.CANCELLED
    assert database.state["stock"] == 10
    assert len(database.writes) == 2
    reader = GetOrderStatusUseCase(MongoOrderRepository(database))
    result = asyncio.run(reader.execute(order.order_id, customer_id="customer-a"))
    assert result.status is OrderStatus.CANCELLED
    assert result.result == "FOUND"
    assert result.response == "Commande order-cancel : statut CANCELLED."
    assert database.state["order"]["status"] == "CANCELLED"


def test_use_case_and_repository_replay_does_not_start_second_transaction():
    database = FakeDatabase()
    use_case = CancelOrderUseCase(MongoOrderRepository(database))
    first = asyncio.run(use_case.execute("order-cancel"))
    assert first.status is OrderStatus.CANCELLED
    assert asyncio.run(use_case.execute("order-cancel")) == first
    assert database.state["stock"] == 10
    assert len(database.sessions) == 1
    assert len(database.writes) == 2


@pytest.mark.parametrize("expected_status", list(OrderStatus))
def test_already_cancelled_transaction_ignores_stale_expected_status_and_missing_product(expected_status):
    database = FakeDatabase(make_order(OrderStatus.CANCELLED))
    database.product_exists = False
    before = deepcopy(database.state)
    assert cancel(database, expected_status) == make_order(OrderStatus.CANCELLED)
    assert database.state == before
    assert database.writes == []


def test_missing_order_inside_transaction_returns_none_without_stock_write():
    database = FakeDatabase()
    database.state["order"] = None
    assert cancel(database) is None
    assert database.state == {"stock": 7, "order": None}
    assert database.writes == []


@pytest.mark.parametrize("status", [OrderStatus.SHIPPED, OrderStatus.DELIVERED])
def test_repository_validates_cancellation_even_with_matching_expected_status(status):
    database = FakeDatabase(make_order(status))
    before = deepcopy(database.state)
    with pytest.raises(InvalidOrderStatusTransitionError, match=status.value):
        cancel(database, status)
    assert database.state == before
    assert database.writes == []
    assert database.sessions[0].aborts == 1


@pytest.mark.parametrize("status", [
    OrderStatus.PROCESSING, OrderStatus.SHIPPED, OrderStatus.DELIVERED,
])
def test_mismatched_expected_status_is_conflict_before_stock(status):
    database = FakeDatabase(make_order(status))
    before = deepcopy(database.state)
    with pytest.raises(OrderStatusConflictError):
        cancel(database)
    assert database.state == before
    assert database.writes == []


@pytest.mark.parametrize("quantity", [0, -1, True, "3", 1.5, None])
def test_invalid_persisted_quantity_never_restores_stock(quantity):
    database = FakeDatabase(make_order(quantity=quantity))
    before = deepcopy(database.state)
    with pytest.raises(ValueError, match="quantité"):
        cancel(database)
    assert database.state == before
    assert database.writes == []


@pytest.mark.parametrize("fault", ["missing-product", "stock-error", "order-error", "no-match"])
def test_transaction_fault_rolls_back_both_writes(fault):
    database = FakeDatabase()
    before = deepcopy(database.state)
    failure = OperationFailure("forced database failure")
    expected_error = OperationFailure
    if fault == "missing-product":
        database.product_exists = False
        expected_error = RuntimeError
    elif fault == "stock-error":
        database.stock_error = failure
    elif fault == "order-error":
        database.order_errors = [failure]
    else:
        database.order_no_match = True
        expected_error = OrderStatusConflictError
    with pytest.raises(expected_error) as caught:
        cancel(database)
    if expected_error is OperationFailure:
        assert caught.value is failure
    assert database.state == before
    session = database.sessions[0]
    assert session.aborts == 1
    assert session.commits == 0
    assert session.exited
    assert [write[0] for write in database.writes] == (
        ["stock"] if fault in ("missing-product", "stock-error") else ["stock", "order"]
    )


@pytest.mark.parametrize("status", [OrderStatus.PROCESSING, OrderStatus.SHIPPED])
def test_concurrent_status_change_after_use_case_read_is_conflict(status):
    database = FakeDatabase()

    def concurrent_update(db, attempt):
        db.state["order"]["status"] = status.value

    database.before_attempt = concurrent_update
    with pytest.raises(OrderStatusConflictError):
        asyncio.run(CancelOrderUseCase(MongoOrderRepository(database)).execute("order-cancel"))
    assert database.reads[0][1] == {}
    assert database.reads[1][1] == {"session": database.sessions[0]}
    assert database.state["order"]["status"] == status.value
    assert database.state["stock"] == 7
    assert database.writes == []


def concurrent_cancellation(database):
    database.state["order"]["status"] = "CANCELLED"
    database.state["stock"] += 3


def test_concurrent_cancellation_after_precheck_is_success_without_second_restoration():
    database = FakeDatabase()
    database.before_attempt = lambda db, attempt: concurrent_cancellation(db)
    result = asyncio.run(CancelOrderUseCase(MongoOrderRepository(database)).execute("order-cancel"))
    assert result.status is OrderStatus.CANCELLED
    assert database.state["stock"] == 10
    assert database.writes == []


def test_transient_callback_retry_rereads_and_restores_stock_only_once_effectively():
    database = FakeDatabase()
    database.retry_transient = True
    database.order_errors = [labeled_error("TransientTransactionError")]
    assert cancel(database).status is OrderStatus.CANCELLED
    assert database.state["stock"] == 10
    session = database.sessions[0]
    assert (session.attempts, session.aborts, session.commits) == (2, 1, 1)
    assert database.reads == [({"order_id": "order-cancel"}, {"session": session})] * 2
    assert [write[0] for write in database.writes] == ["stock", "order", "stock", "order"]


@pytest.mark.parametrize("winner", [OrderStatus.CANCELLED, OrderStatus.SHIPPED])
def test_transient_retry_observes_concurrent_winner_before_any_second_stock_write(winner):
    database = FakeDatabase()
    database.retry_transient = True
    database.order_errors = [labeled_error("TransientTransactionError")]

    def change_after_abort(db):
        if winner is OrderStatus.CANCELLED:
            concurrent_cancellation(db)
        else:
            db.state["order"]["status"] = winner.value

    database.after_abort = change_after_abort
    if winner is OrderStatus.CANCELLED:
        assert cancel(database).status is OrderStatus.CANCELLED
    else:
        with pytest.raises(OrderStatusConflictError):
            cancel(database)
    assert database.sessions[0].attempts == 2
    assert len(database.reads) == 2
    assert [write[0] for write in database.writes] == ["stock", "order"]
    assert database.state["order"]["status"] == winner.value
    assert database.state["stock"] == (10 if winner is OrderStatus.CANCELLED else 7)


@pytest.mark.parametrize("persisted", [False, True])
def test_unresolved_unknown_commit_propagates_without_assuming_rollback(persisted):
    database = FakeDatabase()
    database.commit_error = labeled_error("UnknownTransactionCommitResult")
    database.persist_on_commit_error = persisted
    with pytest.raises(OperationFailure) as caught:
        cancel(database)
    assert caught.value is database.commit_error
    assert database.sessions[0].aborts == 0
    assert database.state["stock"] == (10 if persisted else 7)
    assert database.state["order"]["status"] == ("CANCELLED" if persisted else "CREATED")
    if persisted:
        database.commit_error = None
        assert cancel(database).status is OrderStatus.CANCELLED
        assert database.state["stock"] == 10
        assert len(database.writes) == 2


def install_cancel_use_case(monkeypatch, use_case):
    monkeypatch.setattr(app.state, "settings", Settings(backoffice_api_token="test-only-token"))
    monkeypatch.setattr(app.state, "cancel_order_use_case", use_case)
    other = SimpleNamespace(execute=AsyncMock(side_effect=AssertionError("unrelated use case")))
    monkeypatch.setattr(app.state, "update_order_status_use_case", other)
    monkeypatch.setattr(app.state, "classify_intent_use_case", other)
    monkeypatch.setattr(app.state, "generate_response_use_case", other)
    monkeypatch.setattr(app.state, "get_order_status_use_case", other)
    return other


def authorized_client():
    return TestClient(app, headers={"Authorization": "Bearer test-only-token"})


@pytest.mark.parametrize("status", [
    OrderStatus.CREATED, OrderStatus.PROCESSING, OrderStatus.CANCELLED,
])
def test_cancel_endpoint_success_new_and_already_cancelled(monkeypatch, status):
    repo = SimpleNamespace(
        find_by_order_id=AsyncMock(return_value=make_order(status)),
        cancel=AsyncMock(return_value=make_order(OrderStatus.CANCELLED)),
    )
    other = install_cancel_use_case(monkeypatch, CancelOrderUseCase(repo))
    response = authorized_client().post("/orders/order-cancel/cancel")
    assert response.status_code == 200
    assert response.json() == {
        "order_id": "order-cancel", "product_name": "Bosch X100",
        "quantity": 3, "status": "CANCELLED", "created_at": "2026-10-10T00:00:00Z",
    }
    repo.find_by_order_id.assert_awaited_once_with("order-cancel")
    if status is OrderStatus.CANCELLED:
        repo.cancel.assert_not_awaited()
    else:
        repo.cancel.assert_awaited_once_with("order-cancel", expected_status=status)
    other.execute.assert_not_awaited()


def test_cancel_endpoint_missing_order(monkeypatch):
    use_case = SimpleNamespace(execute=AsyncMock(return_value=None))
    install_cancel_use_case(monkeypatch, use_case)
    response = authorized_client().post("/orders/missing/cancel")
    assert response.status_code == 404
    assert response.json() == {"detail": "Commande introuvable (NOT_FOUND)."}
    use_case.execute.assert_awaited_once_with("missing")


@pytest.mark.parametrize("status", [OrderStatus.SHIPPED, OrderStatus.DELIVERED])
def test_cancel_endpoint_business_conflict_never_writes(monkeypatch, status):
    repo = SimpleNamespace(find_by_order_id=AsyncMock(return_value=make_order(status)),
                           cancel=AsyncMock())
    install_cancel_use_case(monkeypatch, CancelOrderUseCase(repo))
    response = authorized_client().post("/orders/order-cancel/cancel")
    assert response.status_code == 409
    assert status.value in response.json()["detail"]
    repo.cancel.assert_not_awaited()


def test_cancel_endpoint_maps_concurrent_database_change_to_conflict(monkeypatch):
    database = FakeDatabase()
    database.before_attempt = lambda db, attempt: db.state["order"].update(status="SHIPPED")
    install_cancel_use_case(monkeypatch, CancelOrderUseCase(MongoOrderRepository(database)))
    response = authorized_client().post("/orders/order-cancel/cancel")
    assert response.status_code == 409
    assert "changé" in response.json()["detail"]
    assert database.state["stock"] == 7
    assert database.writes == []


@pytest.mark.parametrize("error", [
    InvalidOrderStatusTransitionError("business conflict"),
    OrderStatusConflictError("concurrent conflict"),
])
def test_cancel_endpoint_maps_injected_conflicts(monkeypatch, error):
    use_case = SimpleNamespace(execute=AsyncMock(side_effect=error))
    other = install_cancel_use_case(monkeypatch, use_case)
    response = authorized_client().post("/orders/order-cancel/cancel")
    assert response.status_code == 409
    assert response.json() == {"detail": str(error)}
    use_case.execute.assert_awaited_once_with("order-cancel")
    other.execute.assert_not_awaited()


@pytest.mark.parametrize("failure", ["unexpected", "unknown-commit"])
def test_cancel_endpoint_unexpected_errors_are_sanitized_logged_500(monkeypatch, caplog, failure):
    database = None
    if failure == "unexpected":
        use_case = SimpleNamespace(execute=AsyncMock(
            side_effect=RuntimeError("private database detail")))
    else:
        database = FakeDatabase()
        database.commit_error = labeled_error("UnknownTransactionCommitResult")
        database.persist_on_commit_error = True
        use_case = CancelOrderUseCase(MongoOrderRepository(database))
    other = install_cancel_use_case(monkeypatch, use_case)
    response = authorized_client().post("/orders/order-cancel/cancel")
    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error"}
    assert response.headers["X-Request-ID"]
    assert any(record.getMessage() == "http_request_failed" for record in caplog.records)
    assert "private database detail" not in response.text
    assert "transaction error" not in response.text
    other.execute.assert_not_awaited()
    if database is not None:
        assert database.state["stock"] == 10
        assert database.state["order"]["status"] == "CANCELLED"
        assert database.sessions[0].aborts == 0


def test_composition_root_wires_cancel_use_case_to_existing_repository():
    assert isinstance(app.state.cancel_order_use_case, CancelOrderUseCase)
    assert app.state.cancel_order_use_case.order_repository is order_repository
