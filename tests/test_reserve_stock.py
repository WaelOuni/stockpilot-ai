import asyncio
from unittest.mock import MagicMock

import pytest
from pymongo import ReturnDocument

from stockpilot.application.dto.stock_reservation_result import (
    StockReservationResult,
)
from stockpilot.application.use_cases.reserve_stock import ReserveStockUseCase
from stockpilot.infrastructure.persistence.mongodb import product_repository


class FakeAtomicProductRepository:
    def __init__(self, stock: int) -> None:
        self.stock = stock
        self.calls: list[tuple[str, int]] = []

    async def reserve_stock(
        self,
        product_name: str,
        quantity: int,
    ) -> StockReservationResult:
        self.calls.append((product_name, quantity))
        if self.stock < quantity:
            return StockReservationResult(success=False)
        self.stock -= quantity
        return StockReservationResult(
            success=True,
            remaining_stock=self.stock,
        )


def test_reserve_stock_succeeds_and_returns_remaining_stock() -> None:
    repository = FakeAtomicProductRepository(stock=12)
    use_case = ReserveStockUseCase(repository)

    result = asyncio.run(use_case.execute(" Bosch X100 ", quantity=1))

    assert result.success
    assert result.remaining_stock == 11
    assert repository.stock == 11
    assert repository.calls == [("Bosch X100", 1)]


def test_reserve_stock_refuses_when_quantity_exceeds_stock_without_change() -> None:
    repository = FakeAtomicProductRepository(stock=2)
    use_case = ReserveStockUseCase(repository)

    result = asyncio.run(use_case.execute("Bosch X100", quantity=3))

    assert not result.success
    assert result.remaining_stock is None
    assert repository.stock == 2


def test_mongo_reservation_uses_atomic_conditional_decrement() -> None:
    collection = MagicMock()
    collection.find.return_value.limit.return_value = [{"_id": "product-1"}]
    collection.find_one_and_update.return_value = {"_id": "product-1", "stock": 11}
    database = MagicMock()
    database.__getitem__.return_value = collection
    repository = product_repository.MongoProductRepository(database)

    result = asyncio.run(repository.reserve_stock("Bosch X100", quantity=1))

    assert result == StockReservationResult(success=True, remaining_stock=11)
    collection.find.assert_called_once_with(
        {"name": {"$regex": r"\ABosch\ X100\z", "$options": "i"}},
        {"_id": 1, "name": 1},
    )
    collection.find.return_value.limit.assert_called_once_with(2)
    collection.find_one_and_update.assert_called_once_with(
        {
            "_id": "product-1",
            "stock": {"$gte": 1},
        },
        {"$inc": {"stock": -1}},
        projection={"_id": 1, "stock": 1},
        return_document=ReturnDocument.AFTER,
    )


def test_mongo_reservation_returns_failure_when_atomic_match_fails() -> None:
    collection = MagicMock()
    collection.find.return_value.limit.return_value = [{"_id": "product-1"}]
    collection.find_one_and_update.return_value = None
    database = MagicMock()
    database.__getitem__.return_value = collection
    repository = product_repository.MongoProductRepository(database)

    result = asyncio.run(repository.reserve_stock("Bosch X100", quantity=3))

    assert result == StockReservationResult(success=False)
    collection.find_one_and_update.assert_called_once()


@pytest.mark.parametrize(
    ("products", "error"),
    [
        ([], None),
        ([{"_id": "product-1"}, {"_id": "product-2"}], RuntimeError),
        ([{}], RuntimeError),
    ],
)
def test_mongo_reservation_requires_one_product_with_an_id(products, error) -> None:
    collection = MagicMock()
    collection.find.return_value.limit.return_value = products
    database = MagicMock()
    database.__getitem__.return_value = collection
    repository = product_repository.MongoProductRepository(database)

    if error is None:
        assert asyncio.run(repository.reserve_stock("Bosch X100", quantity=1)) == (
            StockReservationResult(success=False)
        )
    else:
        with pytest.raises(error):
            asyncio.run(repository.reserve_stock("Bosch X100", quantity=1))
    collection.find_one_and_update.assert_not_called()


def test_mongo_release_increments_stock_for_matching_product() -> None:
    collection = MagicMock()
    collection.find.return_value.limit.return_value = [{"_id": "product-1"}]
    collection.update_one.return_value.matched_count = 1
    database = MagicMock()
    database.__getitem__.return_value = collection
    repository = product_repository.MongoProductRepository(database)

    result = asyncio.run(repository.release_stock("Bosch X100", quantity=3))

    assert result is True
    collection.find.assert_called_once_with(
        {"name": {"$regex": r"\ABosch\ X100\z", "$options": "i"}},
        {"_id": 1, "name": 1},
    )
    collection.find.return_value.limit.assert_called_once_with(2)
    collection.update_one.assert_called_once_with(
        {"_id": "product-1"},
        {"$inc": {"stock": 3}},
    )


def test_mongo_release_reports_missing_product() -> None:
    collection = MagicMock()
    collection.find.return_value.limit.return_value = []
    database = MagicMock()
    database.__getitem__.return_value = collection
    repository = product_repository.MongoProductRepository(database)

    result = asyncio.run(repository.release_stock("Unknown", quantity=3))

    assert result is False
    collection.update_one.assert_not_called()
