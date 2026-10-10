from copy import deepcopy
from dataclasses import asdict
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from pymongo.errors import DuplicateKeyError, OperationFailure

from test_admin_sessions import api, login, ORIGIN
from customer_auth import customer_headers
from stockpilot.main import app
from stockpilot.application.dto.product_csv_import import CsvImportResult
from stockpilot.application.use_cases.product_csv_import import (
    PreviewProductCsvImportUseCase, ExecuteProductCsvImportUseCase,
    CsvImportConflict, CsvImportUnknown, CsvImportIndexNotReady, GetProductCsvImportResultUseCase,
)
from stockpilot.infrastructure.persistence.mongodb.product_import import (
    MongoProductCsvImport, require_import_index, prepare_import_index,
)


CSV = b"sku,name,category,price\nNEW-1,Drill,DRILL,12.5\nNEW-2,Saw,SAW,8\n"
SCOPE = "test-session"


class Repository:
    def __init__(self):
        self.products = {}
        self.receipts = {}
        self.reads = []
        self.result_reads = []
        self.writes = 0

    def existing_skus(self, skus):
        self.reads.append(skus)
        return set(skus) & self.products.keys()

    def result(self, operation):
        self.result_reads.append(operation)
        return self.receipts.get(operation)

    def execute(self, operation, fingerprint, products):
        if any(p.sku in self.products for p in products):
            raise CsvImportConflict()
        self.writes += 1
        self.products.update({p.sku: p for p in products})
        value = CsvImportResult(operation, fingerprint, len(products), [p.sku for p in products])
        self.receipts[operation] = value
        return value


@pytest.fixture
def use_cases():
    repository = Repository()
    preview = PreviewProductCsvImportUseCase(repository)
    return repository, preview, ExecuteProductCsvImportUseCase(preview)


@pytest.mark.parametrize("content", [
    CSV, b"\xef\xbb\xbf" + CSV,
    b'sku;name;category;price\r\nNEW-1;"Drill; ""quoted""";DRILL;12.5\r\n',
    b'sku,name,category,price\nNEW-1,"Drill, quoted",DRILL,12.5\n',
    b'name,price,sku,category\nDrill,12.5,NEW-1,DRILL\n',
    b'sku,name,category,price\nNEW-1,"Drill\nmultiline",DRILL,12.5\n',
    b'"sku";"name";"category";"price"\nNEW-1;"Drill; quoted";DRILL;12.5\n',
    b'"sku","name","category","price"\nNEW-1,"Drill, quoted",DRILL,12.5\n',
])
def test_valid_quote_aware_utf8_preview_no_writes(use_cases, content):
    repository, preview, _ = use_cases
    result = preview.execute(content, SCOPE)
    assert result.valid > 0 and result.invalid == 0 and result.binding
    assert len(repository.reads) == 1 and repository.writes == 0
    assert all(product.stock == 0 for product in result.products)


@pytest.mark.parametrize("content", [
    b"", b"\xef\xbb\xbf", b"\xff", b"sku,name,category,price\n",
    b"sku,name,category\nNEW,Drill,DRILL\n",
    b"sku,name,category,price,stock\nNEW,Drill,DRILL,1,4\n",
    b"sku,name,name,price\nNEW,Drill,DRILL,1\n",
    b"sku,name,category,price\n\n",
    b"sku,name,category,price\nNEW,Drill,DRILL\n",
    b"sku,name,category,price\nNEW,Drill,DRILL,1,extra\n",
    b'sku,name,category,price\nNEW,"unterminated,DRILL,1\n',
    b'sku,name,category,price\nNEW,"closed"x,DRILL,1\n',
    b'sku,name,category,price\nNEW,bare"quote,DRILL,1\n',
])
def test_invalid_files_never_approved(use_cases, content):
    repository, preview, _ = use_cases
    result = preview.execute(content, SCOPE)
    assert result.invalid and not result.binding and result.issues
    assert not repository.writes


