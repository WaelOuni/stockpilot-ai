"""Explicit tenant APIs. No dependency on the historical FastAPI application."""

from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import Field, ValidationError

from stockpilot.application.ports.bff_security import BffUnavailable
from stockpilot.application.ports.tenant_orders import TenantOrderNotFound
from stockpilot.application.services.identity_authorization import AuthorizationDenied
from stockpilot.domain.entities.tenant_order import (
    CancelTenantOrder,
    ConfirmTenantOrder,
    PrepareTenantOrder,
    RequestedLine,
)
from stockpilot.domain.entities.tenant_product import TenantProductQuery
from stockpilot.domain.entities.tenant_stock_operation import (
    AdjustTenantStock,
    ArchiveTenantProduct,
    ApplyTenantImport,
    ImportMode,
)
from stockpilot.domain.enums.permission import Permission as P
from stockpilot.presentation.api.b2b_boundary import worker
from stockpilot.presentation.api.schemas.b2b import (
    AdjustmentIn,
    ArchiveIn,
    ApplyIn,
    BuyerOut,
    CancelIn,
    ConfirmIn,
    Contract,
    Identifier,
    MovementOut,
    OperationOut,
    OrderOut,
    PrepareIn,
    PreviewOut,
    ProductOut,
    StockOut,
)

router = APIRouter(prefix="/b2b/api", tags=["B2B business"])


class Page(Contract):
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=10000)


class CatalogueFilters(Page):
    category: str | None = Field(default=None, min_length=1, max_length=128)
    attribute: str | None = Field(default=None, min_length=1, max_length=128)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    minimum_price_minor: int | None = Field(default=None, ge=0, le=2**63 - 1)
    maximum_price_minor: int | None = Field(default=None, ge=0, le=2**63 - 1)
    status: Literal["active", "inactive"] | None = None
    available: bool | None = None


def exact_query(request, fields):
    if set(request.query_params) != fields or any(
        len(request.query_params.getlist(k)) != 1 for k in request.query_params
    ):
        raise ValueError("Exact unambiguous query required.")


def query(request, model):
    if any(len(request.query_params.getlist(k)) != 1 for k in request.query_params):
        raise ValueError("Ambiguous query.")
    return model.model_validate(dict(request.query_params))


def checked(model, value):
    try:
        return model.model_validate(value).model_dump(mode="json")
    except ValidationError:
        raise BffUnavailable("Invalid persisted business response.") from None


async def invoke(request, permission, port, method, *args, operation=None):
    request.state.operation = operation
    bundle = request.app.state.bff_business
    source, context = request.state.tenant_request, request.state.tenant_context

    def execute():
        current = bundle.authorization.resolve_context(source)
        required = (permission,) if isinstance(permission, P) else permission
        if current != context or not any(
            p in current.principal.permissions for p in required
        ):
            raise AuthorizationDenied("Current permission required.")
        return getattr(getattr(bundle, port), method)(source, context, *args)

    return await worker(request, execute)


def found(value):
    if value is None:
        raise TenantOrderNotFound("Resource unavailable.")
    return value


def reference(namespace, operation_id):
    return {"namespace": namespace, "operation_id": operation_id}


@router.get("/products")
async def products(request: Request):
    filters = query(request, CatalogueFilters)
    result = await invoke(
        request,
        P.CATALOGUE_READ,
        "catalogue",
        "search",
        TenantProductQuery(**filters.model_dump()),
    )
    return {
        "items": [checked(ProductOut, p) for p in result.items],
        "total": result.total,
        "has_more": result.has_more,
        "limit": filters.limit,
        "offset": filters.offset,
    }


@router.get("/products/by-sku")
async def product_sku_query(request: Request, sku: Identifier):
    exact_query(request, {"sku"})
    return await product_sku(request, sku)


@router.get("/products/export")
async def export_products(request: Request, mode: ImportMode, category: str | None = None):
    exact_query(request, {"mode"} | ({"category"} if "category" in request.query_params else set()))
    return await invoke(request, P.CATALOGUE_READ, "catalogue", "export_rows", mode, category)


@router.get("/products/by-sku/{sku:path}")
async def product_sku(request: Request, sku: Identifier):
    return checked(
        ProductOut,
        found(await invoke(request, P.CATALOGUE_READ, "catalogue", "by_sku", sku)),
    )


@router.get("/products/{product_id}")
async def product(request: Request, product_id: Identifier):
    return checked(
        ProductOut,
        found(
            await invoke(request, P.CATALOGUE_READ, "catalogue", "by_id", product_id)
        ),
    )


@router.get("/products/{product_id}/stock")
async def stock(request: Request, product_id: Identifier):
    return checked(
        StockOut,
        found(await invoke(request, P.STOCK_READ, "catalogue", "stock", product_id)),
    )


@router.post("/products/{product_id}/archive")
async def archive_product(request: Request, product_id: Identifier, body: ArchiveIn):
    result = await invoke(
        request, P.IMPORT_EXECUTE, "stock", "archive",
        ArchiveTenantProduct(body.operation_id, product_id, body.expected_revision, body.confirmed),
        operation=reference("archive", body.operation_id),
    )
    return checked(OperationOut, result)


@router.get("/buyers")
async def buyers(request: Request):
    page = query(request, Page)
    values = await invoke(
        request, P.ORDER_CREATE, "orders", "buyers", page.limit, page.offset
    )
    return {"items": [checked(BuyerOut, v) for v in values], **page.model_dump()}


