from fastapi import APIRouter, Depends, HTTPException, Query

from stockpilot.application.use_cases.catalog_products import GetProductUseCase, ListProductsUseCase
from stockpilot.presentation.api.dependencies import get_product_use_case, get_list_products_use_case
from stockpilot.presentation.api.schemas.product import ProductResponse, ProductsResponse
from stockpilot.domain.enums.product_category import ProductCategory


router = APIRouter()


@router.get("/products", response_model=ProductsResponse)
async def list_products(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    q: str | None = Query(default=None, max_length=100),
    category: ProductCategory | None = Query(default=None),
    available: bool | None = Query(default=None),
    use_case: ListProductsUseCase = Depends(get_list_products_use_case),
) -> ProductsResponse:
    products = await use_case.execute(limit=limit, offset=offset, q=q, category=category, available=available)
    items = [ProductResponse.from_product(product) for product in products]
    return ProductsResponse(items=items, limit=limit, offset=offset, count=len(items))


@router.get("/products/{sku}", response_model=ProductResponse)
async def get_product(
    sku: str, use_case: GetProductUseCase = Depends(get_product_use_case),
) -> ProductResponse:
    product = await use_case.execute(sku)
    if product is None:
        raise HTTPException(status_code=404, detail="Produit introuvable (NOT_FOUND).")
    return ProductResponse.from_product(product)
