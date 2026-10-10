from dataclasses import dataclass, field

from stockpilot.domain.entities.product import Product


@dataclass(frozen=True)
class CsvIssue:
    line: int
    code: str
    message: str


@dataclass
class CsvPreview:
    fingerprint: str
    analysed: int = 0
    valid: int = 0
    invalid: int = 0
    existing_conflicts: int = 0
    issues: list[CsvIssue] = field(default_factory=list)
    products: list[Product] = field(default_factory=list)
    product_lines: list[int] = field(default_factory=list)
    binding: str | None = None
    delimiter: str | None = None
    issues_truncated: bool = False


@dataclass(frozen=True)
class CsvImportResult:
    operation_id: str
    fingerprint: str
    created: int
    skus: list[str]
