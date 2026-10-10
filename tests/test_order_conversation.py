import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID
from datetime import datetime, timezone

import pytest

from langgraph.checkpoint.memory import MemorySaver

from stockpilot.application.dto.intent_result import IntentResult
from stockpilot.application.order_idempotency import IdempotencyConflictError
from stockpilot.application.order_idempotency import validate_order_payload
from stockpilot.application.order_transaction_errors import (
    OrderOutcomeUnknownError,
    OrderTransactionFailedError,
)
from stockpilot.application.dto.product_recommendation import ProductRecommendation
from stockpilot.application.use_cases.finalize_order import FinalizeOrderUseCase
from stockpilot.application.use_cases.get_order_status import GetOrderStatusUseCase
from stockpilot.application.use_cases.check_stock import CheckStockUseCase
from stockpilot.application.use_cases.prepare_order import PrepareOrderUseCase
from stockpilot.application.dto.stock_reservation_result import (
    StockReservationResult,
)
from stockpilot.application.workflows.stockpilot_workflow import build_graph
from stockpilot.domain.entities.product import Product
from stockpilot.domain.entities.order import Order
from stockpilot.domain.enums.intent import Intent
from stockpilot.domain.enums.order_status import OrderStatus
from stockpilot.domain.enums.product_category import ProductCategory


def make_product(name: str, stock: int = 12) -> Product:
    return Product(
        sku=name.upper().replace(" ", "-"),
        name=name,
        category=ProductCategory.DRILL,
        price=129.99,
        stock=stock,
    )