@pytest.mark.parametrize("row", [
    ",Drill,DRILL,1", "   ,Drill,DRILL,1", "NEW,,DRILL,1", "NEW,   ,DRILL,1",
    "NEW,Drill,drill,1", "NEW,Drill,INVALID,1", "NEW,Drill,DRILL,abc",
    "NEW,Drill,DRILL,-1", "NEW,Drill,DRILL,NaN", "NEW,Drill,DRILL,inf",
    "NEW,Drill,DRILL,-inf", "NEW,Drill,DRILL,1e999",
])
def test_invalid_metadata(use_cases, row):
    assert not use_cases[1].execute(("sku,name,category,price\n" + row).encode(), SCOPE).binding


def test_internal_duplicates_all_lines_and_exact_case(use_cases):
    repository, preview, _ = use_cases
    result = preview.execute(b"sku,name,category,price\nSKU,A,DRILL,1\nSKU,B,SAW,2\nsku,C,OTHER,3", SCOPE)
    assert result.invalid == 2 and result.valid == 1
    assert [issue.line for issue in result.issues] == [2, 3]
    assert repository.reads == [["SKU", "sku"]]


def test_existing_conflicts_single_group_read(use_cases):
    repository, preview, _ = use_cases
    repository.products["NEW-1"] = object()
    result = preview.execute(CSV, SCOPE)
    assert result.existing_conflicts == 1 and result.valid == 1
    assert result.issues[0].line == 2 and not result.binding


@pytest.mark.parametrize("category", ["DRILL", "SAW", "SANDER", "SCREWDRIVER", "OTHER"])
def test_actual_domain_categories(use_cases, category):
    result = use_cases[1].execute(f"sku,name,category,price\nSKU,Name,{category},0".encode(), SCOPE)
    assert result.binding and result.products[0].category.value == category


def test_default_500_rows_limit_and_duplicate_existing_overlap(use_cases):
    repository, preview, _ = use_cases
    rows = b"sku,name,category,price\n" + b"".join(
        f"SKU-{i},Name,OTHER,1\n".encode() for i in range(500)
    )
    result = preview.execute(rows, SCOPE)
    assert result.valid == 500 and result.binding
    rejected = preview.execute(rows + b"OVER,Name,OTHER,1\n", SCOPE)
    assert rejected.analysed == 501 and rejected.invalid and not rejected.binding
    repository.products["SKU"] = object()
    repeated = preview.execute(b"sku,name,category,price\nSKU,A,DRILL,1\nSKU,B,DRILL,1", SCOPE)
    assert repeated.invalid == 2 and repeated.existing_conflicts == 2
    assert len(repeated.issues) == 4


def test_limits_and_bounded_errors(use_cases):
    repository, _, _ = use_cases
    preview = PreviewProductCsvImportUseCase(repository, max_bytes=80, max_products=2, max_field=8, max_issues=1)
    with pytest.raises(ValueError):
        preview.execute(b"x" * 81, SCOPE)
    result = preview.execute(b"sku,name,category,price\nA,,DRILL,1\nB,,DRILL,1\nC,,DRILL,1", SCOPE)
    assert result.issues_truncated and len(result.issues) == 1 and not result.binding
    result = preview.execute(b"sku,name,category,price\nA,Long name,DRILL,1", SCOPE)
    assert result.invalid


def test_atomic_stock_zero_replay_and_different_payload(use_cases):
    repository, preview, execute = use_cases
    before = deepcopy(repository.products)
    approved = preview.execute(CSV, SCOPE)
    assert repository.products == before
    operation = str(uuid4())
    result = execute.execute(CSV, SCOPE, operation, approved.binding, True)
    assert result.created == 2 and repository.writes == 1
    assert all(p.stock == 0 for p in repository.products.values())
    assert execute.execute(CSV, SCOPE, operation, approved.binding, True) == result
    assert repository.writes == 1
    preview.clock = lambda: 10**12
    assert execute.execute(CSV, SCOPE, operation, approved.binding, True) == result
    preview.clock = lambda: 0
    other = CSV.replace(b"12.5", b"13.5")
    # Sign a valid alternate payload with the same issuer, despite the existing records.
    expiry = approved.binding.split(".")[0]
    import hashlib
    binding = expiry + "." + preview.signature(hashlib.sha256(other).hexdigest(), SCOPE, expiry)
    with pytest.raises(CsvImportConflict):
        execute.execute(other, SCOPE, operation, binding, True)


