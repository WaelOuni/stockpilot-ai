import asyncio
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pymongo.errors import PyMongoError

from stockpilot.application.dto.order_status_result import OrderStatusResult
from stockpilot.application.use_cases.get_order_status import GetOrderStatusUseCase
from stockpilot.domain.entities.order import Order
from stockpilot.domain.enums.order_status import OrderStatus


def make_order():
    return Order(
        order_id="order-123", operation_id="op-123", product_name="Bosch X100",
        quantity=2, status=OrderStatus.CREATED,
        created_at=datetime(2026, 10, 9, tzinfo=timezone.utc),
    )


def repository(result=None, error=None):
    return SimpleNamespace(
        find_by_order_id=AsyncMock(return_value=result, side_effect=error),
        find_by_operation_id=AsyncMock(), create=AsyncMock(),
    )


def assert_read_only(repo):
    repo.create.assert_not_awaited()
    repo.find_by_operation_id.assert_not_awaited()


def test_found_status_comes_from_persisted_order_and_is_immutable():
    order = make_order()
    repo = repository(order)
    result = asyncio.run(GetOrderStatusUseCase(repo).execute(order.order_id))
    assert result == OrderStatusResult(
        order_id=order.order_id, status=OrderStatus.CREATED, result="FOUND",
        response="Commande order-123 : statut CREATED.",
    )
    repo.find_by_order_id.assert_awaited_once_with(order.order_id)
    assert_read_only(repo)
    with pytest.raises(FrozenInstanceError):
        result.status = None


def test_no_associated_order_does_not_read_or_write_repository():
    repo = repository()
    result = asyncio.run(GetOrderStatusUseCase(repo).execute(None))
    assert result == OrderStatusResult(
        order_id=None, status=None, result="NO_ORDER_ASSOCIATED",
        response="Aucune commande n'est associée à cette conversation.",
    )
    repo.find_by_order_id.assert_not_awaited()
    assert_read_only(repo)


def test_missing_order_keeps_exact_requested_identifier():
    repo = repository()
    result = asyncio.run(GetOrderStatusUseCase(repo).execute("unknown-ORDER-id"))
    assert result == OrderStatusResult(
        order_id="unknown-ORDER-id", status=None, result="NOT_FOUND",
        response="Commande unknown-ORDER-id introuvable (NOT_FOUND).",
    )
    repo.find_by_order_id.assert_awaited_once_with("unknown-ORDER-id")
    assert_read_only(repo)


@pytest.mark.parametrize("error", [RuntimeError("offline"), PyMongoError("read failed")])
def test_repository_errors_are_not_converted_to_not_found(error):
    repo = repository(error=error)
    with pytest.raises(type(error), match=str(error)) as caught:
        asyncio.run(GetOrderStatusUseCase(repo).execute("order-123"))
    assert caught.value is error
    repo.find_by_order_id.assert_awaited_once_with("order-123")
    assert_read_only(repo)
