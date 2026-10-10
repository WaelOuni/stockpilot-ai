import asyncio
import inspect
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langgraph.checkpoint.memory import MemorySaver

from stockpilot.application.dto.intent_result import IntentResult
from stockpilot.application.dto.order_status_result import OrderStatusResult
from stockpilot.application.workflows.nodes import (
    create_classify_node, create_order_confirmation_node, create_order_status_node,
)
from stockpilot.application.order_transaction_errors import (
    OrderOutcomeUnknownError, OrderTransactionFailedError,
)
from stockpilot.application.workflows.router import route_by_intent
from stockpilot.application.workflows.stockpilot_workflow import build_graph
from stockpilot.domain.enums.intent import Intent
from stockpilot.domain.enums.order_status import OrderStatus


@pytest.mark.parametrize(("intent", "route"), [
    (Intent.ORDER_STATUS, "order_status"), (Intent.ORDER, "order"),
    (Intent.STOCK_CHECK, "stock"), (Intent.PRODUCT_ADVICE, "advice"),
    (Intent.UNKNOWN, "unknown"), (None, "unknown"),
])
def test_intent_routing_separates_read_only_status_from_writes(intent, route):
    assert route_by_intent({"intent": intent}) == route


def test_status_dependency_is_required_keyword_only_preserving_checkpointer_position():
    parameters = inspect.signature(build_graph).parameters
    assert list(parameters)[:6] == [
        "classify_intent", "check_stock", "recommend_product", "prepare_order",
        "finalize_order", "checkpointer",
    ]
    assert parameters["checkpointer"].default is None
    assert parameters["get_order_status"].kind == inspect.Parameter.KEYWORD_ONLY
    assert parameters["get_order_status"].default is inspect.Parameter.empty
    with pytest.raises(TypeError, match="get_order_status"):
        build_graph(*(SimpleNamespace(execute=AsyncMock()) for _ in range(5)))


@pytest.mark.parametrize(("identifiers", "expected"), [
    ({"order_id": "explicit", "last_created_order_id": "latest", "created_order_id": "legacy"}, "explicit"),
    ({"order_id": None, "last_created_order_id": "latest", "created_order_id": "legacy"}, "latest"),
    ({"created_order_id": "legacy"}, "legacy"),
    ({}, None),
])
def test_status_node_identifier_precedence_and_no_state_mutation(identifiers, expected):
    result = OrderStatusResult(
        order_id=expected, status=OrderStatus.CREATED if expected else None,
        result="FOUND" if expected else "NO_ORDER_ASSOCIATED", response="stored status",
    )
    use_case = SimpleNamespace(execute=AsyncMock(return_value=result))
    state = {
        "message": "Où en est ma commande ?", "order_status": "PENDING_CONFIRMATION",
        "pending_order": {"operation_id": "operation-not-order-id", "status": "PENDING_CONFIRMATION"},
        **identifiers,
    }
    original = deepcopy(state)
    update = asyncio.run(create_order_status_node(use_case)(state))
    use_case.execute.assert_awaited_once_with(expected)
    assert update == {
        "queried_order_id": expected, "queried_order_status": "CREATED" if expected else None,
        "order_status_result": result.result, "response": "stored status",
    }
    assert state == original
    assert not set(update) & {"order_status", "pending_order", "last_created_order_id", "created_order_id", "order_id"}


def test_missing_explicit_identifier_does_not_fall_back_to_last_order():
    use_case = SimpleNamespace(execute=AsyncMock(return_value=OrderStatusResult(
        order_id="missing", status=None, result="NOT_FOUND",
        response="Commande missing introuvable (NOT_FOUND).",
    )))
    update = asyncio.run(create_order_status_node(use_case)({
        "order_id": "missing", "last_created_order_id": "known",
    }))
    assert update["queried_order_id"] == "missing"
    assert update["queried_order_status"] is None
    assert update["order_status_result"] == "NOT_FOUND"
    use_case.execute.assert_awaited_once_with("missing")


@pytest.mark.parametrize("intent", list(Intent))
def test_classification_always_clears_a_stale_explicit_order_id(intent):
    classifier = SimpleNamespace(execute=AsyncMock(return_value=IntentResult(intent=intent)))
    update = asyncio.run(create_classify_node(classifier)({
        "message": "new turn", "order_id": "stale-explicit",
    }))
    assert "order_id" in update
    assert update["order_id"] is None
    classifier.execute.assert_awaited_once_with("new turn")


def test_status_graph_never_calls_generation_stock_or_write_use_cases():
    classifier = SimpleNamespace(execute=AsyncMock(return_value=IntentResult(
        intent=Intent.ORDER_STATUS, order_id="order-123",
    )))
    dependencies = [SimpleNamespace(execute=AsyncMock()) for _ in range(4)]
    status = SimpleNamespace(execute=AsyncMock(return_value=OrderStatusResult(
        order_id="order-123", status=OrderStatus.CREATED, result="FOUND",
        response="Commande order-123 : statut CREATED.",
    )))
    graph = build_graph(classifier, *dependencies, MemorySaver(), get_order_status=status)
    result = asyncio.run(graph.ainvoke(
        {"message": "Statut de la commande order-123 ?"},
        {"configurable": {"thread_id": "status-only"}},
    ))
    assert result["response"] == "Commande order-123 : statut CREATED."
    assert result["queried_order_status"] == "CREATED"
    assert result["order_status_result"] == "FOUND"
    for dependency in dependencies:
        dependency.execute.assert_not_awaited()
    status.execute.assert_awaited_once_with("order-123")


@pytest.mark.parametrize(("outcome", "expected_status"), [
    (None, "RESERVATION_FAILED"),
    (OrderTransactionFailedError("aborted"), "ORDER_CREATION_FAILED"),
    (OrderOutcomeUnknownError("unknown"), "ORDER_OUTCOME_UNKNOWN"),
])
def test_failed_new_confirmation_preserves_previous_successful_order(outcome, expected_status):
    finalizer = SimpleNamespace(execute=AsyncMock(
        return_value=None, side_effect=outcome,
    ))
    state = {
        "message": "Oui, je confirme", "order_status": "PENDING_CONFIRMATION",
        "pending_order": {
            "operation_id": "new-operation", "product_name": "Bosch X100",
            "quantity": 1, "status": "PENDING_CONFIRMATION",
        },
        "created_order_id": "old-order", "last_created_order_id": "old-order",
    }
    update = asyncio.run(create_order_confirmation_node(finalizer)(state))
    assert update["order_status"] == expected_status
    assert update["created_order_id"] is None
    assert "last_created_order_id" not in update
    assert {**state, **update}["last_created_order_id"] == "old-order"


def test_status_node_propagates_repository_error_without_changing_pending_state():
    use_case = SimpleNamespace(execute=AsyncMock(side_effect=RuntimeError("offline")))
    state = {
        "order_id": "order-123", "pending_order": {"status": "PENDING_CONFIRMATION"},
        "order_status": "PENDING_CONFIRMATION", "last_created_order_id": "old-order",
    }
    original = deepcopy(state)
    with pytest.raises(RuntimeError, match="offline"):
        asyncio.run(create_order_status_node(use_case)(state))
    assert state == original
