import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from stockpilot.application.use_cases.finalize_order import FinalizeOrderUseCase
from stockpilot.domain.enums.order_status import OrderStatus


def test_finalize_passes_saved_operation_and_quantity_to_transaction() -> None:
    transaction = SimpleNamespace(execute=AsyncMock(side_effect=lambda order: order))
    result = asyncio.run(
        FinalizeOrderUseCase(transaction).execute(
            " Bosch X100 ", 3, operation_id="saved-operation"
        )
    )
    UUID(result.order_id)
    assert result.operation_id == "saved-operation"
    assert result.quantity == 3
    assert result.product_name == "Bosch X100"
    assert result.status == OrderStatus.CREATED
    transaction.execute.assert_awaited_once_with(result)


@pytest.mark.parametrize(
    "product,quantity,operation",
    [("", 3, "op"), ("Bosch", 3, ""), ("Bosch", 0, "op"),
     ("Bosch", -1, "op"), ("Bosch", True, "op"), ("Bosch", 1.5, "op")],
)
def test_finalize_rejects_invalid_input_without_transaction(product, quantity, operation):
    transaction = SimpleNamespace(execute=AsyncMock())
    with pytest.raises(ValueError):
        asyncio.run(FinalizeOrderUseCase(transaction).execute(product, quantity, operation))
    transaction.execute.assert_not_awaited()
