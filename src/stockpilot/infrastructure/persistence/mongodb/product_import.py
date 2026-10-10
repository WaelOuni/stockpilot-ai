from dataclasses import asdict

from pymongo import ReadPreference
from pymongo.collation import Collation
from pymongo.errors import BulkWriteError, ConnectionFailure, DuplicateKeyError, OperationFailure, PyMongoError
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

from stockpilot.application.dto.product_csv_import import CsvImportResult
from stockpilot.application.use_cases.manage_products import ProductSkuIndexNotReadyError
from stockpilot.application.use_cases.product_csv_import import (
    CsvImportConflict, CsvImportIndexNotReady, CsvImportUnknown,
)
from stockpilot.infrastructure.persistence.mongodb.product_indexes import require_unique_sku_index


def require_import_index(collection):
    try:
        for index in collection.list_indexes():
            if (list(index["key"].items()) == [("operation_id", 1)]
                and index.get("unique") is True and not index.get("sparse")
                and "partialFilterExpression" not in index and "expireAfterSeconds" not in index
                and index.get("collation", {"locale": "simple"}).get("locale") == "simple"):
                return
    except OperationFailure as error:
        if error.code != 26:
            raise
    raise CsvImportIndexNotReady("Import operation index not provisioned.")


def preflight_imports(collection):
    seen = set()
    invalid = duplicates = 0
    from stockpilot.application.use_cases.product_csv_import import validate_operation
    for document in collection.find({}, {"operation_id": 1}):
        operation = document.get("operation_id")
        try:
            validate_operation(operation)
        except (ValueError, TypeError):
            invalid += 1
            continue
        if operation in seen:
            duplicates += 1
        seen.add(operation)
    return invalid, duplicates


def prepare_import_index(collection):
    invalid, duplicates = preflight_imports(collection)
    if invalid or duplicates:
        raise ValueError("Unsafe import operation data.")
    try:
        require_import_index(collection)
        return
    except CsvImportIndexNotReady:
        pass
    collection.create_index([("operation_id", 1)], unique=True,
                            name="product_imports_operation_id_unique", collation=Collation("simple"))
    require_import_index(collection)


class MongoProductCsvImport:
    def __init__(self, database):
        self.client = database.client
        self.products = database["products"]
        self.receipts = database["product_imports"].with_options(
            read_preference=ReadPreference.PRIMARY, read_concern=ReadConcern("majority"),
        )

    def existing_skus(self, skus, session=None):
        return {item["sku"] for item in self.products.find(
            {"sku": {"$in": skus}}, {"sku": 1, "_id": 0},
            collation=Collation("simple"), session=session,
        )}

    def result(self, operation_id):
        document = self.receipts.find_one({"operation_id": operation_id}, collation=Collation("simple"))
        return self._result(document) if document else None

    @staticmethod
    def _result(document):
        return CsvImportResult(document["operation_id"], document["fingerprint"],
                               document["created"], document["skus"])

    def reconcile(self, operation_id, fingerprint):
        result = self.result(operation_id)
        if result and result.fingerprint != fingerprint:
            raise CsvImportConflict("Operation belongs to a different file.")
        return result

    def execute(self, operation_id, fingerprint, products):
        try:
            require_unique_sku_index(self.products)
        except OperationFailure as error:
            if error.code != 26:
                raise
            raise ProductSkuIndexNotReadyError("Product SKU index not provisioned.") from error
        require_import_index(self.receipts)
        result = CsvImportResult(operation_id, fingerprint, len(products), [p.sku for p in products])
        for attempt in range(3):
            session = self.client.start_session()
            commit_started = False
            try:
                session.start_transaction(read_concern=ReadConcern("snapshot"),
                                          write_concern=WriteConcern("majority"),
                                          read_preference=ReadPreference.PRIMARY)
                previous = self.receipts.find_one({"operation_id": operation_id},
                                                 collation=Collation("simple"), session=session)
                if previous:
                    if previous["fingerprint"] != fingerprint:
                        raise CsvImportConflict("Operation belongs to a different file.")
                    result = self._result(previous)
                else:
                    if self.existing_skus(result.skus, session):
                        raise CsvImportConflict("SKU exists; entire batch refused.")
                    self.products.insert_many([
                        {"sku": p.sku, "name": p.name, "category": p.category.value,
                         "price": p.price, "stock": 0} for p in products
                    ], session=session)
                    self.receipts.insert_one(asdict(result), session=session)
                commit_started = True
                session.commit_transaction()
                return result
            except CsvImportConflict:
                if session.in_transaction:
                    try:
                        session.abort_transaction()
                    except PyMongoError:
                        pass
                raise
            except PyMongoError as error:
                unknown = error.has_error_label("UnknownTransactionCommitResult")
                transient = error.has_error_label("TransientTransactionError")
                if session.in_transaction and not commit_started:
                    try:
                        session.abort_transaction()
                    except PyMongoError:
                        pass
                if transient and not unknown and attempt < 2:
                    continue
                try:
                    replay = self.reconcile(operation_id, fingerprint)
                except PyMongoError as reconciliation_error:
                    raise CsvImportUnknown("Unable to reconcile import.") from reconciliation_error
                if replay:
                    return replay
                if unknown or (commit_started and not transient) or isinstance(error, ConnectionFailure):
                    raise CsvImportUnknown("Import outcome unknown; do not resubmit.") from error
                if isinstance(error, DuplicateKeyError) or (
                    isinstance(error, BulkWriteError)
                    and any(item.get("code") == 11000 for item in error.details.get("writeErrors", []))
                ):
                    raise CsvImportConflict("Concurrent SKU conflict; entire batch rolled back.") from error
                raise
            finally:
                session.end_session()
        raise CsvImportUnknown("Import outcome unavailable.")
