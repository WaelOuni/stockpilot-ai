import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from pymongo.errors import DuplicateKeyError

from stockpilot.application.order_idempotency import IdempotencyConflictError
from stockpilot.application.use_cases.create_order import CreateOrderUseCase
from stockpilot.application.use_cases.finalize_order import FinalizeOrderUseCase
from stockpilot.application.use_cases.get_order_status import GetOrderStatusUseCase
from stockpilot.application.workflows.nodes import create_order_confirmation_node, create_order_status_node
from stockpilot.infrastructure.persistence.mongodb.order_transaction import MongoOrderTransaction
from stockpilot.main import app
from stockpilot.presentation.api.routes.assistant import customer_thread_key
from customer_auth import configure_customer_auth, customer_headers
from test_create_order import make_order, make_repository, order_document
from test_order_conversation import make_workflow
from test_order_transaction import FakeDatabase, document, execute, labeled_error


@pytest.fixture(autouse=True)
def customer_auth(monkeypatch):
    configure_customer_auth(monkeypatch, app)


@pytest.mark.parametrize("headers", [{}, {"X-Customer-Id": "customer-a"}])
def test_assistant_rejects_missing_customer_token(monkeypatch, headers):
    workflow = SimpleNamespace(ainvoke=AsyncMock())
    monkeypatch.setattr(app.state, "stockpilot_workflow", workflow)
    response = TestClient(app).post(
        "/assistant", headers=headers, json={"message": "hello", "thread_id": "same"},
    )
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid customer credentials."}
    workflow.ainvoke.assert_not_awaited()


def test_customer_header_schema_and_body_cannot_override_identity(monkeypatch):
    workflow = SimpleNamespace(ainvoke=AsyncMock(return_value={"response": "ok"}))
    monkeypatch.setattr(app.state, "stockpilot_workflow", workflow)
    client = TestClient(app)
    response = client.post(
        "/assistant", headers=customer_headers(
            " customer-a ", **{"X-Customer-Id": "customer-b"},
        ),
        json={"message": "hello", "thread_id": "same", "customer_id": "customer-b"},
    )
    assert response.status_code == 200
    workflow.ainvoke.assert_awaited_once_with(
        {"message": "hello", "customer_id": "customer-a"},
        config={"configurable": {"thread_id": customer_thread_key("customer-a", "same")}},
    )
    operation = client.get("/openapi.json").json()["paths"]["/assistant"]["post"]
    assert operation["security"] == [{"CustomerBearer": []}]
    assert not any(parameter["name"] == "X-Customer-Id"
                   for parameter in operation.get("parameters", []))


def test_checkpoint_pair_encoding_is_stable_unambiguous_and_not_raw_identity():
    key = customer_thread_key("customer-a", "thread-b")
    assert key == customer_thread_key("customer-a", "thread-b")
    assert "customer-a" not in key
    assert key != "thread-b"
    assert key != customer_thread_key("customer-b", "thread-b")
    assert key != customer_thread_key("customer-a", "thread-a")
    assert customer_thread_key("a:b", "c") != customer_thread_key("a", "b:c")
    assert customer_thread_key('a","b', "c") != customer_thread_key("a", 'b","c')


@pytest.mark.parametrize("use_case,port", [
    (CreateOrderUseCase, "create"), (FinalizeOrderUseCase, "execute"),
])
def test_customer_facing_creation_propagates_customer_and_technical_none_stays_legacy(use_case, port):
    repository = SimpleNamespace(**{port: AsyncMock(side_effect=lambda order: order)})
    create = use_case(repository)
    owned = asyncio.run(create.execute("Bosch X100", 1, "owned-op", customer_id="customer-a"))
    legacy = asyncio.run(create.execute("Bosch X100", 1, "legacy-op"))
    assert owned.customer_id == "customer-a"
    assert legacy.customer_id is None
    assert getattr(repository, port).await_args_list[0].args[0].customer_id == "customer-a"


@pytest.mark.parametrize("use_case,port", [
    (CreateOrderUseCase, "create"), (FinalizeOrderUseCase, "execute"),
])
@pytest.mark.parametrize("customer_id", ["", " \t", 123])
def test_explicit_invalid_customer_cannot_create_order(use_case, port, customer_id):
    repository = SimpleNamespace(**{port: AsyncMock()})
    with pytest.raises(ValueError):
        asyncio.run(use_case(repository).execute("Bosch X100", 1, "op", customer_id=customer_id))
    getattr(repository, port).assert_not_awaited()


