"""Insert-only initialization of the explicitly isolated synthetic demo database."""

from collections import Counter
from typing import Any
from uuid import UUID

from bson import BSON
from bson.codec_options import CodecOptions
from pymongo import MongoClient, ReadPreference
from pymongo.collation import Collation
from pymongo.errors import PyMongoError
from pymongo.read_concern import ReadConcern
from pymongo.uri_parser import parse_uri
from pymongo.write_concern import WriteConcern

from stockpilot.demo_dataset import DEMO_DATABASE, PLANNED_INDEXES, DemoDataset


COLLECTIONS = ("products", "orders", "stock_movements", "demo_manifests")


def validate_demo_target(uri: str, database: str) -> None:
    if database != DEMO_DATABASE:
        raise ValueError(f"The only permitted demo target is {DEMO_DATABASE}.")
    try:
        parsed = parse_uri(uri)
    except (PyMongoError, ValueError):
        raise ValueError("Mongo URI is invalid; URI details are withheld.") from None
    if parsed.get("database") not in (None, DEMO_DATABASE):
        raise ValueError("Mongo URI database does not match the explicit demo target.")
    options = parsed.get("options", {})
    replica_set = options.get("replicaSet", options.get("replicaset"))
    if replica_set != "rs0":
        raise ValueError("Mongo URI must explicitly declare replicaSet=rs0.")


def guarded_database(client: MongoClient, database_name: str):
    """Check the actual connected primary before any collection/index/data write."""
    if database_name != DEMO_DATABASE:
        raise ValueError("The connected database must be stockpilot_demo.")
    hello = client.admin.command("hello")
    if hello.get("setName") != "rs0" or hello.get("isWritablePrimary") is not True:
        raise ValueError("The connected server must be a writable rs0 primary.")
    if not hello.get("logicalSessionTimeoutMinutes") or hello.get("maxWireVersion", 0) < 7:
        raise ValueError("The connected primary must support MongoDB transactions.")
    database = client.get_database(
        database_name, codec_options=CodecOptions(tz_aware=True),
        read_preference=ReadPreference.PRIMARY,
        read_concern=ReadConcern("majority"), write_concern=WriteConcern("majority"),
    )
    if database.name != DEMO_DATABASE:
        raise ValueError("The effective database is not stockpilot_demo.")
    return database


def _expected_indexes(collection: str) -> dict[str, dict[str, Any]]:
    expected = {"_id_": {"key": [("_id", 1)], "unique": False, "partial": None}}
    for name, keys, unique, partial in PLANNED_INDEXES:
        if name.startswith(f"{collection}_"):
            expected[name] = {"key": list(keys), "unique": unique, "partial": partial}
    return expected


def validate_existing_schema(database, *, complete: bool = False) -> None:
    for info in database.list_collections():
        name = info["name"]
        if name not in COLLECTIONS or info.get("type") != "collection" or info.get("options"):
            raise ValueError("Existing demo collections/options are incompatible; no cleanup allowed.")
        expected = _expected_indexes(name)
        actual = list(database[name].list_indexes())
        for index in actual:
            definition = expected.get(index["name"])
            if (
                definition is None
                or list(index["key"].items()) != definition["key"]
                or bool(index.get("unique", False)) != definition["unique"]
                or index.get("partialFilterExpression") != definition["partial"]
                or index.get("collation", {}).get("locale", "simple") != "simple"
                or any(key in index for key in ("sparse", "expireAfterSeconds", "hidden"))
            ):
                raise ValueError("Existing demo indexes are incompatible; no index changes allowed.")
        if complete and {index["name"] for index in actual} != set(expected):
            raise ValueError("Completed demo dataset is missing approved indexes.")
    if complete and set(database.list_collection_names()) != set(COLLECTIONS):
        raise ValueError("Completed demo dataset is missing approved collections.")


def expected_documents(dataset: DemoDataset) -> dict[str, tuple[dict, ...]]:
    return {
        "products": dataset.products,
        "orders": dataset.orders,
        "stock_movements": dataset.stock_movements,
        "demo_manifests": (dataset.manifest,),
    }


