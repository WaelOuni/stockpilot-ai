from pymongo.collation import Collation
from pymongo.errors import OperationFailure

from stockpilot.application.use_cases.adjust_product_stock import (
    StockMovementIndexNotReadyError,
)


OPERATION_INDEX_NAME = "stock_movements_operation_id_unique"
HISTORY_INDEX_NAME = "stock_movements_product_history"


def has_unique_operation_index(collection) -> bool:
    try:
        indexes = list(collection.list_indexes())
    except OperationFailure as error:
        if error.code == 26:
            return False
        raise
    for index in indexes:
        if (
            list(index["key"].items()) == [("operation_id", 1)]
            and index.get("unique") is True
            and not index.get("sparse")
            and "partialFilterExpression" not in index
            and "expireAfterSeconds" not in index
            and index.get("collation", {"locale": "simple"}).get("locale") == "simple"
            and index.get("collation", {"locale": "simple"}).get("strength") in (None, 3)
        ):
            return True
    return False


def require_unique_operation_index(collection) -> None:
    if not has_unique_operation_index(collection):
        raise StockMovementIndexNotReadyError("Stock operation index is not provisioned.")


def require_stock_movement_indexes(collection) -> None:
    require_unique_operation_index(collection)


def preflight_stock_movements(collection) -> tuple[int, int]:
    seen = set()
    invalid = duplicates = 0
    for document in collection.find({}, {"operation_id": 1}):
        operation_id = document.get("operation_id")
        if not isinstance(operation_id, str) or not operation_id.strip():
            invalid += 1
        elif operation_id in seen:
            duplicates += 1
        else:
            seen.add(operation_id)
    return invalid, duplicates


def prepare_stock_movement_indexes(collection) -> None:
    invalid, duplicates = preflight_stock_movements(collection)
    if invalid or duplicates:
        raise ValueError(
            f"Stock movement index preparation blocked: invalid={invalid}, duplicates={duplicates}."
        )
    if not has_unique_operation_index(collection):
        collection.create_index(
            [("operation_id", 1)], name=OPERATION_INDEX_NAME, unique=True,
            collation=Collation("simple"),
        )
    collection.create_index(
        [("product_sku", 1), ("occurred_at", -1), ("movement_id", -1)],
        name=HISTORY_INDEX_NAME,
        collation=Collation("simple"),
    )
    require_unique_operation_index(collection)