def make_workflow(
    bosch_stock: int = 12,
    *,
    fail_order_creation: bool = False,
    fail_after_insert: bool = False,
    unknown_outcome: bool = False,
):
    products = {
        "Bosch X100": make_product("Bosch X100", stock=bosch_stock),
        "Makita D200": make_product("Makita D200", stock=7),
    }

    reservation_calls: list[tuple[str, int]] = []
    compensation_calls: list[tuple[str, int]] = []

    class FakeProductRepository:
        async def find_by_name(self, name: str) -> Product | None:
            return products.get(name)

        async def reserve_stock(
            self,
            product_name: str,
            quantity: int,
        ) -> StockReservationResult:
            reservation_calls.append((product_name, quantity))
            product = products.get(product_name)
            if product is None or product.stock < quantity:
                return StockReservationResult(success=False)
            remaining_stock = product.stock - quantity
            products[product_name] = Product(
                sku=product.sku,
                name=product.name,
                category=product.category,
                price=product.price,
                stock=remaining_stock,
            )
            return StockReservationResult(
                success=True,
                remaining_stock=remaining_stock,
            )

        async def release_stock(self, product_name: str, quantity: int) -> bool:
            compensation_calls.append((product_name, quantity))
            product = products.get(product_name)
            if product is None:
                return False
            products[product_name] = Product(
                sku=product.sku,
                name=product.name,
                category=product.category,
                price=product.price,
                stock=product.stock + quantity,
            )
            return True

    async def classify(message: str) -> IntentResult:
        if message.startswith("Statut commande "):
            return IntentResult(
                intent=Intent.ORDER_STATUS, order_id=message.removeprefix("Statut commande "),
            )
        if message in ("Où en est ma commande ?", "Quel est le statut de ma dernière commande ?"):
            return IntentResult(intent=Intent.ORDER_STATUS)
        if "Commande-moi 3 Bosch X100" in message:
            return IntentResult(
                intent=Intent.ORDER,
                product_name="Bosch X100",
                quantity=3,
            )
        if "Commande-moi 20 Bosch X100" in message:
            return IntentResult(
                intent=Intent.ORDER,
                product_name="Bosch X100",
                quantity=20,
            )
        if "Commande-moi Bosch X100" in message:
            return IntentResult(
                intent=Intent.ORDER,
                product_name="Bosch X100",
                quantity=None,
            )
        if "Commande-moi Makita D200" in message:
            return IntentResult(intent=Intent.ORDER, product_name="Makita D200")
        if "disponible" in message:
            return IntentResult(
                intent=Intent.STOCK_CHECK,
                product_name="Bosch X100",
            )
        if "perceuse" in message:
            return IntentResult(intent=Intent.PRODUCT_ADVICE)
        return IntentResult(intent=Intent.UNKNOWN)

    check_stock = CheckStockUseCase(FakeProductRepository())
    class FakeOrderRepository:
        def __init__(self) -> None:
            self.orders: list[Order] = []

        async def create(self, order: Order) -> Order:
            if fail_order_creation:
                raise RuntimeError("simulated order insert failure")
            self.orders.append(order)
            if fail_after_insert:
                raise TimeoutError("simulated timeout after insert")
            return order

        async def find_by_operation_id(self, operation_id: str) -> Order | None:
            return next(
                (order for order in self.orders if order.operation_id == operation_id),
                None,
            )

        async def find_by_order_id(self, order_id: str) -> Order | None:
            return next((order for order in self.orders if order.order_id == order_id), None)

    order_repository = FakeOrderRepository()
    class FakeOrderTransaction:
        async def execute(self, order: Order) -> Order | None:
            existing = await order_repository.find_by_operation_id(order.operation_id)
            if existing is not None:
                validate_order_payload(existing, order.product_name, order.quantity)
                return existing
            if unknown_outcome:
                raise OrderOutcomeUnknownError("simulated commit outcome unknown")
            original = products.copy()
            reservation = await FakeProductRepository().reserve_stock(
                order.product_name, order.quantity
            )
            if not reservation.success:
                return None
            try:
                return await order_repository.create(order)
            except TimeoutError:
                # Simulate an already committed transaction recovered by the adapter.
                return order_repository.orders[0]
            except RuntimeError as error:
                products.clear()
                products.update(original)
                raise OrderTransactionFailedError("transaction aborted") from error

    finalize_order = FinalizeOrderUseCase(FakeOrderTransaction())
    classify_use_case = SimpleNamespace(execute=AsyncMock(side_effect=classify))
    recommend_product = SimpleNamespace(
        execute=AsyncMock(
            return_value=ProductRecommendation(
                product_name="Bosch X100",
                answer="Bosch X100 convient.",
            )
        )
    )
    workflow = build_graph(
        classify_use_case,
        check_stock,
        recommend_product,
        PrepareOrderUseCase(check_stock),
        finalize_order,
        checkpointer=MemorySaver(),
        get_order_status=GetOrderStatusUseCase(order_repository),
    )
    return (
        workflow,
        classify_use_case,
        recommend_product,
        order_repository,
        products,
        reservation_calls,
        compensation_calls,
    )


def invoke(workflow, message: str, thread_id: str) -> dict:
    return asyncio.run(
        workflow.ainvoke(
            {"message": message},
            config={"configurable": {"thread_id": thread_id}},
        )
    )


def get_state(workflow, thread_id: str) -> dict:
    snapshot = workflow.get_state(
        {"configurable": {"thread_id": thread_id}}
    )
    return snapshot.values


