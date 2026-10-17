from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI
from stockpilot.presentation.api.saas_boundary import SaasBoundaryMiddleware
from stockpilot.presentation.api.admin_auth import AdminSessions, router as admin_session_router
from stockpilot.application.use_cases.backoffice_orders import GetBackofficeOrderUseCase, ListBackofficeOrdersUseCase
from stockpilot.presentation.api.routes.backoffice_orders import router as backoffice_orders_router
from stockpilot.application.use_cases.backoffice_dashboard import GetBackofficeDashboardUseCase
from stockpilot.presentation.api.routes.backoffice_dashboard import router as backoffice_dashboard_router

from stockpilot.application.use_cases.check_stock import CheckStockUseCase
from stockpilot.application.use_cases.classify_intent import ClassifyIntentUseCase
from stockpilot.application.use_cases.finalize_order import FinalizeOrderUseCase
from stockpilot.application.use_cases.get_order_status import GetOrderStatusUseCase
from stockpilot.application.use_cases.generate_response import GenerateResponseUseCase
from stockpilot.application.use_cases.resolve_product import ResolveProductUseCase
from stockpilot.application.use_cases.prepare_order import PrepareOrderUseCase
from stockpilot.application.use_cases.update_order_status import UpdateOrderStatusUseCase
from stockpilot.application.use_cases.cancel_order import CancelOrderUseCase
from stockpilot.application.use_cases.list_customer_orders import ListCustomerOrdersUseCase
from stockpilot.application.use_cases.resolve_context import ContextResolutionUseCase
from stockpilot.application.use_cases.get_customer_order import GetCustomerOrderUseCase
from stockpilot.application.use_cases.catalog_products import GetProductUseCase, ListProductsUseCase
from stockpilot.presentation.api.routes.products import router as products_router
from stockpilot.presentation.api.routes.backoffice_products import router as backoffice_products_router
from stockpilot.presentation.api.routes.backoffice_product_import import router as product_import_router
from stockpilot.application.use_cases.product_csv_import import (
    PreviewProductCsvImportUseCase, ExecuteProductCsvImportUseCase, GetProductCsvImportResultUseCase,
)
from stockpilot.infrastructure.persistence.mongodb.product_import import MongoProductCsvImport
from stockpilot.presentation.api.routes.backoffice_stock import (
    operations_router as backoffice_stock_operations_router,
    products_router as backoffice_stock_products_router,
)
from stockpilot.application.use_cases.manage_products import CreateProductUseCase, UpdateProductMetadataUseCase
from stockpilot.application.use_cases.adjust_product_stock import (
    AdjustProductStockUseCase, GetStockAdjustmentUseCase, ListStockMovementsUseCase,
)
from stockpilot.application.use_cases.resolve_order_reference import ResolveOrderReferenceUseCase
from stockpilot.application.workflows.stockpilot_workflow import build_graph

from stockpilot.infrastructure.config.settings import Settings
from stockpilot.infrastructure.llm.ollama_adapter import OllamaAdapter
from stockpilot.infrastructure.persistence.mongodb.client import create_mongo_client
from stockpilot.infrastructure.persistence.mongodb.product_repository import MongoProductRepository
from stockpilot.infrastructure.persistence.mongodb.order_repository import MongoOrderRepository
from stockpilot.infrastructure.persistence.mongodb.order_transaction import MongoOrderTransaction

from stockpilot.presentation.api.routes.assistant import router as assistant_router
from stockpilot.presentation.api.routes.chat import router as chat_router
from stockpilot.presentation.api.routes.health import router as health_router
from stockpilot.presentation.api.routes.orders import router as orders_router

from stockpilot.application.use_cases.catalogue_advice import CatalogueAdviceUseCase
from stockpilot.application.use_cases.recommend_product import RecommendProductUseCase

from stockpilot.infrastructure.embeddings.ollama_embedding_adapter import (
    OllamaEmbeddingAdapter,
)
from stockpilot.infrastructure.persistence.postgres.pgvector_knowledge_repository import (
    PgVectorKnowledgeRepository,
)
from stockpilot.infrastructure.persistence.postgres.checkpointer import open_checkpointer
from stockpilot.observability import configure_logging
from stockpilot.presentation.api.observability import RequestLoggingMiddleware
from stockpilot.telemetry import initialize_telemetry, instrument_app


logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(settings.log_level, settings.log_file)
    order_repository.initialize()
    checkpoint_uri = (
        settings.langgraph_checkpoint_db_uri
        if settings.langgraph_checkpoint_db_uri is not None
        else settings.postgres_url
    )
    async with open_checkpointer(checkpoint_uri) as checkpointer:
        app.state.stockpilot_workflow = build_graph(
            classify_intent_use_case,
            check_stock_use_case,
            recommend_product_use_case,
            prepare_order_use_case,
            finalize_order_use_case,
            checkpointer=checkpointer,
            get_order_status=get_order_status_use_case,
            resolve_context=context_resolution_use_case,
            catalogue_advice=catalogue_advice_use_case,
            resolve_order_reference=ResolveOrderReferenceUseCase(product_repository, order_repository),
        )
        try:
            yield
        finally:
            app.state.stockpilot_workflow = None
            if telemetry_provider is not None:
                telemetry_provider.force_flush()
                from stockpilot.telemetry import _meter_provider
                if _meter_provider is not None:
                    _meter_provider.force_flush()


