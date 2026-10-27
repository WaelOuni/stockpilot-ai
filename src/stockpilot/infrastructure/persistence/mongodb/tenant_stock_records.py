"""Strict decoding for dedicated B3 records; no trust in persisted previews."""

import re
from datetime import datetime

from bson import ObjectId

from stockpilot.application.services.tenant_csv import RULES_VERSION, validate_values
from stockpilot.domain.entities.tenant_order import identifier
from stockpilot.domain.entities.tenant_stock_operation import (
    ImportIssue,
    ImportMode,
    OperationStatus,
    StockOperationResult,
    TenantImportPreview,
    TenantStockMovement,
)
from stockpilot.infrastructure.persistence.mongodb.tenant_catalogue import (
    _integer,
    _product,
)
from stockpilot.infrastructure.persistence.mongodb.tenant_unit_of_work import (
    fingerprint,
)


def batch_proof(row):
    return fingerprint(
        {
            k: row[k]
            for k in (
                "import_id",
                "organization_id",
                "actor_user_id",
                "session_binding",
                "mode",
                "file_fingerprint",
                "rules_version",
                "rows",
                "issues",
                "created_at",
                "expires_at",
            )
        }
    )


def batch(row, org):
    if row["organization_id"] != org or row["rules_version"] != RULES_VERSION:
        raise ValueError("Invalid persisted import scope/rules.")
    for key in ("import_id", "organization_id", "actor_user_id"):
        identifier(row[key])
    for key in ("file_fingerprint", "session_binding", "preview_digest"):
        if not isinstance(row[key], str) or not re.fullmatch("[0-9a-f]{64}", row[key]):
            raise ValueError("Invalid persisted fingerprint.")
    mode = ImportMode(row["mode"])
    if row["status"] not in ("invalid", "preview", "applied", "rejected"):
        raise ValueError("Invalid import lifecycle.")
    for key in ("created_at", "expires_at"):
        if not isinstance(row[key], datetime) or row[key].tzinfo is None:
            raise ValueError("Invalid import lifetime.")
    if row["expires_at"] <= row["created_at"] or row["preview_digest"] != batch_proof(
        row
    ):
        raise ValueError("Invalid persisted import proof.")
    if (
        not isinstance(row["rows"], list)
        or len(row["rows"]) > 100
        or not isinstance(row["issues"], list)
        or len(row["issues"]) > 50
    ):
        raise ValueError("Invalid import bounds.")
    issues = []
    for issue in row["issues"]:
        if (
            type(issue["line"]) is not int
            or issue["line"] < 0
            or not isinstance(issue["code"], str)
            or not isinstance(issue["message"], str)
        ):
            raise ValueError("Invalid import diagnostic.")
        issues.append(ImportIssue(**issue))
    if issues:
        if row["status"] not in ("invalid", "rejected") or row["rows"]:
            raise ValueError("Invalid preview.")
    elif row["status"] == "invalid" or not row["rows"]:
        raise ValueError("Invalid ready import.")
    if row["status"] in ("applied", "rejected"):
        identifier(row["operation_id"])
        if (
            not isinstance(row["finished_at"], datetime)
            or row["finished_at"].tzinfo is None
            or row["finished_at"] < row["created_at"]
        ):
            raise ValueError("Invalid terminal import.")
    elif row["operation_id"] is not None or row["finished_at"] is not None:
        raise ValueError("Invalid unconsumed import.")
    seen = set()
    for entry in row["rows"]:
        if (
            set(entry) != {"line", "values", "before"}
            or type(entry["line"]) is not int
            or entry["line"] < 2
        ):
            raise ValueError("Invalid import plan.")
        values = entry["values"]
        validate_values(values, mode)
        if values["sku"] in seen:
            raise ValueError("Duplicate persisted import SKU.")
        seen.add(values["sku"])
        before = entry["before"]
        if mode is ImportMode.CREATE_ONLY:
            if before is not None:
                raise ValueError("Creation cannot target existing stock.")
        elif not isinstance(before, dict) or _product(before, org).sku != values["sku"]:
            raise ValueError("Invalid import ownership.")
    return TenantImportPreview(
        row["import_id"],
        org,
        row["actor_user_id"],
        mode,
        row["status"],
        row["file_fingerprint"],
        row["preview_digest"],
        len(row["rows"]),
        tuple(issues),
        row["expires_at"],
    )


