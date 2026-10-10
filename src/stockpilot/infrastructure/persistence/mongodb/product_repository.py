import re

from pymongo import ReturnDocument

from stockpilot.application.dto.stock_reservation_result import (
    StockReservationResult,
)
from stockpilot.application.ports.product_repository import ProductRepositoryPort
from stockpilot.domain.entities.product import Product
from stockpilot.domain.enums.product_category import ProductCategory


class MongoProductRepository(ProductRepositoryPort):
    def __init__(self, database):
        self.collection = database["products"]

    @staticmethod
    def _name_filter(name: str) -> dict:
        return {
            "name": {
                "$regex": name,
                "$options": "i",
            }
        }

    async def find_by_name(self, name: str) -> Product | None:
        document = self.collection.find_one(self._name_filter(name))

        if document is None:
            return None

        return self._to_product(document)

    @staticmethod
    def _to_product(document) -> Product:
        return Product(
            sku=document["sku"],
            name=document["name"],
            category=ProductCategory(document["category"]),
            price=float(document["price"]),
            stock=int(document["stock"]),
        )

    async def list_products(
        self, limit: int, offset: int, *, q: str | None = None,
        category: ProductCategory | None = None, available: bool | None = None,
    ) -> list[Product]:
        query = {}
        if q is not None and q.strip():
            literal = {"$regex": re.escape(q.strip()), "$options": "i"}
            query["$or"] = [{"sku": literal}, {"name": literal}]
        if category is not None:
            query["category"] = category.value
        if available is not None:
            query["stock"] = {"$gt" if available else "$lte": 0}
        cursor = self.collection.find(query).sort([("sku", 1), ("_id", 1)]).skip(offset).limit(limit)
        return [self._to_product(document) for document in cursor]

    async def find_by_sku(self, sku: str) -> Product | None:
        documents = list(self.collection.find({"sku": sku}).limit(2))
        if len(documents) > 1:
            raise ValueError("Duplicate product SKU; catalog lookup is ambiguous.")
        return self._to_product(documents[0]) if documents else None

    async def reserve_stock(
        self,
        product_name: str,
        quantity: int,
    ) -> StockReservationResult:
        return self.reserve_stock_sync(product_name, quantity)

    def reserve_stock_sync(
        self,
        product_name: str,
        quantity: int,
        session=None,
    ) -> StockReservationResult:
        session_options = {"session": session} if session is not None else {}
        document = self.collection.find_one_and_update(
            {
                **self._name_filter(product_name),
                "stock": {"$gte": quantity},
            },
            {"$inc": {"stock": -quantity}},
            projection={"stock": 1},
            return_document=ReturnDocument.AFTER,
            **session_options,
        )

        if document is None:
            return StockReservationResult(success=False)

        return StockReservationResult(
            success=True,
            remaining_stock=int(document["stock"]),
        )

    async def release_stock(
        self,
        product_name: str,
        quantity: int,
    ) -> bool:
        result = self.collection.update_one(
            self._name_filter(product_name),
            {"$inc": {"stock": quantity}},
        )
        return result.matched_count == 1