app = FastAPI(
    title="StockPilot AI",
    version="0.1.0",
    lifespan=lifespan,
)
app.add_middleware(SaasBoundaryMiddleware)
app.add_middleware(RequestLoggingMiddleware)

settings = Settings()
app.state.settings = settings
telemetry_provider = initialize_telemetry(settings)
instrument_app(app, telemetry_provider)

# LLM
ollama = OllamaAdapter(settings)

# MongoDB
mongo_client = create_mongo_client(settings.mongo_uri)
mongo_database = mongo_client[settings.mongo_database]

product_repository = MongoProductRepository(mongo_database)
order_repository = MongoOrderRepository(mongo_database)

# RAG
embedding_adapter = OllamaEmbeddingAdapter(settings)

knowledge_repository = PgVectorKnowledgeRepository(
    settings.postgres_url
)

# Use cases
generate_response_use_case = GenerateResponseUseCase(ollama)

classify_intent_use_case = ClassifyIntentUseCase(ollama)

check_stock_use_case = CheckStockUseCase(
    product_repository
)

prepare_order_use_case = PrepareOrderUseCase(
    check_stock_use_case, ResolveProductUseCase(check_stock_use_case.product_repository),
)
finalize_order_use_case = FinalizeOrderUseCase(MongoOrderTransaction(mongo_database))
get_order_status_use_case = GetOrderStatusUseCase(order_repository)
update_order_status_use_case = UpdateOrderStatusUseCase(order_repository)
cancel_order_use_case = CancelOrderUseCase(order_repository)
list_customer_orders_use_case = ListCustomerOrdersUseCase(order_repository)
context_resolution_use_case = ContextResolutionUseCase()
get_customer_order_use_case = GetCustomerOrderUseCase(order_repository)

catalogue_advice_use_case = CatalogueAdviceUseCase(product_repository, ollama)
recommend_product_use_case = RecommendProductUseCase(
    embedding_adapter,
    knowledge_repository,
    ollama,
)

app.state.generate_response_use_case = generate_response_use_case
app.state.classify_intent_use_case = classify_intent_use_case
app.state.stockpilot_workflow = None
app.state.check_stock_use_case = check_stock_use_case
app.state.recommend_product_use_case = recommend_product_use_case
app.state.prepare_order_use_case = prepare_order_use_case
app.state.finalize_order_use_case = finalize_order_use_case
app.state.get_order_status_use_case = get_order_status_use_case
app.state.update_order_status_use_case = update_order_status_use_case
app.state.cancel_order_use_case = cancel_order_use_case
app.state.list_customer_orders_use_case = list_customer_orders_use_case
app.state.get_customer_order_use_case = get_customer_order_use_case
app.state.get_product_use_case = GetProductUseCase(product_repository)
app.state.list_products_use_case = ListProductsUseCase(product_repository)
app.state.create_product_use_case = CreateProductUseCase(product_repository)
app.state.preview_product_csv_import = PreviewProductCsvImportUseCase(
    MongoProductCsvImport(mongo_database), max_bytes=settings.product_import_max_bytes,
    max_products=settings.product_import_max_products,
)
app.state.execute_product_csv_import = ExecuteProductCsvImportUseCase(app.state.preview_product_csv_import)
app.state.get_product_csv_import_result = GetProductCsvImportResultUseCase(
    app.state.preview_product_csv_import.repository,
)
app.state.update_product_metadata_use_case = UpdateProductMetadataUseCase(product_repository)
app.state.adjust_product_stock_use_case = AdjustProductStockUseCase(product_repository)
app.state.get_stock_adjustment_use_case = GetStockAdjustmentUseCase(product_repository)
app.state.list_stock_movements_use_case = ListStockMovementsUseCase(product_repository)
app.state.list_backoffice_orders_use_case = ListBackofficeOrdersUseCase(order_repository)
app.state.get_backoffice_order_use_case = GetBackofficeOrderUseCase(order_repository)
app.state.get_backoffice_dashboard_use_case = GetBackofficeDashboardUseCase(
    order_repository, product_repository,
)

app.include_router(health_router)
app.include_router(chat_router)
app.include_router(assistant_router)
app.include_router(orders_router)
app.include_router(products_router)
app.state.admin_sessions = AdminSessions()
app.include_router(admin_session_router)
app.include_router(backoffice_orders_router)
app.include_router(backoffice_dashboard_router)
app.include_router(product_import_router)
app.include_router(backoffice_products_router)
app.include_router(backoffice_stock_products_router)
app.include_router(backoffice_stock_operations_router)


@app.middleware("http")
async def protect_admin_responses(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/backoffice/"):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
    return response
