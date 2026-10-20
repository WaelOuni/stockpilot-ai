"""Pure B3 bounds and CSV diagnostics; no claim of storage atomicity."""

from dataclasses import FrozenInstanceError

import pytest

from stockpilot.application.services.tenant_csv import (
    MAX_BYTES,
    parse_tenant_csv,
    validate_values,
)
from stockpilot.domain.entities.tenant_stock_operation import (
    AdjustTenantStock,
    ApplyTenantImport,
)
from stockpilot.domain.entities.tenant_stock_operation import (
    ImportMode as M,
)


@pytest.mark.parametrize("value", [-1, 1.1, True, None, "5", 1_000_000_001])
def test_physical_quantities_strict(value):
    with pytest.raises(ValueError):
        AdjustTenantStock("op", "product", value, 1, "physical inventory")


@pytest.mark.parametrize("value", [0, -1, 1.5, True, None])
def test_revision_strict(value):
    with pytest.raises(ValueError):
        AdjustTenantStock("op", "product", 5, value, "physical inventory")


@pytest.mark.parametrize("value", ["", " ", "x\ncommand", "x" * 501, None])
def test_reason_bounded_printable(value):
    with pytest.raises(ValueError):
        AdjustTenantStock("op", "product", 5, 1, value)


@pytest.mark.parametrize("value", [False, 1, "true", None])
def test_import_confirmation_strict(value):
    with pytest.raises(ValueError):
        ApplyTenantImport("op", "import", "digest", value)


@pytest.mark.parametrize(
    "mode,content",
    [
        (M.STOCK_ONLY, b"sku,quantity\nDRILL-001,8\n"),
        (M.STOCK_ONLY, b"\xef\xbb\xbfsku;quantity\nDRILL-001;8\n"),
        (
            M.METADATA_ONLY,
            b"sku,name,description,category,price_minor,currency\nSKU,Tool,Description,drill,4900,EUR\n",
        ),
        (
            M.CREATE_ONLY,
            b"sku,name,description,category,price_minor,currency,quantity\nSKU,Tool,Description,drill,4900,EUR,8\n",
        ),
    ],
)
def test_explicit_schemas_utf8_and_separator(mode, content):
    parsed = parse_tenant_csv(content, mode)
    assert not parsed.issues and len(parsed.rows) == 1
    validate_values(parsed.rows[0]["values"], mode)


@pytest.mark.parametrize(
    "content",
    [
        b"sku,quantity\nSKU,-1\n",
        b"sku,quantity\nSKU,1.5\n",
        b"sku,quantity\nSKU,1e2\n",
        b"sku,quantity\nSKU,+1\n",
        b"sku,quantity\nSKU,01\n",
        b"sku,quantity\nSKU,NaN\n",
        b"sku,quantity\nSKU,1000000001\n",
        b"sku,quantity\nSKU,8,extra\n",
        b"sku,quantity\nSKU,8\nSKU,9\n",
        b"sku,quantity\n\n",
        b"sku,quantity,role\nSKU,8,ORGANIZATION_ADMIN\n",
        b"sku,quantity,organization_id\nSKU,8,org-b\n",
        b"sku,quantity,quantity\nSKU,8,8\n",
        b"wrong\nSKU,8\n",
        b'sku,quantity\n"broken,8\n',
        b'sku,quantity\nSKU,8"\n',
        b"sku,quantity\n\xff,8\n",
        b"",
    ],
)
def test_invalid_csv_is_bounded_diagnostics_not_partial_plan(content):
    parsed = parse_tenant_csv(content, M.STOCK_ONLY)
    assert parsed.issues and parsed.rows == ()
    assert all(issue.message and "\xff" not in issue.message for issue in parsed.issues)


@pytest.mark.parametrize(
    "price", ["-1", "1.5", "NaN", "Infinity", "1e3", "9223372036854775808"]
)
def test_prices_are_integer_minor_units(price):
    content = f"sku,name,description,category,price_minor,currency\nSKU,Tool,Description,drill,{price},EUR\n".encode()
    assert parse_tenant_csv(content, M.METADATA_ONLY).issues


@pytest.mark.parametrize(
    "name,category,currency",
    [
        (" Tool", "drill", "EUR"),
        ("Tool ", "drill", "EUR"),
        ("Tool", " drill", "EUR"),
        ("Tool", "", "EUR"),
        ("Tool", "drill", "eur"),
    ],
)
def test_preview_metadata_validation_matches_domain(name, category, currency):
    content = f"sku,name,description,category,price_minor,currency\nSKU,{name},Description,{category},100,{currency}\n".encode()
    assert parse_tenant_csv(content, M.METADATA_ONLY).issues


def test_csv_bounds_no_raw_diagnostic_leak():
    with pytest.raises(ValueError):
        parse_tenant_csv(b"x" * (MAX_BYTES + 1), M.STOCK_ONLY)
    parsed = parse_tenant_csv(
        ("sku,quantity\n" + "".join(f"SKU-{i},1\n" for i in range(101))).encode(),
        M.STOCK_ONLY,
    )
    assert parsed.issues and not parsed.rows
    secret_value = "SENSITIVE-SYNTHETIC-ONLY"
    parsed = parse_tenant_csv(
        f"sku,quantity\nSKU,{secret_value}\n".encode(), M.STOCK_ONLY
    )
    assert secret_value not in repr(parsed.issues)
    parsed = parse_tenant_csv(
        ("sku,quantity\n" + "SKU,invalid\n" * 100).encode(), M.STOCK_ONLY
    )
    assert len(parsed.issues) <= 50


def test_cells_are_literal_not_mongo_queries_or_formulas():
    parsed = parse_tenant_csv(b"sku,quantity\n$ne,1\n=literal,2\n", M.STOCK_ONLY)
    assert not parsed.issues
    assert {r["values"]["sku"] for r in parsed.rows} == {"$ne", "=literal"}


def test_requests_immutable_no_reserved_or_role_override():
    command = AdjustTenantStock("op", "product", 5, 1, "physical inventory")
    with pytest.raises(FrozenInstanceError):
        command.quantity = 0
    assert not hasattr(command, "reserved") and not hasattr(command, "organization_id")


@pytest.mark.parametrize(
    "change",
    [
        {"fingerprint": "corrupt"},
        {"status": "pending", "code": "ok"},
        {"status": "succeeded", "code": "ok", "finished_at": "not-a-date"},
        {"status": "succeeded", "code": "ok", "movement_ids": []},
        {"status": "rejected", "code": "ok"},
    ],
)
def test_contradictory_durable_result_is_not_success(change):
    from datetime import UTC, datetime

    from stockpilot.infrastructure.persistence.mongodb.tenant_stock_records import (
        operation,
    )

    now = datetime.now(UTC)
    row = {
        "organization_id": "org",
        "namespace": "adjust",
        "operation_id": "op",
        "actor_user_id": "user",
        "fingerprint": "0" * 64,
        "status": "succeeded",
        "code": "ok",
        "movement_ids": ["movement"],
        "import_id": None,
        "created_at": now,
        "finished_at": now,
    }
    row.update(change)
    with pytest.raises(ValueError):
        operation(row, "org")
