"""Independent adversarial checks for the bounded V12.38 normalizers."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from stockpilot.application.dto.intent_result import IntentResult
from stockpilot.application.use_cases.classify_intent import ClassifyIntentUseCase
from stockpilot.application.use_cases.resolve_order_reference import ResolveOrderReferenceUseCase
from stockpilot.domain.enums.intent import Intent


@pytest.mark.parametrize('message', [
    'Commande -6 Vis Synthétiques V016',
    'Commande −6 Vis Synthétiques V016',
    'Commande moins six Vis Synthétiques V016',
    'Commande une demi-douzaine de vis ou six vis',
    'Commande une demi-douzaine de vis puis 12 vis',
    'Commande Produit-Demi-Douzaine-001',
])
def test_independent_lexical_authority_does_not_repair_invalid_user_counts(message):
    raw = IntentResult(intent=Intent.ORDER, quantity=-6)
    llm = SimpleNamespace(classify_intent=AsyncMock(return_value=raw))
    result = asyncio.run(ClassifyIntentUseCase(llm).execute(message))
    assert result.quantity == -6


@pytest.mark.parametrize('count', ['2', '+2', '0002'])
def test_independent_counted_reference_uses_full_span_and_exact_namespaces(count):
    sku = 'REFERENCE-12-34-56'
    products = SimpleNamespace(find_resolution_skus=AsyncMock(return_value=[SimpleNamespace(sku=sku)]))
    orders = SimpleNamespace(find_by_order_id_and_customer_id=AsyncMock(return_value=None))
    result = asyncio.run(ResolveOrderReferenceUseCase(products, orders).execute(f'Commande {count} {sku}', 'owner'))
    assert result.intent == Intent.ORDER and result.reference == sku
    products.find_resolution_skus.assert_awaited_once_with(sku)
    orders.find_by_order_id_and_customer_id.assert_awaited_once_with(sku, 'owner')


def test_independent_counted_route_rejects_non_owned_repository_result():
    sku = 'REFERENCE-12-34-56'
    products = SimpleNamespace(find_resolution_skus=AsyncMock(return_value=[SimpleNamespace(sku=sku)]))
    orders = SimpleNamespace(find_by_order_id_and_customer_id=AsyncMock(return_value=SimpleNamespace(order_id=sku, customer_id='foreign')))
    with pytest.raises(ValueError, match='Non-owned'):
        asyncio.run(ResolveOrderReferenceUseCase(products, orders).execute(f'Commande 2 {sku}', 'owner'))
