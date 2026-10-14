"""V12.25: an advice clarification must not confirm an older pending order."""
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

from langgraph.checkpoint.memory import MemorySaver

from stockpilot.application.dto.intent_result import IntentResult
from stockpilot.application.use_cases.catalogue_advice import CatalogueAdviceUseCase
from stockpilot.application.use_cases.check_stock import StockResult
from stockpilot.application.use_cases.finalize_order import FinalizeOrderUseCase
from stockpilot.application.use_cases.prepare_order import PrepareOrderUseCase
from stockpilot.application.workflows.stockpilot_workflow import build_graph
from stockpilot.domain.enums.intent import Intent
from stockpilot.domain.enums.order_status import OrderStatus
from test_order_conversation import invoke


def test_advice_clarification_disarms_an_older_pending_confirmation():
    orders, reservations = [], []

    class InMemoryTransaction:
        async def execute(self, order, *, replay_only=False):
            if replay_only:
                return None
            reservations.append(order.quantity)
            orders.append(order)
            return replace(order, status=OrderStatus.CREATED)

    classifier = SimpleNamespace(execute=AsyncMock(side_effect=lambda message:
        IntentResult(intent=Intent.ORDER, product_name='Bosch X100', quantity=3)
        if message.startswith('Commande') else IntentResult(intent=Intent.PRODUCT_ADVICE)))
    stock = SimpleNamespace(execute=AsyncMock(return_value=StockResult(
        'BOSCH-X100', 'Bosch X100', 12, True, '507f1f77bcf86cd799439011')))
    advice = CatalogueAdviceUseCase(
        SimpleNamespace(find_advice_candidates=AsyncMock()),
        SimpleNamespace(generate=AsyncMock()))
    graph = build_graph(classifier, stock, SimpleNamespace(execute=AsyncMock()),
        PrepareOrderUseCase(stock), FinalizeOrderUseCase(InMemoryTransaction()),
        checkpointer=MemorySaver(), get_order_status=SimpleNamespace(execute=AsyncMock()),
        catalogue_advice=advice)
    invoke(graph, 'Commande-moi 3 Bosch X100', 'advice-clarification')
    clarified = invoke(graph, 'Conseille-moi une perceuse dans mon budget', 'advice-clarification')
    assert clarified['advice_status'] == 'CLARIFICATION_REQUIRED'
    assert clarified['pending_order'] is None, 'A budget clarification must disarm the older order'
    assert clarified['active_order_confirmation'] is None
    invoke(graph, 'oui', 'advice-clarification')
    assert not orders and not reservations