@pytest.mark.parametrize("change", ["file", "scope", "confirmation", "operation", "expired"])
def test_execute_requires_server_bound_explicit_approval(use_cases, change):
    repository, preview, execute = use_cases
    approved = preview.execute(CSV, SCOPE)
    if change == "expired":
        preview.clock = lambda: 10**12
    with pytest.raises(ValueError):
        execute.execute(CSV + b" " if change == "file" else CSV,
                        "other" if change == "scope" else SCOPE,
                        "bad" if change == "operation" else str(uuid4()),
                        approved.binding, change != "confirmation")
    assert repository.writes == 0


def test_catalogue_race_refuses_whole_batch(use_cases):
    repository, preview, execute = use_cases
    approved = preview.execute(CSV, SCOPE)
    repository.products["NEW-2"] = object()
    with pytest.raises(CsvImportConflict):
        execute.execute(CSV, SCOPE, str(uuid4()), approved.binding, True)
    assert "NEW-1" not in repository.products and repository.writes == 0


@pytest.fixture
def import_api(api, monkeypatch, use_cases):
    repository, preview, execute = use_cases
    monkeypatch.setattr(app.state, "preview_product_csv_import", preview)
    monkeypatch.setattr(app.state, "execute_product_csv_import", execute)
    monkeypatch.setattr(app.state, "get_product_csv_import_result", GetProductCsvImportResultUseCase(repository))
    client, state, clock, _ = api
    return client, repository, state, clock


def headers(client):
    csrf = login(client).json()["csrf_token"]
    return {"Origin": ORIGIN, "X-Admin-CSRF": csrf}


def test_api_preview_execute_result_and_typed_contract(import_api):
    client, repository, _, _ = import_api
    auth = headers(client)
    response = client.post("/backoffice/products/import/preview", content=CSV, headers=auth)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    approved = response.json()
    assert approved["products"][0]["stock"] == 0 and repository.writes == 0
    operation = str(uuid4())
    execute_headers = {**auth, "X-Import-Operation-Id": operation,
                       "X-Import-Preview": approved["binding"], "X-Import-Confirm": "CREATE_ONLY"}
    created = client.post("/backoffice/products/import/execute", content=CSV, headers=execute_headers)
    assert created.status_code == 200
    assert created.json() == client.get(f"/backoffice/products/import/batches/{operation}").json()
    assert client.post("/backoffice/products/import/execute", content=CSV, headers=execute_headers).json() == created.json()
    assert repository.writes == 1
    assert "_id" not in created.json()


@pytest.mark.parametrize("kind", ["public", "jwt", "technical", "origin", "csrf", "duplicate_origin", "expired"])
@pytest.mark.parametrize("route", ["preview", "execute"])
def test_api_auth_before_repository(import_api, kind, route):
    client, repository, _, clock = import_api
    auth = headers(client) if kind != "public" else {}
    if kind == "jwt":
        auth.update(customer_headers("customer"))
    if kind == "technical":
        auth["Authorization"] = "Bearer technical-only-secret"
    if kind == "origin":
        auth["Origin"] = "https://evil.example.test"
    if kind == "csrf":
        auth["X-Admin-CSRF"] = "wrong"
    if kind == "duplicate_origin":
        auth = list(auth.items()) + [("Origin", ORIGIN)]
    if kind == "expired":
        clock[0] += 1801
    response = client.post(f"/backoffice/products/import/{route}", content=CSV, headers=auth)
    assert response.status_code in (401, 403)
    assert not repository.reads and not repository.writes