def test_status_after_confirmation_and_cancelled_new_order_keeps_last_success():
    workflow, classifier, recommender, repo, products, reservations, releases = make_workflow()
    invoke(workflow, "Commande-moi 3 Bosch X100", "status-thread")
    confirmed = invoke(workflow, "Oui, je confirme", "status-thread")
    order_id = repo.orders[0].order_id
    assert confirmed["last_created_order_id"] == order_id

    queried = invoke(workflow, "Où en est ma commande ?", "status-thread")
    assert queried["queried_order_id"] == order_id
    assert queried["queried_order_status"] == "CREATED"
    assert queried["order_status_result"] == "FOUND"
    assert queried["order_status"] == "CONFIRMED"
    assert queried["pending_order"] == confirmed["pending_order"]

    prepared = invoke(workflow, "Commande-moi Makita D200", "status-thread")
    assert prepared["last_created_order_id"] == order_id
    queried_pending = invoke(workflow, "Où en est ma commande ?", "status-thread")
    assert queried_pending["pending_order"] == prepared["pending_order"]
    assert queried_pending["order_status"] == "PENDING_CONFIRMATION"
    assert queried_pending["queried_order_status"] == "CREATED"
    cancelled = invoke(workflow, "Non", "status-thread")
    assert cancelled["created_order_id"] is None
    assert cancelled["last_created_order_id"] == order_id
    final = invoke(workflow, "Quel est le statut de ma dernière commande ?", "status-thread")
    assert final["queried_order_id"] == order_id
    assert final["queried_order_status"] == "CREATED"
    assert final["order_status"] == "CANCELLED"
    assert final["pending_order"] == cancelled["pending_order"]
    assert final["response"] == f"Commande {order_id} : statut CREATED."
    assert len(repo.orders) == 1
    assert reservations == [("Bosch X100", 3)]
    assert releases == []
    assert products["Bosch X100"].stock == 9
    assert products["Makita D200"].stock == 7
    recommender.execute.assert_not_awaited()
    assert classifier.execute.await_count == 5


def test_explicit_status_overrides_thread_order_and_is_cleared_next_turn():
    workflow, _, recommender, repo, _, reservations, _ = make_workflow()
    for thread, message in (
        ("own-thread", "Commande-moi 3 Bosch X100"),
        ("other-thread", "Commande-moi Makita D200"),
    ):
        invoke(workflow, message, thread)
        invoke(workflow, "Oui, je confirme", thread)
    own_id, other_id = (order.order_id for order in repo.orders)
    explicit = invoke(workflow, f"Statut commande {other_id}", "own-thread")
    assert explicit["order_id"] == other_id
    assert explicit["queried_order_id"] == other_id
    assert explicit["last_created_order_id"] == own_id

    implicit = invoke(workflow, "Où en est ma commande ?", "own-thread")
    assert implicit["order_id"] is None
    assert implicit["queried_order_id"] == own_id
    missing = invoke(workflow, "Statut commande missing-id", "own-thread")
    assert missing["order_status_result"] == "NOT_FOUND"
    assert missing["queried_order_id"] == "missing-id"
    assert missing["queried_order_status"] is None
    assert missing["response"] == "Commande missing-id introuvable (NOT_FOUND)."
    assert missing["last_created_order_id"] == own_id
    assert missing["order_status"] == "CONFIRMED"
    recovered = invoke(workflow, "Où en est ma commande ?", "own-thread")
    assert recovered["order_id"] is None
    assert recovered["queried_order_id"] == own_id
    invoke(workflow, f"Statut commande {other_id}", "own-thread")
    stock_turn = invoke(workflow, "Bosch X100 disponible ?", "own-thread")
    assert stock_turn["order_id"] is None
    assert len(repo.orders) == 2
    assert reservations == [("Bosch X100", 3), ("Makita D200", 1)]
    recommender.execute.assert_not_awaited()


def test_status_without_associated_order_is_isolated_between_threads():
    workflow, _, _, repo, _, reservations, _ = make_workflow()
    invoke(workflow, "Commande-moi Bosch X100", "confirmed-thread")
    invoke(workflow, "Oui, je confirme", "confirmed-thread")
    result = invoke(workflow, "Où en est ma commande ?", "unrelated-thread")
    assert result["order_status_result"] == "NO_ORDER_ASSOCIATED"
    assert result["queried_order_id"] is None
    assert result["queried_order_status"] is None
    assert result["response"] == "Aucune commande n'est associée à cette conversation."
    assert result.get("last_created_order_id") is None
    assert len(repo.orders) == 1
    assert reservations == [("Bosch X100", 1)]


def test_status_during_first_pending_order_does_not_use_operation_id_or_confirm():
    workflow, _, _, repo, _, reservations, _ = make_workflow()
    prepared = invoke(workflow, "Commande-moi Bosch X100", "first-pending")
    assert prepared["pending_order"]["operation_id"]
    result = invoke(workflow, "Où en est ma commande ?", "first-pending")
    assert result["order_status_result"] == "NO_ORDER_ASSOCIATED"
    assert result["queried_order_id"] is None
    assert result["pending_order"] == prepared["pending_order"]
    assert result["order_status"] == "PENDING_CONFIRMATION"
    assert repo.orders == []
    assert reservations == []


