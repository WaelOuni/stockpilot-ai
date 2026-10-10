import csv
import hashlib
import hmac
import io
import secrets
import time
from uuid import UUID

from stockpilot.application.dto.product_csv_import import CsvImportResult, CsvIssue, CsvPreview
from stockpilot.application.ports.product_csv_import import ProductCsvImportPort
from stockpilot.application.use_cases.manage_products import validate_metadata
from stockpilot.domain.entities.product import Product
from stockpilot.domain.enums.product_category import ProductCategory


class CsvImportConflict(Exception):
    pass


class CsvImportUnknown(Exception):
    pass


class CsvImportIndexNotReady(Exception):
    pass


def validate_operation(operation_id: str) -> None:
    try:
        value = UUID(operation_id)
        if value.version != 4 or str(value) != operation_id:
            raise ValueError()
    except (ValueError, AttributeError):
        raise ValueError("Canonical UUIDv4 required.") from None


def validate_quotes(text: str, delimiter: str) -> None:
    quoted = closed = False
    field_start = True
    index = 0
    while index < len(text):
        character = text[index]
        if quoted:
            if character == '"':
                if index + 1 < len(text) and text[index + 1] == '"':
                    index += 1
                else:
                    quoted = False
                    closed = True
        elif character in (delimiter, "\r", "\n"):
            field_start = True
            closed = False
        elif character == '"' and field_start and not closed:
            quoted = True
            field_start = False
        elif character == '"' or closed:
            raise ValueError("Malformed CSV quoting.")
        else:
            field_start = False
        index += 1
    if quoted:
        raise ValueError("Malformed CSV quoting.")


class PreviewProductCsvImportUseCase:
    def __init__(self, repository: ProductCsvImportPort, max_bytes=2 * 1024 * 1024,
                 max_products=500, max_field=4096, max_issues=100, clock=time.time):
        self.repository = repository
        self.max_bytes = max_bytes
        self.max_products = max_products
        self.max_field = max_field
        self.max_issues = max_issues
        self.clock = clock
        self.secret = secrets.token_bytes(32)

    def signature(self, fingerprint: str, scope: str, expiry: str) -> str:
        return hmac.new(self.secret, f"{fingerprint}:{scope}:{expiry}".encode(), hashlib.sha256).hexdigest()

    def approve(self, binding: str, fingerprint: str, scope: str) -> None:
        try:
            expiry, signature = binding.split(".")
            if int(expiry) < self.clock() or not hmac.compare_digest(
                signature, self.signature(fingerprint, scope, expiry),
            ):
                raise ValueError()
        except (ValueError, AttributeError):
            raise ValueError("Preview expired or does not match this file/session.") from None

    def execute(self, content: bytes, scope: str, check_existing: bool = True) -> CsvPreview:
        if len(content) > self.max_bytes:
            raise ValueError("CSV size limit exceeded.")
        preview = CsvPreview(hashlib.sha256(content).hexdigest())

        def issue(line, code, message):
            if len(preview.issues) < self.max_issues:
                preview.issues.append(CsvIssue(line, code, message))
            else:
                preview.issues_truncated = True

        try:
            text = content.decode("utf-8-sig", errors="strict")
            if not text:
                raise ValueError("Empty CSV.")
            # Only a quote-aware exact header chooses the separator, not MIME or extension.
            candidates = []
            for separator in (",", ";"):
                try:
                    header = next(csv.reader(io.StringIO(text, newline=""), delimiter=separator, strict=True), [])
                except csv.Error:
                    continue
                if len(header) == 4 and set(header) == {"sku", "name", "category", "price"}:
                    candidates.append(separator)
            if len(candidates) != 1:
                raise ValueError("Header must contain sku,name,category,price exactly once.")
            preview.delimiter = candidates[0]
            validate_quotes(text, preview.delimiter)
            reader = csv.reader(io.StringIO(text, newline=""), delimiter=preview.delimiter, strict=True)
            header = next(reader)
            entries = []
            while True:
                line = reader.line_num + 1
                try:
                    row = next(reader)
                except StopIteration:
                    break
                preview.analysed += 1
                if preview.analysed > self.max_products:
                    raise ValueError("Product count limit exceeded.")
                if len(row) != 4 or any(len(value) > self.max_field for value in row):
                    issue(line, "row", "Exactly four bounded fields required; empty lines are invalid.")
                    preview.invalid += 1
                    continue
                values = dict(zip(header, row))
                try:
                    if not values["sku"].strip():
                        raise ValueError()
                    category = ProductCategory(values["category"])
                    price = float(values["price"])
                    validate_metadata({"name": values["name"], "category": category, "price": price})
                    entries.append((line, Product(values["sku"], values["name"], category, price, 0)))
                except (ValueError, OverflowError):
                    issue(line, "values", "Invalid SKU/name/category or nonnegative finite numeric price.")
                    preview.invalid += 1
            if not preview.analysed:
                raise ValueError("At least one product required.")
            seen = {}
            for line, product in entries:
                seen.setdefault(product.sku, []).append(line)
            existing = self.repository.existing_skus(list(seen)) if seen and check_existing else set()
            for line, product in entries:
                if len(seen[product.sku]) > 1:
                    issue(line, "duplicate", "SKU repeated within this file.")
                    preview.invalid += 1
                if product.sku in existing:
                    issue(line, "existing", "SKU already exists; CREATE_ONLY never updates.")
                    preview.existing_conflicts += 1
                if len(seen[product.sku]) == 1 and product.sku not in existing:
                    preview.products.append(product)
                    preview.product_lines.append(line)
                    preview.valid += 1
        except (UnicodeError, csv.Error, ValueError) as error:
            issue(0, "file", str(error) if isinstance(error, ValueError) else "Invalid UTF-8 or malformed CSV.")
            preview.invalid += 1
        if not preview.invalid and not preview.existing_conflicts:
            expiry = str(int(self.clock()) + 1800)
            preview.binding = expiry + "." + self.signature(preview.fingerprint, scope, expiry)
        return preview


class ExecuteProductCsvImportUseCase:
    def __init__(self, preview: PreviewProductCsvImportUseCase):
        self.preview = preview
        self.repository = preview.repository

    def execute(self, content: bytes, scope: str, operation_id: str, binding: str,
                confirmed: bool) -> CsvImportResult:
        validate_operation(operation_id)
        if len(content) > self.preview.max_bytes:
            raise ValueError("CSV size limit exceeded.")
        fingerprint = hashlib.sha256(content).hexdigest()
        if confirmed is not True:
            raise ValueError("Explicit confirmation required.")
        existing = self.repository.result(operation_id)
        if existing:
            if existing.fingerprint != fingerprint:
                raise CsvImportConflict("Operation belongs to a different file.")
            return existing
        self.preview.approve(binding, fingerprint, scope)
        preview = self.preview.execute(content, scope, check_existing=False)
        if preview.invalid or preview.existing_conflicts:
            existing = self.repository.result(operation_id)
            if existing and existing.fingerprint == fingerprint:
                return existing
            raise CsvImportConflict("File invalid or catalogue changed since preview.")
        return self.repository.execute(operation_id, fingerprint, preview.products)


class GetProductCsvImportResultUseCase:
    def __init__(self, repository: ProductCsvImportPort):
        self.repository = repository

    def execute(self, operation_id: str) -> CsvImportResult | None:
        validate_operation(operation_id)
        return self.repository.result(operation_id)