def _test_run_manifests(manifests: list[dict], dataset: DemoDataset) -> dict[str, dict]:
    manifests = [
        manifest for manifest in manifests
        if manifest.get("demo_test_kind") == "run_manifest"
    ]
    by_run_id = {}
    for manifest in manifests:
        run_id = manifest.get("demo_test_run_id")
        try:
            parsed = UUID(run_id)
        except (ValueError, TypeError, AttributeError):
            raise ValueError("Demo test manifest has an invalid run identity.") from None
        if str(parsed) != run_id or parsed.version != 4:
            raise ValueError("Demo test manifest has an invalid run identity.")
        expected_id = f"stockpilot-demo-test-run-{parsed.hex}"
        if manifest.get("_id") != expected_id or run_id in by_run_id:
            raise ValueError("Demo test manifest identity is inconsistent.")
        if (
            manifest.get("baseline_dataset_id") != dataset.dataset_id
            or manifest.get("baseline_sha256") != dataset.manifest["sha256"]
        ):
            raise ValueError("Demo test manifest does not identify the immutable baseline.")
        if manifest.get("status") not in {"started", "completed", "failed"}:
            raise ValueError("Demo test manifest status is invalid.")
        for field in ("product_skus", "order_ids", "operation_ids"):
            values = manifest.get(field)
            if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
                raise ValueError("Demo test manifest resource inventory is invalid.")
            if len(values) != len(set(values)):
                raise ValueError("Demo test manifest resource inventory is duplicated.")
        prefix = f"DEMO-TEST-{parsed.hex}-"
        if any(not sku.startswith(prefix) for sku in manifest["product_skus"]):
            raise ValueError("Demo test manifest contains an unscoped product SKU.")
        for field in ("order_ids", "operation_ids"):
            for value in manifest[field]:
                try:
                    identity = UUID(value)
                except (ValueError, TypeError, AttributeError):
                    raise ValueError("Demo test manifest contains an invalid operation identity.") from None
                if str(identity) != value or identity.version != 4:
                    raise ValueError("Demo test manifest contains an invalid operation identity.")
        by_run_id[run_id] = manifest
    return by_run_id


def _validate_demo_test_documents(
    extras: dict[str, list[dict]], dataset: DemoDataset,
) -> dict[str, int]:
    manifests = _test_run_manifests(extras["demo_manifests"], dataset)
    if not any(extras.values()):
        return {
            "runs": 0, "runs_by_status": {},
            "products": 0, "orders": 0, "stock_movements": 0,
        }

    products_by_run_sku = {}
    counts = {
        "runs": len(manifests),
        "runs_by_status": dict(Counter(
            manifest["status"] for manifest in manifests.values()
        )),
        "products": 0,
        "orders": 0,
        "stock_movements": 0,
    }
    for product in extras["products"]:
        run_id = product.get("demo_test_run_id")
        manifest = manifests.get(run_id)
        sku = product.get("sku")
        if (
            manifest is None or product.get("demo_test_kind") != "product"
            or sku not in manifest["product_skus"]
            or sku in products_by_run_sku
            or type(product.get("stock")) is not int or product["stock"] < 0
        ):
            raise ValueError("Unowned or invalid demo test product found; no writes allowed.")
        products_by_run_sku[(run_id, sku)] = product
        counts["products"] += 1

    for order in extras["orders"]:
        owners = [
            (run_id, manifest) for run_id, manifest in manifests.items()
            if order.get("order_id") in manifest["order_ids"]
            and order.get("operation_id") in manifest["operation_ids"]
        ]
        if len(owners) != 1:
            raise ValueError("Unowned or invalid demo test order found; no writes allowed.")
        run_id, manifest = owners[0]
        if order.get("demo_test_run_id") not in (None, run_id):
            raise ValueError("Demo test order ownership marker conflicts with its manifest.")
        product = next((
            candidate for (owner, _), candidate in products_by_run_sku.items()
            if owner == run_id and candidate.get("name") == order.get("product_name")
        ), None)
        if (
            manifest is None
            or order.get("order_id") not in manifest["order_ids"]
            or order.get("operation_id") not in manifest["operation_ids"]
            or product is None
            or order.get("reserved_product_id") != product["_id"]
        ):
            raise ValueError("Unowned or invalid demo test order found; no writes allowed.")
        counts["orders"] += 1

    for movement in extras["stock_movements"]:
        owners = [
            (run_id, manifest) for run_id, manifest in manifests.items()
            if movement.get("operation_id") in manifest["operation_ids"]
            and movement.get("product_sku") in manifest["product_skus"]
        ]
        if len(owners) != 1:
            raise ValueError("Unowned or invalid demo test stock movement found; no writes allowed.")
        run_id, manifest = owners[0]
        if movement.get("demo_test_run_id") not in (None, run_id):
            raise ValueError("Demo test movement ownership marker conflicts with its manifest.")
        product = products_by_run_sku.get((run_id, movement.get("product_sku")))
        if (
            manifest is None
            or movement.get("operation_id") not in manifest["operation_ids"]
            or product is None
            or movement.get("product_id") != product["_id"]
            or type(movement.get("delta")) is not int
            or movement.get("stock_before") + movement["delta"] != movement.get("stock_after")
            or movement.get("stock_after", -1) < 0
        ):
            raise ValueError("Unowned or invalid demo test stock movement found; no writes allowed.")
        counts["stock_movements"] += 1

    allowed_manifest_ids = {manifest["_id"] for manifest in manifests.values()}
    if any(document.get("_id") not in allowed_manifest_ids for document in extras["demo_manifests"]):
        raise ValueError("Unowned demo manifest found; no writes allowed.")
    for run_id, manifest in manifests.items():
        product_skus = {sku for owner, sku in products_by_run_sku if owner == run_id}
        if product_skus != set(manifest["product_skus"]):
            raise ValueError("Demo test manifest product ownership is incomplete.")
    return counts