def test_second_successful_confirmation_replaces_last_order_reference():
    workflow, _, _, repo, _, _, _ = make_workflow()
    invoke(workflow, "Commande-moi Bosch X100", "two-successes")
    first = invoke(workflow, "Oui, je confirme", "two-successes")
    invoke(workflow, "Commande-moi Makita D200", "two-successes")
    second = invoke(workflow, "Oui, je confirme", "two-successes")
    assert first["last_created_order_id"] != second["last_created_order_id"]
    assert second["last_created_order_id"] == repo.orders[-1].order_id
    queried = invoke(workflow, "Où en est ma commande ?", "two-successes")
    assert queried["queried_order_id"] == second["last_created_order_id"]


def test_explicit_status_while_pending_preserves_order_and_does_not_confirm():
    workflow, _, recommender, repo, _, reservations, releases = make_workflow()
    invoke(workflow, "Commande-moi Bosch X100", "known-order-thread")
    created = invoke(workflow, "Oui, je confirme", "known-order-thread")
    order_id = created["created_order_id"]
    prepared = invoke(workflow, "Commande-moi Makita D200", "pending-status-thread")

    found = invoke(workflow, f"Statut commande {order_id}", "pending-status-thread")
    assert found["queried_order_id"] == order_id
    assert found["queried_order_status"] == "CREATED"
    assert found["order_status"] == "PENDING_CONFIRMATION"
    assert found["pending_order"] == prepared["pending_order"]
    assert found.get("last_created_order_id") is None
    missing = invoke(workflow, "Statut commande missing", "pending-status-thread")
    assert missing["order_status_result"] == "NOT_FOUND"
    assert missing["queried_order_status"] is None
    assert missing["pending_order"] == prepared["pending_order"]
    assert missing["order_status"] == "PENDING_CONFIRMATION"
    assert len(repo.orders) == 1
    assert reservations == [("Bosch X100", 1)]
    assert releases == []
    recommender.execute.assert_not_awaited()


def test_order_confirmation_uses_pending_order_from_same_thread() -> None:
    workflow, classifier, _, order_repository, products, reservations, releases = (
        make_workflow()
    )

    prepared = invoke(workflow, "Commande-moi Bosch X100", "thread-1")
    assert prepared["order_status"] == "PENDING_CONFIRMATION"
    assert order_repository.orders == []

    confirmed = invoke(workflow, "Oui, je confirme", "thread-1")

    assert prepared["order_status"] == "PENDING_CONFIRMATION"
    assert confirmed["order_status"] == "CONFIRMED"
    assert confirmed["created_order_id"] == order_repository.orders[0].order_id
    assert confirmed["pending_order"] == {
        "operation_id": prepared["pending_order"]["operation_id"],
        "product_name": "Bosch X100",
        "quantity": 1,
        "status": "CONFIRMED",
    }
    UUID(prepared["pending_order"]["operation_id"])
    assert order_repository.orders[0].operation_id == prepared["pending_order"]["operation_id"]
    assert confirmed["response"] == (
        f"Commande {confirmed['created_order_id']} créée pour 1 Bosch X100."
    )
    assert len(order_repository.orders) == 1
    assert classifier.execute.await_count == 1
    assert reservations == [("Bosch X100", 1)]
    assert releases == []
    assert products["Bosch X100"].stock == 11


