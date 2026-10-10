from copy import deepcopy
from datetime import timezone

from pymongo import ReadPreference, ReturnDocument
from pymongo.collation import Collation
from pymongo.errors import ConnectionFailure, DuplicateKeyError, PyMongoError
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

from stockpilot.application.use_cases.adjust_product_stock import (
    InsufficientStockError,
    ProductStockNotFoundError,
    StockAdjustmentAmbiguousProductError,
    StockAdjustmentConflictError,
    StockAdjustmentOutcomeUnknownError,
    StockAdjustmentTransactionFailedError,
)
from stockpilot.domain.entities.stock_movement import StockMovement
from stockpilot.infrastructure.persistence.mongodb.stock_movement_indexes import (
    require_unique_operation_index,
)


MAX_TRANSACTION_ATTEMPTS = 3
_INT64_MAX = 9_223_372_036_854_775_807


class MongoStockAdjustmentTransaction:
    def __init__(self, database) -> None:
        self.client = database.client
        self.products = database["products"]
        self.movements = database["stock_movements"].with_options(
            read_concern=ReadConcern("majority"), read_preference=ReadPreference.PRIMARY,
        )

    @staticmethod
    def _movement(document: dict) -> StockMovement:
        occurred_at = document["occurred_at"]
        if occurred_at.tzinfo is None:
            occurred_at = occurred_at.replace(tzinfo=timezone.utc)
        return StockMovement(
            movement_id=document["movement_id"],
            operation_id=document["operation_id"],
            product_sku=document["product_sku"],
            delta=document["delta"],
            stock_before=document["stock_before"],
            stock_after=document["stock_after"],
            reason=document["reason"],
            occurred_at=occurred_at,
            source=document["source"],
        )

    @staticmethod
    def _same_payload(document: dict, sku: str, delta: int, reason: str) -> None:
        if (
            document.get("product_sku") != sku
            or document.get("delta") != delta
            or document.get("reason") != reason
        ):
            raise StockAdjustmentConflictError("Operation ID already belongs to a different adjustment.")

    def find_by_operation_id(self, operation_id: str, session=None) -> dict | None:
        options = {"session": session} if session is not None else {}
        return self.movements.find_one(
            {"operation_id": operation_id}, collation=Collation("simple"), **options,
        )

    def reconcile(
        self, operation_id: str, sku: str, delta: int, reason: str,
    ) -> StockMovement | None:
        document = self.find_by_operation_id(operation_id)
        if document is None:
            return None
        self._same_payload(document, sku, delta, reason)
        return self._movement(document)

    def execute(
        self, *, sku: str, delta: int, reason: str, operation_id: str,
        movement_id: str, occurred_at,
    ) -> StockMovement:
        require_unique_operation_index(self.movements)
        try:
            replay = self.reconcile(operation_id, sku, delta, reason)
        except PyMongoError as error:
            raise StockAdjustmentOutcomeUnknownError(
                "Unable to reconcile the stock adjustment."
            ) from error
        if replay is not None:
            return replay

        for attempt in range(MAX_TRANSACTION_ATTEMPTS):
            session = self.client.start_session()
            commit_started = False
            try:
                session.start_transaction(
                    read_concern=ReadConcern("snapshot"),
                    write_concern=WriteConcern("majority"),
                    read_preference=ReadPreference.PRIMARY,
                )
                replay_document = self.find_by_operation_id(operation_id, session)
                if replay_document is not None:
                    self._same_payload(replay_document, sku, delta, reason)
                    result = self._movement(replay_document)
                else:
                    candidates = list(
                        self.products.find(
                            {"sku": sku},
                            {"_id": 1},
                            session=session,
                            collation=Collation("simple"),
                        ).limit(2)
                    )
                    if len(candidates) > 1:
                        raise StockAdjustmentAmbiguousProductError(
                            "Product SKU is ambiguous."
                        )
                    if not candidates:
                        raise ProductStockNotFoundError("Product not found.")
                    product_id = candidates[0].get("_id")
                    if product_id is None:
                        raise StockAdjustmentTransactionFailedError(
                            "Product identity unavailable."
                        )
                    stock_filter = {
                        "$type": ["int", "long"],
                    }
                    if delta < 0:
                        stock_filter["$gte"] = -delta
                    else:
                        stock_filter["$gte"] = 0
                        stock_filter["$lte"] = _INT64_MAX - delta
                    updated = self.products.find_one_and_update(
                        {"_id": product_id, "stock": stock_filter},
                        {"$inc": {"stock": delta}},
                        return_document=ReturnDocument.AFTER,
                        session=session,
                    )
                    if updated is None:
                        exists = self.products.find_one(
                            {"_id": product_id}, {"_id": 1},
                            session=session,
                        )
                        if exists is None:
                            raise ProductStockNotFoundError("Product not found.")
                        current = self.products.find_one(
                            {"_id": product_id}, {"stock": 1},
                            session=session,
                        )
                        if current and isinstance(current.get("stock"), int) and delta < 0:
                            raise InsufficientStockError("Insufficient stock.")
                        raise StockAdjustmentTransactionFailedError(
                            "Product stock is not a supported integer value."
                        )
                    stock_after = updated["stock"]
                    stock_before = stock_after - delta
                    document = {
                        "movement_id": movement_id,
                        "operation_id": operation_id,
                        "product_sku": sku,
                        "product_id": deepcopy(product_id),
                        "delta": delta,
                        "stock_before": stock_before,
                        "stock_after": stock_after,
                        "reason": reason,
                        "occurred_at": occurred_at,
                        "source": "BACKOFFICE",
                    }
                    self.movements.insert_one(document, session=session)
                    result = self._movement(document)
                commit_started = True
                session.commit_transaction()
                return result
            except (
                InsufficientStockError,
                ProductStockNotFoundError,
                StockAdjustmentAmbiguousProductError,
                StockAdjustmentConflictError,
                StockAdjustmentTransactionFailedError,
            ):
                if session.in_transaction:
                    try:
                        session.abort_transaction()
                    except PyMongoError:
                        pass
                raise
            except DuplicateKeyError as error:
                if session.in_transaction:
                    try:
                        session.abort_transaction()
                    except PyMongoError:
                        pass
                try:
                    replay = self.reconcile(operation_id, sku, delta, reason)
                except StockAdjustmentConflictError:
                    raise
                except PyMongoError as reconcile_error:
                    raise StockAdjustmentOutcomeUnknownError(
                        "Unable to reconcile the stock adjustment."
                    ) from reconcile_error
                if replay is not None:
                    return replay
                raise StockAdjustmentOutcomeUnknownError(
                    "Unable to establish the stock adjustment outcome."
                ) from error
            except PyMongoError as error:
                if session.in_transaction and not commit_started:
                    try:
                        session.abort_transaction()
                    except PyMongoError:
                        pass
                unknown_commit = error.has_error_label("UnknownTransactionCommitResult")
                retryable_abort = error.has_error_label("TransientTransactionError")
                if retryable_abort and not unknown_commit and attempt + 1 < MAX_TRANSACTION_ATTEMPTS:
                    continue
                if unknown_commit or (not retryable_abort and (
                    commit_started or isinstance(error, ConnectionFailure)
                )):
                    try:
                        replay = self.reconcile(operation_id, sku, delta, reason)
                    except StockAdjustmentConflictError:
                        raise
                    except PyMongoError as reconcile_error:
                        raise StockAdjustmentOutcomeUnknownError(
                            "Unable to reconcile the stock adjustment."
                        ) from reconcile_error
                    if replay is not None:
                        return replay
                    raise StockAdjustmentOutcomeUnknownError(
                        "The stock adjustment outcome is unknown."
                    ) from error
                if retryable_abort:
                    try:
                        replay = self.reconcile(operation_id, sku, delta, reason)
                    except PyMongoError as reconcile_error:
                        raise StockAdjustmentOutcomeUnknownError(
                            "Unable to reconcile the stock adjustment."
                        ) from reconcile_error
                    if replay is not None:
                        return replay
                raise StockAdjustmentTransactionFailedError(
                    "Stock adjustment transaction failed."
                ) from error
            finally:
                session.end_session()
        raise StockAdjustmentTransactionFailedError("Stock adjustment retry limit reached.")
