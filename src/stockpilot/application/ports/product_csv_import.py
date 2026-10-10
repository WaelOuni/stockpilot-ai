from typing import Protocol

from stockpilot.application.dto.product_csv_import import CsvImportResult
from stockpilot.domain.entities.product import Product


class ProductCsvImportPort(Protocol):
    def existing_skus(self, skus: list[str]) -> set[str]: ...
    def execute(self, operation_id: str, fingerprint: str, products: list[Product]) -> CsvImportResult: ...
    def result(self, operation_id: str) -> CsvImportResult | None: ...
