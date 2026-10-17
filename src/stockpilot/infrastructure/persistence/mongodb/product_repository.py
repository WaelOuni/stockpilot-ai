from stockpilot.application.stock_identity import StockIdentityRequiredError
from stockpilot.infrastructure.persistence.mongodb.pagination import read_page
from stockpilot.application.dto.advice_criteria import AdviceCriteria
import re
from bson import ObjectId
from datetime import datetime, timezone

from pymongo import ReadPreference, ReturnDocument
from pymongo.read_concern import ReadConcern
from pymongo.errors import DuplicateKeyError
from pymongo.collation import Collation

from stockpilot.application.dto.backoffice_dashboard import (
    DashboardRecentStockMovement,
    ProductDashboardStats,
)
from stockpilot.application.dto.product_page import ProductPage
from stockpilot.application.dto.stock_movement_page import StockMovementPage
from stockpilot.application.dto.stock_reservation_result import (
    StockReservationResult,
)
from stockpilot.application.ports.product_repository import ProductRepositoryPort
from stockpilot.domain.entities.product import Product
from stockpilot.domain.entities.stock_movement import StockMovement
from stockpilot.domain.enums.product_category import ProductCategory
from stockpilot.application.use_cases.manage_products import (
    DuplicateProductSkuError, validate_metadata,
)
from stockpilot.infrastructure.persistence.mongodb.product_indexes import require_unique_sku_index


def _dashboard_integer(value, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"Stored dashboard field {field} must be an integer.")
    return int(value)


