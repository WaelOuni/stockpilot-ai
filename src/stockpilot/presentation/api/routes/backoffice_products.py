from fastapi import APIRouter, Depends, HTTPException, Query

from stockpilot.application.use_cases.catalog_products import GetProductUseCase, ListProductsUseCase
from stockpilot.application.use_cases.manage_products import (
    CreateProductUseCase, UpdateProductMetadataUseCase,
    DuplicateProductSkuError, ProductSkuIndexNotReadyError,
)
from stockpilot.domain.enums.product_category import ProductCategory
from stockpilot.presentation.api.admin_auth import SafeSessionRoute, require_admin_mutation, require_backoffice_read
from stockpilot.presentation.api.dependencies import (
    get_product_use_case, get_list_products_use_case, get_create_product_use_case,
    get_update_product_metadata_use_case,
)
from stockpilot.presentation.api.schemas.backoffice_product import CreateProductRequest, ProductMetadataRequest
from stockpilot.presentation.api.schemas.product import ProductResponse, ProductsResponse


class SafeProductRoute(SafeSessionRoute):
    validation_message = "Informations produit invalides."


router = APIRouter(prefix="/backoffice/products", route_class=SafeProductRoute)


def failure(error: Exception) -> HTTPException:
    if isinstance(error, DuplicateProductSkuError):
        return HTTPException(409, "Ce SKU existe déjà.")
    if isinstance(error, ProductSkuIndexNotReadyError):
        return HTTPException(503, "Gestion produits bloquée : index SKU unique non provisionné.")
    return HTTPException(500, "Opération produit impossible.")


@router.get("", response_model=ProductsResponse, response_model_exclude_none=True, dependencies=[Depends(require_backoffice_read)])
async def list_products(
    limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0),
    q: str | None = Query(None, max_length=100),
    category: ProductCategory | None = Query(None), available: bool | None = Query(None),
    include_total: bool = Query(False),
    use_case: ListProductsUseCase = Depends(get_list_products_use_case),
):
    try:
        page = await use_case.execute(limit, offset, q=q, category=category, available=available, **({"include_total": True} if include_total else {}))
        items = [ProductResponse.from_product(product) for product in page.items]
        return ProductsResponse(items=items, limit=limit, offset=offset, count=len(items), has_more=page.has_more, total=page.total if include_total else None)
    except Exception as error:
        raise failure(error) from error


@router.get("/{sku}", response_model=ProductResponse, dependencies=[Depends(require_backoffice_read)])
async def get_product(sku: str, use_case: GetProductUseCase = Depends(get_product_use_case)):
    try:
        product = await use_case.execute(sku)
        response = ProductResponse.from_product(product) if product else None
    except Exception as error:
        raise failure(error) from error
    if response is None:
        raise HTTPException(404, "Produit introuvable.")
    return response


@router.post("", response_model=ProductResponse, status_code=201, dependencies=[Depends(require_admin_mutation)])
async def create_product(
    request: CreateProductRequest, use_case: CreateProductUseCase = Depends(get_create_product_use_case),
):
    try:
        return ProductResponse.from_product(await use_case.execute(**request.model_dump()))
    except Exception as error:
        raise failure(error) from error


@router.patch("/{sku}", response_model=ProductResponse, dependencies=[Depends(require_admin_mutation)])
async def update_product(
    sku: str, request: ProductMetadataRequest,
    use_case: UpdateProductMetadataUseCase = Depends(get_update_product_metadata_use_case),
):
    try:
        product = await use_case.execute(sku, request.model_dump(exclude_unset=True))
        response = ProductResponse.from_product(product) if product else None
    except Exception as error:
        raise failure(error) from error
    if response is None:
        raise HTTPException(404, "Produit introuvable.")
    return response