def test_api_size_stream_check_and_unknown_get(import_api):
    client, repository, _, _ = import_api
    auth = headers(client)
    app.state.preview_product_csv_import.max_bytes = 32
    response = client.post("/backoffice/products/import/preview", content=CSV, headers=auth)
    assert response.status_code == 413 and not repository.reads
    assert client.get(f"/backoffice/products/import/batches/{uuid4()}").status_code == 404
    assert client.get("/backoffice/products/import/batches/bad").status_code == 422


def test_api_bounded_stream_ignores_mime_and_rejects_duplicate_confirmation(import_api):
    client, repository, _, _ = import_api
    auth = headers(client)
    response = client.post("/backoffice/products/import/preview", content=CSV,
                           headers={**auth, "Content-Type": "application/octet-stream"})
    assert response.status_code == 200
    metadata = {**auth, "X-Import-Operation-Id": str(uuid4()),
                "X-Import-Preview": response.json()["binding"], "X-Import-Confirm": "CREATE_ONLY"}
    response = client.post("/backoffice/products/import/execute", content=CSV,
                           headers=list(metadata.items()) + [("X-Import-Confirm", "CREATE_ONLY")])
    assert response.status_code == 422 and repository.writes == 0
    app.state.preview_product_csv_import.max_bytes = 32
    response = client.post("/backoffice/products/import/preview", content=iter([CSV[:30], CSV[30:]]),
                           headers={**auth, "Content-Length": "1"})
    assert response.status_code == 413


@pytest.mark.parametrize("kind", ["public", "jwt", "technical", "revoked"])
def test_result_get_cookie_only_auth_before_mongo(import_api, kind):
    client, repository, state, _ = import_api
    auth = {}
    if kind != "public":
        headers(client)
    if kind == "jwt":
        auth = customer_headers("customer")
    if kind == "technical":
        auth = {"Authorization": "Bearer technical-only-secret"}
    if kind == "revoked":
        state.sessions.clear()
    assert client.get(f"/backoffice/products/import/batches/{uuid4()}", headers=auth).status_code == 401
    assert repository.result_reads == []


@pytest.mark.parametrize("index", [
    {"key": {"operation_id": 1}, "unique": False},
    {"key": {"operation_id": 1}, "unique": True, "sparse": True},
    {"key": {"operation_id": 1}, "unique": True, "partialFilterExpression": {}},
    {"key": {"operation_id": 1}, "unique": True, "expireAfterSeconds": 100},
    {"key": {"operation_id": 1}, "unique": True, "collation": {"locale": "en"}},
    {"key": {"operation_id": 1, "fingerprint": 1}, "unique": True},
])
def test_receipt_index_fail_closed(index):
    collection = MagicMock()
    collection.list_indexes.return_value = [index]
    with pytest.raises(CsvImportIndexNotReady):
        require_import_index(collection)
    collection.create_index.assert_not_called()


def test_absent_collection_index_and_explicit_provision():
    collection = MagicMock()
    collection.list_indexes.side_effect = OperationFailure("namespace", 26)
    with pytest.raises(CsvImportIndexNotReady):
        require_import_index(collection)
    collection.list_indexes.side_effect = None
    collection.list_indexes.return_value = [{"key": {"operation_id": 1}, "unique": True}]
    collection.find.return_value = []
    prepare_import_index(collection)
    collection.create_index.assert_not_called()


def mongo_fake():
    database = MagicMock()
    products = MagicMock()
    receipts = MagicMock()
    database.__getitem__.side_effect = lambda name: products if name == "products" else receipts
    receipts.with_options.return_value = receipts
    products.list_indexes.return_value = [{"key": {"sku": 1}, "unique": True}]
    receipts.list_indexes.return_value = [{"key": {"operation_id": 1}, "unique": True}]
    products.find.return_value = []
    receipts.find_one.return_value = None
    session = database.client.start_session.return_value
    session.in_transaction = True
    return MongoProductCsvImport(database), products, receipts, session


