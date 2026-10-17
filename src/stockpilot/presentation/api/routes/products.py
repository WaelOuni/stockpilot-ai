from stockpilot.presentation.api.auth import require_customer_identity
from fastapi import APIRouter, Depends, HTTPException, Query

from stockpilot.application.use_cases.catalog_products import GetProductUseCase, ListProductsUseCase
from stockpilot.presentation.api.dependencies import get_product_use_case, get_list_products_use_case
from stockpilot.presentation.api.schemas.product import ProductResponse, ProductsResponse
from stockpilot.domain.enums.product_category import ProductCategory


router = APIRouter(dependencies=[Depends(require_customer_identity)])


@router.get("/products", response_model=ProductsResponse, response_model_exclude_none=True)
async def list_products(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    q: str | None = Query(default=None, max_length=100),
    category: ProductCategory | None = Query(default=None),
    available: bool | None = Query(default=None),
    include_total: bool = Query(False),
    use_case: ListProductsUseCase = Depends(get_list_products_use_case),
) -> ProductsResponse:
    page = await use_case.execute(limit=limit, offset=offset, q=q, category=category, available=available, **({"include_total": True} if include_total else {}))
    items = [ProductResponse.from_product(product) for product in page.items]
    return ProductsResponse(items=items, limit=limit, offset=offset, count=len(items), has_more=page.has_more, total=page.total if include_total else None)


@router.get("/products/{sku}", response_model=ProductResponse)
async def get_product(
    sku: str, use_case: GetProductUseCase = Depends(get_product_use_case),
) -> ProductResponse:
    product = await use_case.execute(sku)
    if product is None:
        raise HTTPException(status_code=404, detail="Produit introuvable (NOT_FOUND).")
    return ProductResponse.from_product(product)
