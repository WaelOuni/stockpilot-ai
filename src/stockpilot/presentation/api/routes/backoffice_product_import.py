from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from stockpilot.application.use_cases.manage_products import ProductSkuIndexNotReadyError
from stockpilot.application.dto.product_csv_import import CsvPreview, CsvImportResult
from stockpilot.application.use_cases.product_csv_import import (
    CsvImportConflict, CsvImportIndexNotReady, CsvImportUnknown,
)
from stockpilot.presentation.api.admin_auth import (
    SafeSessionRoute, Session, require_admin_cookie_only, require_admin_mutation,
)


router = APIRouter(prefix="/backoffice/products/import", tags=["backoffice product import"],
                   route_class=SafeSessionRoute)
CSV_BODY = {"requestBody": {"required": True, "content": {
    "text/csv": {"schema": {"type": "string", "format": "binary"}},
}}}


async def content(request):
    limit = request.app.state.preview_product_csv_import.max_bytes
    length = request.headers.get("content-length")
    if length:
        try:
            if int(length) > limit or int(length) < 0:
                raise HTTPException(413, "CSV trop volumineux.")
        except ValueError:
            raise HTTPException(422, "Taille invalide.") from None
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > limit:
            raise HTTPException(413, "CSV trop volumineux.")
        body.extend(chunk)
    return bytes(body)


def failure(error):
    if isinstance(error, ValueError):
        return HTTPException(422, "CSV, confirmation ou aperçu invalide/expiré.")
    if isinstance(error, CsvImportConflict):
        return HTTPException(409, "Import CREATE_ONLY refusé : conflit ou fichier invalide.")
    if isinstance(error, (CsvImportIndexNotReady, ProductSkuIndexNotReadyError)):
        return HTTPException(503, "Index préalable d’import non provisionné.")
    if isinstance(error, CsvImportUnknown):
        return HTTPException(503, "Résultat inconnu : rapprochez l’opération, ne réessayez pas.")
    return HTTPException(500, "Import impossible ; vérifiez le résultat de l’opération.")


@router.post("/preview", response_model=CsvPreview, openapi_extra=CSV_BODY)
async def preview(request: Request, session: Session = Depends(require_admin_mutation)):
    body = await content(request)
    try:
        result = await run_in_threadpool(request.app.state.preview_product_csv_import.execute,
                                        body, session.csrf_token)
        return asdict(result)
    except Exception as error:
        raise failure(error) from error


@router.post("/execute", response_model=CsvImportResult, openapi_extra={
    **CSV_BODY,
    "parameters": [
        {"name": "X-Import-Operation-Id", "in": "header", "required": True,
         "schema": {"type": "string", "format": "uuid"}},
        {"name": "X-Import-Preview", "in": "header", "required": True, "schema": {"type": "string"}},
        {"name": "X-Import-Confirm", "in": "header", "required": True,
         "schema": {"type": "string", "enum": ["CREATE_ONLY"]}},
    ],
})
async def execute(request: Request, session: Session = Depends(require_admin_mutation)):
    body = await content(request)
    try:
        headers = request.headers
        if any(len(headers.getlist(name)) != 1 for name in (
            "x-import-operation-id", "x-import-preview", "x-import-confirm",
        )):
            raise ValueError()
        result = await run_in_threadpool(
            request.app.state.execute_product_csv_import.execute, body, session.csrf_token,
            headers["x-import-operation-id"], headers["x-import-preview"],
            headers["x-import-confirm"] == "CREATE_ONLY",
        )
        return asdict(result)
    except Exception as error:
        raise failure(error) from error


@router.get("/batches/{operation_id}", response_model=CsvImportResult,
            dependencies=[Depends(require_admin_cookie_only)])
async def batch(operation_id: str, request: Request):
    try:
        result = await run_in_threadpool(request.app.state.get_product_csv_import_result.execute,
                                        operation_id)
    except Exception as error:
        raise failure(error) from error
    if result is None:
        raise HTTPException(404, "Aucun résultat visible ; absence ne prouve pas un rollback.")
    return asdict(result)