def test_explicit_quantity_is_preserved_from_request_through_confirmation() -> None:
    workflow, classifier, _, order_repository, products, reservations, _ = (
        make_workflow(bosch_stock=11)
    )

    prepared = invoke(workflow, "Commande-moi 3 Bosch X100", "qty-thread")

    assert classifier.execute.await_count == 1
    assert prepared["order_status"] == "PENDING_CONFIRMATION"
    assert prepared["pending_order"] == {
        "operation_id": prepared["pending_order"]["operation_id"],
        "product_name": "Bosch X100",
        "quantity": 3,
        "status": "PENDING_CONFIRMATION",
    }
    assert prepared["response"] == (
        "La Bosch X100 est disponible avec 11 unités en stock. "
        "Confirmez-vous la commande de 3 Bosch X100 ?"
    )
    assert reservations == []

    confirmed = invoke(workflow, "Oui, je confirme", "qty-thread")

    assert confirmed["order_status"] == "CONFIRMED"
    assert confirmed["pending_order"]["quantity"] == 3
    assert confirmed["response"] == (
        f"Commande {confirmed['created_order_id']} créée pour 3 Bosch X100."
    )
    assert reservations == [("Bosch X100", 3)]
    assert products["Bosch X100"].stock == 8
    assert len(order_repository.orders) == 1
    assert order_repository.orders[0].quantity == 3
    assert order_repository.orders[0].operation_id == prepared["pending_order"]["operation_id"]
    assert classifier.execute.await_count == 1


def test_quantity_above_available_stock_does_not_create_pending_order() -> None:
    workflow, _, _, order_repository, products, reservations, _ = make_workflow(
        bosch_stock=11
    )

    result = invoke(workflow, "Commande-moi 20 Bosch X100", "too-many-thread")

    assert result["order_status"] == "INSUFFICIENT_STOCK"
    assert result.get("pending_order") is None
    assert result["response"] == (
        "Il ne reste que 11 unité(s) de Bosch X100, mais 20 sont demandées."
    )
    assert reservations == []
    assert order_repository.orders == []
    assert products["Bosch X100"].stock == 11


def test_order_can_be_cancelled_on_second_turn() -> None:
    workflow, classifier, _, order_repository, _, reservations, releases = make_workflow()

    invoke(workflow, "Commande-moi Bosch X100", "cancel-thread")
    cancelled = invoke(workflow, "Non, annule", "cancel-thread")

    assert cancelled["order_status"] == "CANCELLED"
    assert cancelled["pending_order"]["status"] == "CANCELLED"
    assert cancelled["response"] == "Commande annulée."
    assert order_repository.orders == []
    assert reservations == []
    assert releases == []
    assert classifier.execute.await_count == 1


def test_confirmation_rejects_stock_depleted_after_preparation() -> None:
    workflow, _, _, order_repository, products, reservations, releases = make_workflow()
    invoke(workflow, "Commande-moi Bosch X100", "race-thread")
    prepared_product = products["Bosch X100"]
    products["Bosch X100"] = Product(
        sku=prepared_product.sku,
        name=prepared_product.name,
        category=prepared_product.category,
        price=prepared_product.price,
        stock=0,
    )

    result = invoke(workflow, "Oui, je confirme", "race-thread")

    assert result["order_status"] == "RESERVATION_FAILED"
    assert result["pending_order"]["status"] == "RESERVATION_FAILED"
    assert result["response"] == (
        "Le stock de Bosch X100 n'est plus suffisant "
        "pour finaliser la commande."
    )
    assert products["Bosch X100"].stock == 0
    assert order_repository.orders == []
    assert reservations == [("Bosch X100", 1)]
    assert releases == []


def test_create_failure_rolls_back_without_manual_release() -> None:
    workflow, _, _, order_repository, products, reservations, releases = (
        make_workflow(bosch_stock=8, fail_order_creation=True)
    )
    invoke(workflow, "Commande-moi 3 Bosch X100", "create-fail-thread")

    result = invoke(workflow, "Oui, je confirme", "create-fail-thread")

    assert reservations == [("Bosch X100", 3)]
    assert releases == []
    assert products["Bosch X100"].stock == 8
    assert order_repository.orders == []
    assert result["order_status"] == "ORDER_CREATION_FAILED"
    assert result["pending_order"]["status"] == "ORDER_CREATION_FAILED"
    assert result["created_order_id"] is None
    assert result["response"] == (
        "Impossible de finaliser la commande. "
        "La transaction a été annulée, sans modification du stock."
    )