def demo_content_summary(database, dataset: DemoDataset, *, session=None) -> dict[str, Any]:
    """Verify the immutable reference fixture and report separately owned test documents."""
    expected = expected_documents(dataset)
    extras = {}
    for name, documents in expected.items():
        options = {"session": session} if session is not None else {}
        actual = list(database[name].find({}, **options))
        if name == "demo_manifests":
            baseline = [document for document in actual if document.get("_id") == dataset.dataset_id]
        else:
            baseline = [
                document for document in actual
                if document.get("demo_dataset_id") == dataset.dataset_id
            ]
        expected_by_id = {document["_id"]: _bson_normalized(document) for document in documents}
        observed_by_id = {document["_id"]: document for document in baseline}
        if observed_by_id != expected_by_id or len(baseline) != len(documents):
            raise ValueError(
                "Existing demo contents are partial/incompatible: immutable reference dataset mismatch."
            )
        baseline_ids = {document["_id"] for document in baseline}
        extras[name] = [document for document in actual if document["_id"] not in baseline_ids]

    test_counts = _validate_demo_test_documents(extras, dataset)
    return {
        "reference": {
            "dataset_id": dataset.dataset_id,
            "sha256": dataset.manifest["sha256"],
            "products": len(dataset.products),
            "orders": len(dataset.orders),
            "stock_movements": len(dataset.stock_movements),
            "manifests": 1,
        },
        "test_fixtures": test_counts,
        "total_documents": {
            name: len(expected[name]) + len(extras[name])
            for name in expected
        },
    }


def _bson_normalized(document: dict) -> dict:
    # BSON stores millisecond timestamps; compare decoded database values exactly.
    return BSON.encode(document).decode(codec_options=CodecOptions(tz_aware=True))


def verify_content(database, dataset: DemoDataset, *, session=None) -> str:
    """Accept empty or exact reference content plus separately manifested owned test docs."""
    actual = {
        name: list(database[name].find({}, session=session))
        for name in expected_documents(dataset)
    }
    if not any(actual.values()):
        return "empty"
    demo_content_summary(database, dataset, session=session)
    return "complete"


def initialize_demo(client, database, dataset: DemoDataset) -> str:
    """Create exactly the approved schema, then commit all 5,501 documents atomically."""
    if database.name != DEMO_DATABASE or database.client is not client:
        raise ValueError("Initialization requires stockpilot_demo on the guarded client.")
    guarded_database(client, database.name)
    validate_existing_schema(database)
    state = verify_content(database, dataset)
    if state == "complete":
        validate_existing_schema(database, complete=True)
        return "replayed"

    existing = set(database.list_collection_names())
    for name in COLLECTIONS:
        if name not in existing:
            database.create_collection(name)
    for name, keys, unique, partial in PLANNED_INDEXES:
        collection = next(collection for collection in COLLECTIONS if name.startswith(f"{collection}_"))
        options: dict[str, Any] = {"name": name}
        if unique:
            options["unique"] = True
        if partial is not None:
            options["partialFilterExpression"] = partial
        if collection == "products":
            options["collation"] = Collation("simple")
        database[collection].create_index(list(keys), **options)
    validate_existing_schema(database, complete=True)

    def insert(session):
        if verify_content(database, dataset, session=session) == "complete":
            return "replayed"
        for name, documents in expected_documents(dataset).items():
            for offset in range(0, len(documents), 500):
                database[name].insert_many(list(documents[offset:offset + 500]), session=session)
        return "inserted"

    with client.start_session() as session:
        outcome = session.with_transaction(
            insert, read_concern=ReadConcern("snapshot"),
            write_concern=WriteConcern("majority"), read_preference=ReadPreference.PRIMARY,
            max_commit_time_ms=10000,
        )
    if verify_content(database, dataset) != "complete":
        raise ValueError("Post-commit verification did not find the complete dataset.")
    return outcome
