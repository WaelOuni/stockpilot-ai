import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from stockpilot.application.use_cases.release_stock import ReleaseStockUseCase
from stockpilot.application.stock_identity import StockIdentityRequiredError


def test_standalone_release_without_history_requires_manual_intervention() -> None:
    repository = SimpleNamespace(release_stock=AsyncMock(return_value=True))
    with pytest.raises(StockIdentityRequiredError, match="intervention manuelle"):
        asyncio.run(ReleaseStockUseCase(repository).execute(" Bosch X100 ", 3))
    repository.release_stock.assert_not_awaited()


@pytest.mark.parametrize("quantity", [0, -1, True, 1.5])
def test_standalone_release_rejects_invalid_quantity(quantity):
    repository = SimpleNamespace(release_stock=AsyncMock())
    with pytest.raises(ValueError):
        asyncio.run(ReleaseStockUseCase(repository).execute("Bosch X100", quantity))
    repository.release_stock.assert_not_awaited()