def test_replay_before_reservation_returns_existing_order() -> None:
    workflow, _, _, repository, products, reservations, releases = make_workflow()
    prepared = invoke(workflow, "Commande-moi 3 Bosch X100", "replay-thread")
    existing = Order(
        order_id="existing-order",
        operation_id=prepared["pending_order"]["operation_id"],
        product_name="Bosch X100",
        quantity=3,
        status=OrderStatus.CREATED,
        created_at=datetime.now(timezone.utc),
    )
    repository.orders.append(existing)

    result = invoke(workflow, "Oui, je confirme", "replay-thread")

    assert result["order_status"] == "CONFIRMED"
    assert result["created_order_id"] == existing.order_id
    assert reservations == []
    assert releases == []
    assert repository.orders == [existing]
    assert products["Bosch X100"].stock == 12


def test_timeout_after_commit_recovers_without_compensation() -> None:
    workflow, _, _, repository, products, reservations, releases = make_workflow(
        bosch_stock=11, fail_after_insert=True
    )
    prepared = invoke(workflow, "Commande-moi 3 Bosch X100", "timeout-thread")

    result = invoke(workflow, "Oui, je confirme", "timeout-thread")

    assert result["order_status"] == "CONFIRMED"
    assert result["created_order_id"] == repository.orders[0].order_id
    assert repository.orders[0].operation_id == prepared["pending_order"]["operation_id"]
    assert reservations == [("Bosch X100", 3)]
    assert releases == []
    assert products["Bosch X100"].stock == 8


def test_failed_reconciliation_never_restores_stock_blindly() -> None:
    workflow, _, _, repository, _, reservations, releases = make_workflow(
        unknown_outcome=True
    )
    invoke(workflow, "Commande-moi 3 Bosch X100", "lookup-fail-thread")

    result = invoke(workflow, "Oui, je confirme", "lookup-fail-thread")
    assert result["order_status"] == "ORDER_OUTCOME_UNKNOWN"
    assert result["created_order_id"] is None
    assert "intervention manuelle" in result["response"]
    invoke(workflow, "Oui, je confirme", "lookup-fail-thread")

    assert reservations == []
    assert releases == []


def test_replay_payload_conflict_is_rejected_before_reservation() -> None:
    workflow, _, _, repository, _, reservations, releases = make_workflow()
    prepared = invoke(workflow, "Commande-moi 3 Bosch X100", "conflict-thread")
    repository.orders.append(Order(
        order_id="existing-order",
        operation_id=prepared["pending_order"]["operation_id"],
        product_name="Bosch X100",
        quantity=5,
        status=OrderStatus.CREATED,
        created_at=datetime.now(timezone.utc),
    ))

    with pytest.raises(IdempotencyConflictError):
        invoke(workflow, "Oui, je confirme", "conflict-thread")

    assert reservations == []
    assert releases == []


def test_double_confirmation_for_three_units_only_reserves_once() -> None:
    workflow, _, _, repository, products, reservations, releases = make_workflow(
        bosch_stock=11
    )
    invoke(workflow, "Commande-moi 3 Bosch X100", "double-three")
    first = invoke(workflow, "Oui, je confirme", "double-three")
    second = invoke(workflow, "Oui, je confirme", "double-three")

    assert second["created_order_id"] == first["created_order_id"]
    assert reservations == [("Bosch X100", 3)]
    assert len(repository.orders) == 1
    assert products["Bosch X100"].stock == 8
    assert releases == []


def test_cancel_three_units_never_invokes_transaction() -> None:
    from stockpilot.application.workflows.nodes import create_order_confirmation_node

    finalize = SimpleNamespace(execute=AsyncMock())
    node = create_order_confirmation_node(finalize)
    result = asyncio.run(node({
        "message": "Non, annule",
        "order_status": "PENDING_CONFIRMATION",
        "pending_order": {
            "operation_id": "saved-operation",
            "product_name": "Bosch X100",
            "quantity": 3,
            "status": "PENDING_CONFIRMATION",
        },
    }))
    assert result["order_status"] == "CANCELLED"
    finalize.execute.assert_not_awaited()


