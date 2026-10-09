from stockpilot.domain.enums.product_category import ProductCategory


def normalize_category(raw_product: str | None) -> ProductCategory | None:
    if not raw_product:
        return None

    value = raw_product.lower()

    if "perceuse" in value or "drill" in value:
        return ProductCategory.DRILL

    if "visseuse" in value or "screwdriver" in value:
        return ProductCategory.SCREWDRIVER

    if "scie" in value or "saw" in value:
        return ProductCategory.SAW

    if "ponceuse" in value or "sander" in value:
        return ProductCategory.SANDER

    return ProductCategory.OTHER
