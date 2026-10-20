"""Bounded untrusted CSV parser. No I/O, repositories or interpretation of cells."""

import csv
import hashlib
import io
import re
from dataclasses import dataclass

from stockpilot.application.csv_validation import validate_quotes
from stockpilot.domain.entities.tenant_order import identifier
from stockpilot.domain.entities.tenant_product import MAX_PRICE_MINOR
from stockpilot.domain.entities.tenant_stock_operation import (
    ImportIssue,
    ImportMode,
    quantity,
)

RULES_VERSION = "b2b-csv-v1"
MAX_BYTES = 256 * 1024
MAX_ROWS = 100
MAX_FIELD = 8192
MAX_ISSUES = 50
_HEADERS = {
    ImportMode.CREATE_ONLY: frozenset(
        {
            "sku",
            "name",
            "description",
            "category",
            "price_minor",
            "currency",
            "quantity",
        }
    ),
    ImportMode.METADATA_ONLY: frozenset(
        {"sku", "name", "description", "category", "price_minor", "currency"}
    ),
    ImportMode.STOCK_ONLY: frozenset({"sku", "quantity"}),
}


@dataclass(frozen=True)
class ParsedTenantCsv:
    file_fingerprint: str
    rows: tuple[dict, ...]
    issues: tuple[ImportIssue, ...]


def parse_tenant_csv(content: bytes, mode: ImportMode) -> ParsedTenantCsv:
    if type(content) is not bytes or not isinstance(mode, ImportMode):
        raise TypeError("Bytes and an explicit import mode required.")
    if len(content) > MAX_BYTES:
        raise ValueError("CSV size limit exceeded.")
    digest = hashlib.sha256(content).hexdigest()
    issues, entries = [], []

    def issue(line, code, message):
        if len(issues) < MAX_ISSUES:
            issues.append(ImportIssue(line, code, message))

    try:
        text = content.decode("utf-8-sig", errors="strict")
        candidates = []
        for separator in (",", ";"):
            try:
                header = next(
                    csv.reader(
                        io.StringIO(text, newline=""), delimiter=separator, strict=True
                    ),
                    [],
                )
            except csv.Error:
                continue
            if len(header) == len(_HEADERS[mode]) and set(header) == _HEADERS[mode]:
                candidates.append(separator)
        if len(candidates) != 1:
            raise ValueError()
        separator = candidates[0]
        validate_quotes(text, separator)
        reader = csv.reader(
            io.StringIO(text, newline=""), delimiter=separator, strict=True
        )
        header = next(reader)
        seen = set()
        count = 0
        while True:
            line = reader.line_num + 1
            try:
                cells = next(reader)
            except StopIteration:
                break
            count += 1
            if count > MAX_ROWS:
                issue(0, "row_limit", "At most 100 rows allowed.")
                break
            if len(cells) != len(header) or any(len(v) > MAX_FIELD for v in cells):
                issue(line, "row", "Wrong field count or size.")
                continue
            row = dict(zip(header, cells))
            try:
                identifier(row["sku"])
                if row["sku"] in seen:
                    issue(line, "duplicate", "SKU repeated within file.")
                    continue
                seen.add(row["sku"])
                if "quantity" in row:
                    if not re.fullmatch(r"(?:0|[1-9][0-9]{0,9})", row["quantity"]):
                        raise ValueError()
                    row["quantity"] = int(row["quantity"])
                    quantity(row["quantity"])
                if "price_minor" in row:
                    if not re.fullmatch(r"(?:0|[1-9][0-9]{0,18})", row["price_minor"]):
                        raise ValueError()
                    row["price_minor"] = int(row["price_minor"])
                    if row["price_minor"] > MAX_PRICE_MINOR:
                        raise ValueError()
                    for key, limit in (("name", 512), ("category", 128)):
                        if (
                            not row[key]
                            or not row[key].strip()
                            or len(row[key]) > limit
                        ):
                            raise ValueError()
                    if not re.fullmatch(r"[A-Z]{3}", row["currency"]):
                        raise ValueError()
                validate_values(row, mode)
                entries.append({"line": line, "values": row})
            except (ValueError, TypeError):
                issue(
                    line,
                    "values",
                    "Invalid SKU, metadata, integer price/currency or physical quantity.",
                )
        if not count:
            issue(0, "empty", "At least one row required.")
    except (UnicodeError, csv.Error, ValueError):
        issue(0, "file", "Invalid UTF-8, header or CSV quoting.")
    # An invalid preview retains bounded diagnostics only, never a partial apply plan.
    return ParsedTenantCsv(digest, () if issues else tuple(entries), tuple(issues))


def validate_values(row, mode):
    """Validate normalized persisted plans too, including BSON integers."""
    if not isinstance(row, dict) or set(row) != _HEADERS[mode]:
        raise ValueError("Invalid normalized fields.")
    identifier(row["sku"])
    if "quantity" in row:
        if not isinstance(row["quantity"], int) or isinstance(row["quantity"], bool):
            raise ValueError("Integer required.")
        quantity(int(row["quantity"]))
    if "price_minor" in row:
        if (
            not isinstance(row["price_minor"], int)
            or isinstance(row["price_minor"], bool)
            or not 0 <= row["price_minor"] <= MAX_PRICE_MINOR
        ):
            raise ValueError("Invalid integer price.")
        for key, limit in (("name", 512), ("category", 128), ("description", 8192)):
            if type(row[key]) is not str or len(row[key]) > limit:
                raise ValueError("Invalid metadata.")
            if key != "description" and (not row[key] or row[key].strip() != row[key]):
                raise ValueError("Invalid metadata identifier.")
        if type(row["currency"]) is not str or not re.fullmatch(
            r"[A-Z]{3}", row["currency"]
        ):
            raise ValueError("Invalid currency.")