def test_confirmation_passes_exact_operation_to_transactional_use_case() -> None:
    from stockpilot.application.workflows.nodes import create_order_confirmation_node

    order = Order(
        order_id="persisted-id",
        operation_id="saved-operation",
        product_name="Bosch X100",
        quantity=3,
        status=OrderStatus.CREATED,
        created_at=datetime.now(timezone.utc),
    )
    finalize = SimpleNamespace(execute=AsyncMock(return_value=order))
    result = asyncio.run(create_order_confirmation_node(finalize)({
        "message": "Oui, je confirme",
        "order_status": "PENDING_CONFIRMATION",
        "pending_order": {
            "operation_id": "saved-operation",
            "product_name": "Bosch X100",
            "quantity": 3,
            "status": "PENDING_CONFIRMATION",
        },
    }))
    finalize.execute.assert_awaited_once_with(
        "Bosch X100", 3, operation_id="saved-operation"
    )
    assert result["created_order_id"] == order.order_id


def test_threads_keep_pending_orders_isolated() -> None:
    workflow, _, _, order_repository, _, _, _ = make_workflow()

    invoke(workflow, "Commande-moi Bosch X100", "thread-A")
    invoke(workflow, "Commande-moi Makita D200", "thread-B")
    confirmed = invoke(workflow, "Oui, je confirme", "thread-A")
    confirmed_b = invoke(workflow, "Oui, je confirme", "thread-B")

    state_a = get_state(workflow, "thread-A")
    state_b = get_state(workflow, "thread-B")
    assert confirmed["pending_order"]["product_name"] == "Bosch X100"
    assert confirmed_b["pending_order"]["product_name"] == "Makita D200"
    assert state_a["pending_order"]["status"] == "CONFIRMED"
    assert state_b["pending_order"]["status"] == "CONFIRMED"
    assert state_a["created_order_id"] != state_b["created_order_id"]
    assert {order.product_name for order in order_repository.orders} == {
        "Bosch X100",
        "Makita D200",
    }


def test_confirmation_without_pending_order_does_not_invent_order() -> None:
    workflow, _, _, order_repository, _, reservations, releases = make_workflow()

    result = invoke(workflow, "Oui, je confirme", "empty-thread")

    assert result["response"] == "I could not determine the requested action."
    assert result.get("pending_order") is None
    assert result.get("order_status") is None
    assert order_repository.orders == []
    assert reservations == []
    assert releases == []


def test_confirmed_order_cannot_be_confirmed_again() -> None:
    workflow, classifier, _, order_repository, _, reservations, releases = make_workflow()

    invoke(workflow, "Commande-moi Bosch X100", "once-thread")
    invoke(workflow, "Oui, je confirme", "once-thread")
    repeated = invoke(workflow, "Oui, je confirme", "once-thread")

    assert repeated["response"] != "Commande confirmée pour 1 Bosch X100."
    assert repeated["pending_order"]["status"] == "CONFIRMED"
    assert repeated["order_status"] == "CONFIRMED"
    assert classifier.execute.await_count == 2
    assert len(order_repository.orders) == 1
    assert reservations == [("Bosch X100", 1)]
    assert releases == []


def test_stock_check_and_product_advice_still_route_normally() -> None:
    workflow, classifier, recommendation, _, _, _, _ = make_workflow()

    stock_result = invoke(
        workflow,
        "Est-ce que Bosch X100 est disponible ?",
        "normal-stock-thread",
    )
    advice_result = invoke(
        workflow,
        "Quelle perceuse pour béton ?",
        "normal-advice-thread",
    )

    assert stock_result["response"] == (
        "Bosch X100 est disponible avec 12 unité(s) en stock."
    )
    assert advice_result["response"].startswith("Bosch X100 convient.")
    assert classifier.execute.await_count == 2
    recommendation.execute.assert_awaited_once_with(
        "Quelle perceuse pour béton ?"
    )