@pytest.mark.parametrize("customer_id", [None, "", " ", "customer-b", "Customer-a"])
@pytest.mark.parametrize("stored_customer", [None, "", " ", "customer-a"])
def test_missing_wrong_or_legacy_customer_is_indistinguishable_from_absent_order(customer_id, stored_customer):
    order = replace(make_order(), customer_id=stored_customer)
    repository = SimpleNamespace(find_by_order_id=AsyncMock(return_value=order))
    status = GetOrderStatusUseCase(repository)
    denied = asyncio.run(status.execute(order.order_id, customer_id=customer_id))
    repository.find_by_order_id.return_value = None
    missing = asyncio.run(status.execute(order.order_id, customer_id=customer_id))
    assert denied == missing
    assert denied.result == "NOT_FOUND"
    assert denied.status is None
    assert denied.response == f"Commande {order.order_id} introuvable (NOT_FOUND)."


def test_matching_customer_reads_persisted_status_and_no_identifier_is_no_association():
    order = replace(make_order(), customer_id="customer-a")
    repository = SimpleNamespace(find_by_order_id=AsyncMock(return_value=order))
    status = GetOrderStatusUseCase(repository)
    found = asyncio.run(status.execute(order.order_id, customer_id="customer-a"))
    assert found.result == "FOUND"
    assert found.status == order.status
    assert asyncio.run(status.execute(None, customer_id="customer-a")).result == "NO_ORDER_ASSOCIATED"
    repository.find_by_order_id.assert_awaited_once_with(order.order_id)


def test_status_node_rechecks_cached_foreign_id_and_clears_stale_status():
    order = replace(make_order(), customer_id="customer-a")
    repository = SimpleNamespace(find_by_order_id=AsyncMock(return_value=order))
    state = {
        "customer_id": "customer-b", "last_created_order_id": order.order_id,
        "queried_order_status": "CREATED", "order_status_result": "FOUND",
    }
    update = asyncio.run(create_order_status_node(GetOrderStatusUseCase(repository))(state))
    assert update["queried_order_status"] is None
    assert update["order_status_result"] == "NOT_FOUND"
    assert update["response"] == f"Commande {order.order_id} introuvable (NOT_FOUND)."
    assert repository.find_by_order_id.await_args.args == (order.order_id,)


def test_finalize_transaction_persists_owner_with_single_stock_decrement():
    database = FakeDatabase()
    finalizer = FinalizeOrderUseCase(MongoOrderTransaction(database))
    first = asyncio.run(finalizer.execute("Bosch X100", 3, "owned-op", customer_id="customer-a"))
    replay = asyncio.run(finalizer.execute("Bosch X100", 3, "owned-op", customer_id="customer-a"))
    assert replay == first
    assert first.customer_id == "customer-a"
    assert database.documents["owned-op"]["customer_id"] == "customer-a"
    assert database.stock == 7
    assert len(database.writes) == 2
    with pytest.raises(IdempotencyConflictError):
        asyncio.run(finalizer.execute("Bosch X100", 3, "owned-op", customer_id="customer-b"))
    assert database.stock == 7
    assert len(database.writes) == 2


def test_mongo_persists_customer_and_missing_field_deserializes_as_unowned():
    repository, collection = make_repository()
    owned = replace(make_order(), customer_id="customer-a")
    asyncio.run(repository.create(owned))
    persisted = collection.insert_one.call_args.args[0]
    assert persisted["customer_id"] == "customer-a"
    assert repository._to_order(persisted) == owned
    assert repository._to_order(order_document(make_order())).customer_id is None


@pytest.mark.parametrize("customer_id", ["customer-a", "customer-b", None])
def test_create_find_existing_checks_customer_before_returning_order(customer_id):
    order = replace(make_order(), customer_id="customer-a")
    repository = SimpleNamespace(find_by_operation_id=AsyncMock(return_value=order))
    lookup = CreateOrderUseCase(repository).find_existing(
        order.operation_id, order.product_name, order.quantity, customer_id=customer_id,
    )
    if customer_id == "customer-a":
        assert asyncio.run(lookup) == order
    else:
        with pytest.raises(IdempotencyConflictError):
            asyncio.run(lookup)


@pytest.mark.parametrize("customer_id", ["customer-a", "customer-b", None])
def test_repository_duplicate_replay_checks_customer(customer_id):
    repository, collection = make_repository()
    original = replace(make_order(), customer_id="customer-a")
    collection.insert_one.side_effect = DuplicateKeyError("duplicate")
    collection.find_one.return_value = {**order_document(original), "customer_id": original.customer_id}
    replay = repository.create(replace(original, order_id="retry", customer_id=customer_id))
    if customer_id == "customer-a":
        assert asyncio.run(replay) == original
    else:
        with pytest.raises(IdempotencyConflictError):
            asyncio.run(replay)
    collection.update_one.assert_not_called()


