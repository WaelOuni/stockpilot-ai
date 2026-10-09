import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from stockpilot.application.use_cases.release_stock import ReleaseStockUseCase


def test_standalone_release_delegates_exact_quantity() -> None:
    repository = SimpleNamespace(release_stock=AsyncMock(return_value=True))
    assert asyncio.run(ReleaseStockUseCase(repository).execute(" Bosch X100 ", 3))
    repository.release_stock.assert_awaited_once_with(
        product_name="Bosch X100", quantity=3
    )


@pytest.mark.parametrize("quantity", [0, -1, True, 1.5])
def test_standalone_release_rejects_invalid_quantity(quantity):
    repository = SimpleNamespace(release_stock=AsyncMock())
    with pytest.raises(ValueError):
        asyncio.run(ReleaseStockUseCase(repository).execute("Bosch X100", quantity))
    repository.release_stock.assert_not_awaited()
