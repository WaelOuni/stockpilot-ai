"""Explicit SKU index preparation; never invoked by application startup."""

from pymongo.collation import Collation

from stockpilot.application.use_cases.manage_products import ProductSkuIndexNotReadyError


SKU_INDEX_NAME = "products_sku_unique"


def require_unique_sku_index(collection) -> None:
    for index in collection.list_indexes():
        if (
            list(index["key"].items()) == [("sku", 1)]
            and index.get("unique") is True
            and not index.get("sparse")
            and "partialFilterExpression" not in index
            and "expireAfterSeconds" not in index
            and index.get("collation", {"locale": "simple"}).get("locale") == "simple"
        ):
            return
    raise ProductSkuIndexNotReadyError("Exact unique SKU index is not provisioned.")


def preflight_skus(collection) -> tuple[int, int]:
    """Count unsafe documents and duplicate exact strings without exposing data."""
    seen = set()
    invalid = duplicates = 0
    for document in collection.find({}, {"sku": 1}):
        sku = document.get("sku")
        if not isinstance(sku, str) or not sku.strip():
            invalid += 1
        elif sku in seen:
            duplicates += 1
        else:
            seen.add(sku)
    return invalid, duplicates


def prepare_unique_sku_index(collection) -> None:
    invalid, duplicates = preflight_skus(collection)
    if invalid or duplicates:
        raise ValueError(f"SKU preparation blocked: invalid={invalid}, duplicates={duplicates}.")
    try:
        require_unique_sku_index(collection)
        return
    except ProductSkuIndexNotReadyError:
        pass
    # Mongo, not preflight, arbitrates racing inserts/builds. Never drop/merge data
    # or replace an incompatible existing index to make the build succeed.
    collection.create_index(
        [("sku", 1)], name=SKU_INDEX_NAME, unique=True, collation=Collation("simple"),
    )
    require_unique_sku_index(collection)