@pytest.mark.parametrize("committed", [True, False])
def test_unknown_commit_reconciles_without_reapply(committed):
    adapter, products, receipts, session = mongo_fake()
    repository = Repository()
    preview = PreviewProductCsvImportUseCase(repository).execute(CSV, SCOPE)
    operation = str(uuid4())
    value = CsvImportResult(operation, preview.fingerprint, 2, ["NEW-1", "NEW-2"])
    error = OperationFailure("private", details={"errorLabels": ["UnknownTransactionCommitResult", "TransientTransactionError"]})
    session.commit_transaction.side_effect = error
    receipts.find_one.side_effect = [None, asdict(value) if committed else None]
    if committed:
        assert adapter.execute(operation, preview.fingerprint, preview.products) == value
    else:
        with pytest.raises(CsvImportUnknown):
            adapter.execute(operation, preview.fingerprint, preview.products)
    assert products.insert_many.call_count == 1
    session.abort_transaction.assert_not_called()


def test_duplicate_sku_rolls_back_known_failure():
    adapter, products, _, session = mongo_fake()
    preview = PreviewProductCsvImportUseCase(Repository()).execute(CSV, SCOPE)
    products.insert_many.side_effect = DuplicateKeyError("private")
    with pytest.raises(CsvImportConflict):
        adapter.execute(str(uuid4()), preview.fingerprint, preview.products)
    session.abort_transaction.assert_called_once()
    session.commit_transaction.assert_not_called()


def test_only_known_abort_bounded_retry():
    adapter, products, _, session = mongo_fake()
    preview = PreviewProductCsvImportUseCase(Repository()).execute(CSV, SCOPE)
    products.insert_many.side_effect = [
        OperationFailure("private", details={"errorLabels": ["TransientTransactionError"]}), None,
    ]
    value = adapter.execute(str(uuid4()), preview.fingerprint, preview.products)
    assert value.created == 2 and products.insert_many.call_count == 2
    session.abort_transaction.assert_called_once()


def test_absent_product_collection_is_prerequisite_failure_before_transaction():
    adapter, products, _, session = mongo_fake()
    from stockpilot.application.use_cases.manage_products import ProductSkuIndexNotReadyError
    products.list_indexes.side_effect = OperationFailure("namespace", 26)
    with pytest.raises(ProductSkuIndexNotReadyError):
        adapter.execute(str(uuid4()), "a" * 64, [])
    products.insert_many.assert_not_called()
    session.start_transaction.assert_not_called()


@pytest.mark.parametrize("uri,database,confirmation,apply", [
    ("mongodb://localhost/stockpilot", "stockpilot_e2e", None, False),
    ("mongodb://localhost", " stockpilot", None, False),
    ("mongodb://localhost", "", None, False),
    ("mongodb://localhost", "stockpilot", None, True),
    ("mongodb://localhost", "stockpilot", "stockpilot_e2e", True),
])
def test_import_index_cli_refuses_unsafe_target_before_connect(uri, database, confirmation, apply):
    from scripts.prepare_product_import_index import validate_target
    with pytest.raises(ValueError):
        validate_target(uri, database, confirmation, apply)


def test_import_index_cli_dryrun_does_not_provision(monkeypatch):
    from scripts import prepare_product_import_index as script
    client = MagicMock()
    client.__enter__.return_value = client
    database = client.__getitem__.return_value
    database.name = "stockpilot"
    monkeypatch.setattr(script, "MongoClient", MagicMock(return_value=client))
    monkeypatch.setattr(script, "preflight_imports", lambda collection: (0, 0))
    monkeypatch.setattr(script, "require_import_index", MagicMock(side_effect=CsvImportIndexNotReady()))
    provision = MagicMock()
    monkeypatch.setattr(script, "prepare_import_index", provision)
    assert script.main(["--database", "stockpilot"]) == 0
    provision.assert_not_called()
    assert script.main(["--database", "stockpilot", "--apply"]) == 1
    provision.assert_not_called()
