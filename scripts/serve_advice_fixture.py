"""Synthetic browser-only API: no database clients, provider calls or business writes."""
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import FastAPI

from evals.fixtures.catalogue_advice import MemoryCatalogue, advice_graph
from stockpilot.application.dto.intent_result import IntentResult
from stockpilot.application.use_cases.check_stock import StockResult
from stockpilot.domain.enums.intent import Intent
from stockpilot.infrastructure.config.settings import Settings
from stockpilot.presentation.api.routes.assistant import router


class BrowserCatalogue(MemoryCatalogue):
    async def find_resolution_skus(self, sku):
        if sku == 'OFFLINE-1':
            raise ConnectionError('Synthetic catalogue outage')
        return await super().find_resolution_skus(sku)


def create_app():
    if os.environ.get('ADVICE_SYNTHETIC_E2E') != '1':
        raise RuntimeError('Explicit synthetic E2E mode required')
    secret = os.environ['CUSTOMER_JWT_SECRET']
    stock = SimpleNamespace(execute=AsyncMock(return_value=StockResult(
        sku='D-C', name='Perceuse Concurrence Synthétique V017', stock=5, available=True)))
    graph, _, llm, _ = advice_graph(BrowserCatalogue(), check_stock=stock)
    llm.classify_intent.side_effect = lambda message: IntentResult(
        intent=Intent.STOCK_CHECK, product_name='Perceuse Concurrence Synthétique V017'
    ) if message == 'Le stock du SKU D-C ?' else IntentResult(intent=Intent.UNKNOWN)
    app = FastAPI()
    app.state.settings = Settings(_env_file=None, customer_jwt_secret=secret,
        deployment_environment='development', log_file=None, otel_enabled=False,
        mongo_uri='mongodb://127.0.0.1:9', postgres_url='postgresql://unused:unused@127.0.0.1:9/unused',
        langgraph_checkpoint_db_uri=None)
    app.state.stockpilot_workflow = graph
    app.include_router(router)

    @app.get('/health')
    def health():
        return {'status': 'synthetic-advice-only'}

    return app


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(create_app(), host='127.0.0.1', port=8002, log_level='warning')