class MongoProductRepository(ProductRepositoryPort):
    def __init__(self, database):
        self.database = database
        self.collection = database["products"]

    async def get_dashboard_product_stats(self) -> ProductDashboardStats:
        pipeline = [{
            "$group": {
                "_id": None,
                "total_products": {"$sum": 1},
                "available_products": {
                    "$sum": {"$cond": [{"$gt": ["$stock", 0]}, 1, 0]},
                },
                "unavailable_products": {
                    "$sum": {"$cond": [{"$lte": ["$stock", 0]}, 1, 0]},
                },
                "total_stock_units": {"$sum": "$stock"},
            },
        }]
        result = next(iter(self.collection.aggregate(pipeline)), {})
        return ProductDashboardStats(
            total_products=_dashboard_integer(result.get("total_products", 0), "product count"),
            available_products=_dashboard_integer(result.get("available_products", 0), "available product count"),
            unavailable_products=_dashboard_integer(
                result.get("unavailable_products", 0), "unavailable product count",
            ),
            total_stock_units=_dashboard_integer(result.get("total_stock_units", 0), "stock units"),
        )

    async def list_recent_dashboard_stock_movements(
        self,
    ) -> list[DashboardRecentStockMovement]:
        projection = {
            "_id": 0,
            "product_sku": 1,
            "delta": 1,
            "occurred_at": 1,
            "reason": 1,
        }
        cursor = (
            self.database["stock_movements"]
            .find({}, projection)
            .sort([("occurred_at", -1), ("movement_id", -1)])
            .limit(5)
        )
        recent = []
        for document in cursor:
            occurred_at = document["occurred_at"]
            if occurred_at.tzinfo is None:
                occurred_at = occurred_at.replace(tzinfo=timezone.utc)
            recent.append(DashboardRecentStockMovement(
                product_sku=document["product_sku"],
                delta=_dashboard_integer(document["delta"], "stock movement delta"),
                occurred_at=occurred_at,
                reason=document["reason"],
            ))
        return recent

    async def create_product(self, product: Product) -> Product:
        require_unique_sku_index(self.collection)
        validate_metadata({"name": product.name, "category": product.category, "price": product.price})
        if not isinstance(product.sku, str) or not product.sku.strip():
            raise ValueError("SKU must not be blank.")
        document = {
            "sku": product.sku, "name": product.name, "category": product.category.value,
            "price": product.price, "stock": 0,
        }
        try:
            result = self.collection.insert_one(document)
        except DuplicateKeyError as error:
            raise DuplicateProductSkuError("Product SKU already exists.") from error
        persisted = self.collection.find_one({"_id": result.inserted_id})
        if persisted is None:
            raise RuntimeError("Persisted product unavailable.")
        return self._to_product(persisted)

    async def update_metadata(self, sku: str, changes: dict) -> Product | None:
        require_unique_sku_index(self.collection)
        validate_metadata(changes)
        values = {key: value.value if key == "category" else value for key, value in changes.items()}
        document = self.collection.find_one_and_update(
            {"sku": sku}, {"$set": values}, return_document=ReturnDocument.AFTER,
            collation=Collation("simple"),
        )
        return self._to_product(document) if document is not None else None

    @staticmethod
    def _name_filter(name: str) -> dict:
        return {
            "name": {
                "$regex": re.escape(name).replace(r"\ ", " "),
                "$options": "i",
            }
        }

    async def find_by_name(self, name: str) -> Product | None:
        from stockpilot.application.use_cases.resolve_product import ResolveProductUseCase
        return (await ResolveProductUseCase(self).execute(name)).product

    async def find_resolution_skus(self, sku: str) -> list[Product]:
        query = {"sku": {"$regex": r"\A" + re.escape(sku) + r"\z", "$options": "i"}}
        return [self._to_product(doc) for doc in self.collection.find(query).limit(2)]

    async def find_resolution_candidates(self, name: str, *, exact: bool, limit: int) -> list[Product]:
        from stockpilot.application.use_cases.resolve_product import normalize_name, CANDIDATE_LIMIT
        if not 1 <= limit <= CANDIDATE_LIMIT:
            raise ValueError("Invalid candidate limit.")
        accents = {
            "a": "[aàáâãäå]", "c": "[cç]", "e": "[eèéêë]",
            "i": "[iìíîï]", "n": "[nñ]", "o": "[oòóôõö]", "u": "[uùúûü]", "y": "[yýÿ]",
        }
        def literal(value):
            return "".join(accents.get(c, re.escape(c)) for c in value)
        normalized = normalize_name(name)
        if not normalized:
            return []
        if exact:
            pattern = r"\A\s*" + r"\s+".join(literal(word) for word in normalized.split()) + r"\s*\z"
            query = {"name": {"$regex": pattern, "$options": "i"}}
        else:
            tokens = normalized.split()
            if not tokens:
                return []
            query = {"$and": [{"name": {"$regex": r"(?<!\w)" + literal(token) + r"(?!\w)",
                                      "$options": "i"}} for token in dict.fromkeys(tokens)]}
        return [self._to_product(doc) for doc in self.collection.find(query).limit(limit)]

    @staticmethod
    def _to_product(document) -> Product:
        return Product(
            sku=document["sku"],
            name=document["name"],
            category=ProductCategory(document["category"]),
            price=float(document["price"]),
            stock=int(document["stock"]),
            document_id=str(document["_id"]) if isinstance(document.get("_id"), ObjectId) else None,
        )

    async def list_products(
        self, limit: int, offset: int, *, q: str | None = None,
        category: ProductCategory | None = None, available: bool | None = None,
        include_total: bool = False,
    ) -> ProductPage:
        query = {}
        if q is not None and q.strip():
            literal = {"$regex": re.escape(q.strip()), "$options": "i"}
            query["$or"] = [{"sku": literal}, {"name": literal}]
        if category is not None:
            query["category"] = category.value
        if available is not None:
            query["stock"] = {"$gt" if available else "$lte": 0}
        if include_total:
            documents, total = read_page(self.collection, query, [("sku", 1), ("_id", 1)], limit, offset)
            return ProductPage([self._to_product(doc) for doc in documents], offset + len(documents) < total, total)
        cursor = self.collection.find(query).sort([("sku", 1), ("_id", 1)]).skip(offset).limit(limit + 1)
        documents = list(cursor)
        return ProductPage(
            items=[self._to_product(document) for document in documents[:limit]],
            has_more=len(documents) > limit,
        )

    async def find_advice_candidates(self, criteria: AdviceCriteria) -> list[Product]:
        query = {"price": {"$gte": 0}}
        if criteria.maximum is not None:
            query["price"]["$lt" if criteria.exclusive_maximum else "$lte"] = criteria.maximum
        if criteria.minimum is not None:
            query["price"]["$gte"] = criteria.minimum
        if criteria.category is not None:
            query["category"] = criteria.category.value
        if criteria.available is not None:
            query["stock"] = {"$gt" if criteria.available else "$lte": 0}
        if criteria.exclude_skus:
            query["sku"] = {"$nin": list(criteria.exclude_skus)}
        documents = self.collection.find(query).sort([
            ("price", 1), ("sku", 1), ("_id", 1),
        ]).limit(criteria.limit)
        products = [self._to_advice_product(document) for document in documents]
        if any(not criteria.matches(product) for product in products):
            raise ValueError("Invalid catalogue candidate; advice refused.")
        return products

    def _to_advice_product(self, document) -> Product:
        # Validate raw values before the legacy converter can coerce bools/fractions.
        if (isinstance(document["price"], bool)
                or not isinstance(document["price"], (int, float))
                or isinstance(document["stock"], bool)
                or not isinstance(document["stock"], int) or document["stock"] < 0):
            raise ValueError("Malformed catalogue facts; advice refused.")
        product = self._to_product(document)
        if not AdviceCriteria().matches(product):
            raise ValueError("Malformed catalogue facts; advice refused.")
        return product

    async def find_advice_by_sku(self, sku: str) -> Product | None:
        documents = list(self.collection.find({"sku": sku}, collation=Collation("simple")).limit(2))
        if len(documents) > 1:
            raise ValueError("Duplicate product SKU; advice refused.")
        return self._to_advice_product(documents[0]) if documents else None

    async def find_by_sku(self, sku: str) -> Product | None:
        documents = list(self.collection.find({"sku": sku}).limit(2))
        if len(documents) > 1:
            raise ValueError("Duplicate product SKU; catalog lookup is ambiguous.")
        return self._to_product(documents[0]) if documents else None

    async def adjust_stock(
        self, *, sku: str, delta: int, reason: str, operation_id: str,
        movement_id: str, occurred_at: datetime,
    ) -> StockMovement:
        from stockpilot.infrastructure.persistence.mongodb.stock_adjustment_transaction import (
            MongoStockAdjustmentTransaction,
        )

        return MongoStockAdjustmentTransaction(self.database).execute(
            sku=sku,
            delta=delta,
            reason=reason,
            operation_id=operation_id,
            movement_id=movement_id,
            occurred_at=occurred_at,
        )

    @staticmethod
    def _to_stock_movement(document) -> StockMovement:
        occurred_at = document["occurred_at"]
        if occurred_at.tzinfo is None:
            occurred_at = occurred_at.replace(tzinfo=timezone.utc)
        return StockMovement(
            movement_id=document["movement_id"],
            operation_id=document["operation_id"],
            product_sku=document["product_sku"],
            delta=int(document["delta"]),
            stock_before=int(document["stock_before"]),
            stock_after=int(document["stock_after"]),
            reason=document["reason"],
            occurred_at=occurred_at,
            source=document["source"],
        )

    async def find_stock_adjustment(self, operation_id: str) -> StockMovement | None:
        collection = self.database["stock_movements"].with_options(
            read_concern=ReadConcern("majority"), read_preference=ReadPreference.PRIMARY,
        )
        document = collection.find_one(
            {"operation_id": operation_id}, collation=Collation("simple"),
        )
        return self._to_stock_movement(document) if document is not None else None

    async def list_stock_movements(
        self, sku: str, limit: int, offset: int, *, include_total: bool = False,
    ) -> StockMovementPage:
        if include_total:
            documents, total = read_page(self.database["stock_movements"], {"product_sku": sku},
                [("occurred_at", -1), ("movement_id", -1), ("_id", -1)], limit, offset, {"_id": 0, "product_id": 0})
            return StockMovementPage([self._to_stock_movement(doc) for doc in documents], offset + len(documents) < total, total)
        documents = list(
            self.database["stock_movements"].find(
                {"product_sku": sku},
                {"_id": 0, "product_id": 0},
                collation=Collation("simple"),
            ).sort([("occurred_at", -1), ("movement_id", -1)]).skip(offset).limit(limit + 1)
        )
        return StockMovementPage(
            items=[self._to_stock_movement(document) for document in documents[:limit]],
            has_more=len(documents) > limit,
        )

    async def reserve_stock(
        self,
        product_name: str,
        quantity: int,
    ) -> StockReservationResult:
        return self.reserve_stock_sync(product_name, quantity)

    def resolve_stock_product(
        self, product_name: str, session=None, *, legacy: bool = False,
    ) -> dict | None:
        session_options = {"session": session} if session is not None else {}
        # Legacy reservations used an unanchored, unescaped regex. Any of its
        # candidates may have been decremented; an exact match alone is unsafe.
        if legacy and re.search(r"[.^$*+?{}\[\]\\|()]", product_name):
            raise RuntimeError("Identité produit legacy ambiguë avec métacaractères ; restitution refusée.")
        query = self._name_filter(product_name) if legacy else {
            "name": {"$regex": r"\A" + re.escape(product_name) + r"\z", "$options": "i"},
        }
        documents = list(self.collection.find(
            query,
            {"_id": 1, "name": 1},
            **session_options,
        ).limit(2))
        if len(documents) > 1:
            raise RuntimeError("Identité produit ambiguë ; aucune mutation de stock autorisée.")
        if not documents:
            return None
        if legacy and documents[0].get("name", "").casefold() != product_name.casefold():
            raise RuntimeError("Identité produit legacy non fiable ; restitution refusée.")
        if documents[0].get("_id") is None:
            raise RuntimeError("Identité du document produit indisponible.")
        return documents[0]

    def reserve_stock_sku(
        self, sku: str, quantity: int, session, *, document_id: str | None = None,
    ) -> dict | None:
        if not isinstance(document_id, str) or not ObjectId.is_valid(document_id):
            return None
        prepared_id = ObjectId(document_id)
        # Detect corrupt duplicate identities before any write in this snapshot.
        documents = list(self.collection.find({"sku": sku}, {"_id": 1}, session=session).limit(2))
        if len(documents) > 1:
            raise ValueError("Duplicate product SKU; reservation refused.")
        if not documents or documents[0]["_id"] != prepared_id:
            return None
        return self.collection.find_one_and_update(
            {"_id": prepared_id, "sku": sku, "stock": {"$gte": quantity}},
            {"$inc": {"stock": -quantity}}, projection={"_id": 1, "stock": 1},
            return_document=ReturnDocument.AFTER, session=session,
        )

    def reserve_stock_document(
        self, product_name: str, quantity: int, session=None,
    ) -> dict | None:
        product = self.resolve_stock_product(product_name, session)
        if product is None:
            return None
        session_options = {"session": session} if session is not None else {}
        return self.collection.find_one_and_update(
            {"_id": product["_id"], "stock": {"$gte": quantity}},
            {"$inc": {"stock": -quantity}},
            projection={"_id": 1, "stock": 1},
            return_document=ReturnDocument.AFTER,
            **session_options,
        )

    def reserve_stock_sync(
        self,
        product_name: str,
        quantity: int,
        session=None,
    ) -> StockReservationResult:
        document = self.reserve_stock_document(product_name, quantity, session)

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
        # A standalone name-based increment cannot prove reservation identity
        # or prevent a second credit. Use atomic persisted-order cancellation.
        raise StockIdentityRequiredError()