def operation(row, org):
    if not isinstance(row["fingerprint"], str) or not re.fullmatch(
        "[0-9a-f]{64}", row["fingerprint"]
    ):
        raise ValueError("Invalid operation fingerprint.")
    if row["organization_id"] != org or row["namespace"] not in ("adjust", "import", "archive"):
        raise ValueError("Invalid persisted operation.")
    for key in ("operation_id", "actor_user_id"):
        identifier(row[key])
    status = OperationStatus(row["status"])
    ids = row["movement_ids"]
    if not isinstance(ids, list) or len(ids) > 100 or len(set(ids)) != len(ids):
        raise ValueError("Invalid movement result.")
    for value in ids:
        identifier(value)
    if not isinstance(row["created_at"], datetime) or row["created_at"].tzinfo is None:
        raise ValueError("Invalid operation time.")
    if status is OperationStatus.PENDING:
        if row["finished_at"] is not None or ids or row["code"] != "pending":
            raise ValueError("Invalid pending outcome.")
    elif (
        not isinstance(row["finished_at"], datetime)
        or row["finished_at"].tzinfo is None
        or row["finished_at"] < row["created_at"]
    ):
        raise ValueError("Invalid terminal outcome.")
    if status is OperationStatus.REJECTED and ids:
        raise ValueError("Rejected operation cannot contain committed movements.")
    if status is OperationStatus.REJECTED and row["code"] not in {
        "unavailable_resource",
        "revision_conflict",
        "reserved_conflict",
        "preview_invalid",
        "consumed",
        "expired",
        "invalid_csv",
    }:
        raise ValueError("Invalid rejection outcome.")
    if status is OperationStatus.SUCCEEDED and row["code"] != "ok":
        raise ValueError("Invalid success outcome.")
    if (
        status is OperationStatus.SUCCEEDED
        and row["namespace"] == "adjust"
        and len(ids) != 1
    ):
        raise ValueError("Successful adjustment requires exactly one movement.")
    if row["namespace"] == "import":
        identifier(row["import_id"])
    elif row["import_id"] is not None:
        raise ValueError("Invalid operation namespace.")
    return StockOperationResult(
        row["operation_id"],
        row["namespace"],
        org,
        row["actor_user_id"],
        status,
        row["code"],
        tuple(ids),
        row["import_id"],
    )


def movement(row, org):
    if row["organization_id"] != org or not isinstance(row["product_id"], ObjectId):
        raise ValueError("Invalid movement scope.")
    for key in (
        "movement_id",
        "sku",
        "operation_id",
        "actor_user_id",
        "source_reference",
    ):
        identifier(row[key])
    if (row["kind"], row["operation_namespace"]) not in (
        ("ADJUSTMENT", "adjust"),
        ("IMPORT_ADJUSTMENT", "import"),
    ):
        raise ValueError("Invalid movement type.")
    facts = [
        _integer(row[k])
        for k in (
            "quantity_before",
            "quantity_after",
            "reserved_before",
            "reserved_after",
            "revision_after",
        )
    ]
    before, after, rb, ra, revision = facts
    if min(facts) < 0 or revision < 1 or rb != ra or rb > before or ra > after:
        raise ValueError("Invalid movement stock facts.")
    if (
        not isinstance(row["occurred_at"], datetime)
        or row["occurred_at"].tzinfo is None
    ):
        raise ValueError("Invalid movement timestamp.")
    return TenantStockMovement(
        row["movement_id"],
        org,
        str(row["product_id"]),
        row["sku"],
        row["operation_id"],
        row["actor_user_id"],
        row["kind"],
        row["operation_namespace"],
        *facts,
        row["reason"],
        row["source_reference"],
        row["occurred_at"],
    )