@pytest.mark.parametrize("inside", [False, True])
@pytest.mark.parametrize("customer_id", ["customer-a", "customer-b", None])
def test_transaction_owner_replay_never_reserves_stock(inside, customer_id):
    database = FakeDatabase()
    original = replace(make_order(), customer_id="customer-a")
    if inside:
        database.winner_on_attempt = original
    else:
        database.documents[original.operation_id] = document(original)
    retry = replace(original, order_id="retry", customer_id=customer_id)
    if customer_id == "customer-a":
        assert execute(database, retry) == original
    else:
        with pytest.raises(IdempotencyConflictError):
            execute(database, retry)
    assert database.stock == 10
    assert database.writes == []


@pytest.mark.parametrize("error", [DuplicateKeyError("duplicate"), labeled_error("TransientTransactionError")])
def test_reconciliation_after_abort_checks_winning_customer_and_rolls_back_stock(error):
    database = FakeDatabase()
    original = replace(make_order(), customer_id="customer-a")
    database.insert_errors = [error]
    database.winner_after_abort = original
    database.retry_transient = True
    with pytest.raises(IdempotencyConflictError):
        execute(database, replace(original, customer_id="customer-b"))
    assert database.stock == 10
    assert database.documents == {original.operation_id: document(original)}
    assert database.session.commits == 0


@pytest.mark.parametrize("pending_customer,current_customer", [
    ("customer-a", "customer-b"), ("customer-a", None), (None, "customer-a"),
])
def test_direct_graph_confirmation_fails_closed_for_changed_or_legacy_pending_owner(pending_customer, current_customer):
    finalizer = SimpleNamespace(execute=AsyncMock())
    state = {
        "message": "Oui, je confirme", "customer_id": current_customer,
        "order_status": "PENDING_CONFIRMATION",
        "pending_order": {
            "operation_id": "op", "product_name": "Bosch X100", "quantity": 1,
            "status": "PENDING_CONFIRMATION", "customer_id": pending_customer,
        },
    }
    with pytest.raises(ValueError):
        asyncio.run(create_order_confirmation_node(finalizer)(state))
    finalizer.execute.assert_not_awaited()


def test_http_same_thread_customers_have_isolated_pending_orders_and_status(monkeypatch):
    workflow, _, _, repository, products, reservations, _ = make_workflow()
    monkeypatch.setattr(app.state, "stockpilot_workflow", workflow)
    client = TestClient(app)

    def post(customer, message, thread="shared"):
        response = client.post(
            "/assistant", headers=customer_headers(customer),
            json={"message": message, "thread_id": thread},
        )
        assert response.status_code == 200
        return response.json()

    # Old raw-thread checkpoints are never consulted by the namespaced HTTP path.
    asyncio.run(workflow.ainvoke(
        {"message": "Commande-moi Bosch X100"},
        config={"configurable": {"thread_id": "shared"}},
    ))
    empty = post("customer-a", "Oui, je confirme")
    assert empty.get("pending_order") is None
    assert empty.get("created_order_id") is None
    prepared = post("customer-a", "Commande-moi 3 Bosch X100")
    foreign = post("customer-b", "Oui, je confirme")
    assert foreign.get("pending_order") is None
    assert foreign.get("last_created_order_id") is None
    assert reservations == []
    other_pending = post("customer-b", "Commande-moi Makita D200")
    assert other_pending["pending_order"]["operation_id"] != prepared["pending_order"]["operation_id"]
    confirmed = post("customer-a", "Oui, je confirme")
    own_id = confirmed["created_order_id"]
    assert repository.orders[0].customer_id == "customer-a"
    assert post("customer-a", "Où en est ma commande ?")["queried_order_status"] == "CREATED"
    foreign_status = post("customer-b", f"Statut commande {own_id}")
    assert foreign_status["order_status_result"] == "NOT_FOUND"
    assert foreign_status["queried_order_status"] is None
    assert foreign_status.get("last_created_order_id") is None
    assert foreign_status.get("created_order_id") is None
    assert foreign_status["pending_order"] == other_pending["pending_order"]
    missing_status = post("customer-b", "Statut commande missing")
    assert missing_status["queried_order_status"] is None
    assert missing_status["order_status_result"] == "NOT_FOUND"
    no_order = post("customer-b", "Où en est ma commande ?")
    assert no_order["order_status_result"] == "NO_ORDER_ASSOCIATED"
    assert post("customer-a", "Où en est ma commande ?", thread="fresh")["order_status_result"] == "NO_ORDER_ASSOCIATED"
    assert post("customer-a", f"Statut commande {own_id}", thread="fresh")["order_status_result"] == "FOUND"
    confirmed_b = post("customer-b", "Oui, je confirme")
    assert confirmed_b["created_order_id"] != own_id
    assert repository.orders[1].customer_id == "customer-b"
    assert post("customer-a", "Où en est ma commande ?")["queried_order_id"] == own_id
    assert products["Bosch X100"].stock == 9
    assert products["Makita D200"].stock == 6
    assert reservations == [("Bosch X100", 3), ("Makita D200", 1)]