@router.get("/orders")
async def orders(request: Request):
    page = query(request, Page)
    values = await invoke(
        request,
        (P.ORDER_READ_OWN, P.ORDER_READ_ORGANIZATION),
        "orders",
        "list",
        page.limit,
        page.offset,
    )
    return {"items": [checked(OrderOut, v) for v in values], **page.model_dump()}


@router.post("/orders/prepare")
async def prepare(request: Request, body: PrepareIn):
    command = PrepareTenantOrder(
        body.operation_id,
        body.customer_id,
        tuple(RequestedLine(x.sku, x.quantity) for x in body.lines),
    )
    return checked(
        OrderOut,
        await invoke(
            request,
            P.ORDER_CREATE,
            "orders",
            "prepare",
            command,
            operation=reference("prepare", body.operation_id),
        ),
    )


@router.get("/orders/operations")
async def order_result_query(
    request: Request,
    namespace: Literal["prepare", "confirm", "cancel"],
    operation_id: Identifier,
):
    exact_query(request, {"namespace", "operation_id"})
    return await order_result(request, namespace, operation_id)


@router.get("/orders/operations/{namespace}/{operation_id:path}")
async def order_result(
    request: Request,
    namespace: Literal["prepare", "confirm", "cancel"],
    operation_id: Identifier,
):
    permission = (
        (P.ORDER_CANCEL_OWN, P.ORDER_CANCEL_ORGANIZATION)
        if namespace == "cancel"
        else P.ORDER_CREATE
    )
    return checked(
        OrderOut,
        await invoke(
            request,
            permission,
            "orders",
            "result",
            namespace,
            operation_id,
            operation=reference(namespace, operation_id),
        ),
    )


@router.get("/orders/{order_id}")
async def order(request: Request, order_id: Identifier):
    return checked(
        OrderOut,
        await invoke(
            request,
            (P.ORDER_READ_OWN, P.ORDER_READ_ORGANIZATION),
            "orders",
            "get",
            order_id,
        ),
    )


@router.post("/orders/{order_id}/confirm")
async def confirm(request: Request, order_id: Identifier, body: ConfirmIn):
    return checked(
        OrderOut,
        await invoke(
            request,
            P.ORDER_CREATE,
            "orders",
            "confirm",
            ConfirmTenantOrder(
                body.operation_id, order_id, body.preparation_digest, body.confirmed
            ),
            operation=reference("confirm", body.operation_id),
        ),
    )


@router.post("/orders/{order_id}/cancel")
async def cancel(request: Request, order_id: Identifier, body: CancelIn):
    return checked(
        OrderOut,
        await invoke(
            request,
            (P.ORDER_CANCEL_OWN, P.ORDER_CANCEL_ORGANIZATION),
            "orders",
            "cancel",
            CancelTenantOrder(body.operation_id, order_id),
            operation=reference("cancel", body.operation_id),
        ),
    )


def operation_response(result):
    body = checked(OperationOut, result)
    status = {"succeeded": 200, "pending": 202, "rejected": 409}[body["status"]]
    return JSONResponse(body, status)


@router.post("/products/{product_id}/adjustments")
async def adjust(request: Request, product_id: Identifier, body: AdjustmentIn):
    return operation_response(
        await invoke(
            request,
            P.STOCK_ADJUST,
            "stock",
            "adjust",
            AdjustTenantStock(product_id=product_id, **body.model_dump()),
            operation=reference("adjust", body.operation_id),
        )
    )


@router.get("/stock/movements")
async def movements(request: Request):
    page = query(request, Page)
    values = await invoke(
        request, P.STOCK_READ, "stock", "movements", page.limit, page.offset
    )
    return {"items": [checked(MovementOut, v) for v in values], **page.model_dump()}


@router.post("/imports/preview")
async def preview(request: Request, mode: ImportMode):
    if (
        set(request.query_params) != {"mode"}
        or len(request.query_params.getlist("mode")) != 1
    ):
        raise ValueError("Explicit import mode required.")
    return checked(
        PreviewOut,
        await invoke(
            request, P.IMPORT_EXECUTE, "stock", "preview", await request.body(), mode
        ),
    )


@router.post("/imports/apply")
async def apply(request: Request, body: ApplyIn):
    return operation_response(
        await invoke(
            request,
            P.IMPORT_EXECUTE,
            "stock",
            "apply",
            ApplyTenantImport(
                body.operation_id, body.import_id, body.preview_digest, body.confirmed
            ),
            operation=reference("import", body.operation_id),
        )
    )


@router.get("/imports")
async def imports(request: Request):
    page = query(request, Page)
    values = await invoke(
        request, P.IMPORT_EXECUTE, "stock", "imports", page.limit, page.offset
    )
    return {"items": [checked(PreviewOut, v) for v in values], **page.model_dump()}


@router.get("/stock/operations")
async def stock_result_query(
    request: Request,
    namespace: Literal["adjust", "import", "archive"],
    operation_id: Identifier,
):
    exact_query(request, {"namespace", "operation_id"})
    return await stock_result(request, namespace, operation_id)


@router.get("/stock/operations/{namespace}/{operation_id:path}")
async def stock_result(
    request: Request, namespace: Literal["adjust", "import", "archive"], operation_id: Identifier
):
    return operation_response(
        await invoke(
            request,
            P.STOCK_ADJUST if namespace == "adjust" else P.IMPORT_EXECUTE,
            "stock",
            "result",
            namespace,
            operation_id,
            operation=reference(namespace, operation_id),
        )
    )
