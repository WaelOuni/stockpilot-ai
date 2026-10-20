"""B2B facts, separate from the historical Product and reservation engine."""

from dataclasses import dataclass

from stockpilot.domain.identity_validation import require_identifier

MAX_PRICE_MINOR = 2**63 - 1


@dataclass(frozen=True)
class TenantProduct:
    product_id: str
    organization_id: str
    sku: str
    name: str
    description: str
    category: str
    price_minor: int
    currency: str
    status: str
    attributes: tuple[str, ...]
    revision: int

    def __post_init__(self):
        for value in (
            self.product_id,
            self.organization_id,
            self.sku,
            self.name,
            self.category,
        ):
            require_identifier(value)
        if not isinstance(self.description, str):
            raise TypeError("Invalid description.")
        if (
            type(self.price_minor) is not int
            or not 0 <= self.price_minor <= MAX_PRICE_MINOR
        ):
            raise ValueError("Nonnegative integer minor-unit price required.")
        if (
            not isinstance(self.currency, str)
            or len(self.currency) != 3
            or not self.currency.isascii()
            or not self.currency.isupper()
            or not self.currency.isalpha()
        ):
            raise ValueError("Three uppercase currency letters required.")
        if self.status not in ("active", "inactive"):
            raise ValueError("Invalid product status.")
        if type(self.revision) is not int or self.revision < 1:
            raise ValueError("Positive revision required.")
        if not isinstance(self.attributes, tuple) or any(
            not isinstance(v, str) or not v or len(v) > 128 for v in self.attributes
        ):
            raise ValueError("Invalid attributes.")


@dataclass(frozen=True)
class TenantStock:
    product_id: str
    organization_id: str
    quantity: int
    reserved: int
    status: str
    revision: int

    def __post_init__(self):
        require_identifier(self.product_id)
        require_identifier(self.organization_id)
        if (
            any(
                type(v) is not int
                for v in (self.quantity, self.reserved, self.revision)
            )
            or not 0 <= self.reserved <= self.quantity
            or self.revision < 1
        ):
            raise ValueError("Invalid stock facts.")
        if self.status not in ("active", "inactive"):
            raise ValueError("Invalid stock status.")

    @property
    def available(self) -> int:
        return self.quantity - self.reserved if self.status == "active" else 0


@dataclass(frozen=True)
class TenantProductQuery:
    limit: int = 20
    offset: int = 0
    category: str | None = None
    attribute: str | None = None
    minimum_price_minor: int | None = None
    maximum_price_minor: int | None = None
    currency: str | None = None
    status: str | None = None

    def __post_init__(self):
        if (
            type(self.limit) is not int
            or not 1 <= self.limit <= 100
            or type(self.offset) is not int
            or not 0 <= self.offset <= 10000
        ):
            raise ValueError("Invalid pagination.")
        for value in (self.category, self.attribute, self.currency, self.status):
            if value is not None and (
                type(value) is not str or not value or len(value) > 128
            ):
                raise ValueError("Scalar filter required.")
        for value in (self.minimum_price_minor, self.maximum_price_minor):
            if value is not None and (
                type(value) is not int or not 0 <= value <= MAX_PRICE_MINOR
            ):
                raise ValueError("Integer price filter required.")
        if (
            self.minimum_price_minor is not None
            and self.maximum_price_minor is not None
            and self.minimum_price_minor > self.maximum_price_minor
        ):
            raise ValueError("Invalid price range.")
        if (
            self.minimum_price_minor is not None or self.maximum_price_minor is not None
        ) and self.currency is None:
            raise ValueError("Currency required for price comparisons.")
        if self.currency is not None and (
            len(self.currency) != 3
            or not self.currency.isascii()
            or not self.currency.isalpha()
            or not self.currency.isupper()
        ):
            raise ValueError("Invalid currency.")
        if self.status is not None and self.status not in ("active", "inactive"):
            raise ValueError("Invalid status.")


@dataclass(frozen=True)
class TenantProductPage:
    items: tuple[TenantProduct, ...]
    total: int
    has_more: bool
