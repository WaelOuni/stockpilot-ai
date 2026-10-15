"""Bare commande/reference must not silently confuse the two namespaces."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from stockpilot.application.dto.intent_result import IntentResult
from stockpilot.application.workflows.nodes import create_classify_node
from stockpilot.domain.enums.intent import Intent
from stockpilot.application.use_cases.resolve_order_reference import ResolveOrderReferenceUseCase


def test_observed_product_reference_is_not_an_order_lookup():
    classifier = SimpleNamespace(execute=AsyncMock(return_value=IntentResult(
        intent=Intent.ORDER_STATUS, order_id='DEMO-DRILL-0002')))
    products = SimpleNamespace(find_resolution_skus=AsyncMock(return_value=[SimpleNamespace(sku='DEMO-DRILL-0002')]))
    orders = SimpleNamespace(find_by_order_id_and_customer_id=AsyncMock(return_value=None))
    node = create_classify_node(classifier, ResolveOrderReferenceUseCase(products, orders))
    result = asyncio.run(node({'message':'Commande DEMO-DRILL-0002', 'customer_id':'synthetic'}))
    assert result['intent'] == Intent.ORDER
    assert result['order_id'] is None

import pytest
from stockpilot.application.workflows.stockpilot_workflow import build_graph
from stockpilot.application.use_cases.check_stock import CheckStockUseCase
from stockpilot.application.use_cases.prepare_order import PrepareOrderUseCase
from stockpilot.application.use_cases.resolve_product import ResolveProductUseCase
from stockpilot.application.use_cases.get_order_status import GetOrderStatusUseCase
from stockpilot.domain.entities.product import Product
from stockpilot.domain.enums.product_category import ProductCategory
from langgraph.checkpoint.memory import MemorySaver


def ports(skus=(), order=None):
    products = SimpleNamespace(
        find_resolution_skus=AsyncMock(return_value=[SimpleNamespace(sku=s) for s in skus]))
    orders = SimpleNamespace(find_by_order_id_and_customer_id=AsyncMock(return_value=order))
    return products, orders


@pytest.mark.parametrize('reference', ['DEMO-DRILL-0002','ABC-123','550e8400-e29b-41d4-a716-446655440000','MODEL-12-34-56'])
@pytest.mark.parametrize('kind', ['product','order','collision','unknown','duplicates','foreign'])
def test_namespaces_have_no_reserved_prefix(reference, kind):
    owned = SimpleNamespace(order_id=reference, customer_id='synthetic')
    p,o = ports([reference]* (2 if kind=='duplicates' else 1) if kind in {'product','collision','duplicates'} else [],
                owned if kind in {'order','collision'} else None)
    result=asyncio.run(ResolveOrderReferenceUseCase(p,o).execute('Commande '+reference,'synthetic'))
    expected = Intent.ORDER if kind=='product' else Intent.ORDER_STATUS if kind=='order' else Intent.UNKNOWN
    assert result.intent == expected
    assert result.reference == (reference if expected != Intent.UNKNOWN else None)
    o.find_by_order_id_and_customer_id.assert_awaited_once_with(reference,'synthetic')


@pytest.mark.parametrize('message', [
    'Je veux commander DEMO-DRILL-0002',
    'Ajoute 2 DEMO-DRILL-0002 à ma commande','Je veux acheter 2 DEMO-DRILL-0002',
    'Montre-moi la commande ORD-TEST-001','Où en est ma commande ORD-TEST-001 ?',
    'Statut de la commande ORD-TEST-001','Retrouve la commande ORD-TEST-001',
    'Cherche le produit DEMO-DRILL-0002','Stock du produit DEMO-DRILL-0002',
    'Confirme la commande','Commande operation_id ABC-123',
    'Commande 1.5 DEMO-DRILL-0002','Commande 1,5 DEMO-DRILL-0002',
    'Commande 1.5abc DEMO-DRILL-0002','Commande une douzaine de Vis Synthétiques V015',
    'Commande 3 câbles de 2 mètres',
])
def test_other_frames_remain_unnormalized(message):
    p,o=ports(['DEMO-DRILL-0002'])
    assert asyncio.run(ResolveOrderReferenceUseCase(p,o).execute(message,'synthetic')) is None
    p.find_resolution_skus.assert_not_awaited()
    o.find_by_order_id_and_customer_id.assert_not_awaited()


@pytest.mark.parametrize('reference',['2','12','deux','une'])
def test_count_without_product_is_not_a_numeric_sku(reference):
    p,o=ports([reference])
    result=asyncio.run(ResolveOrderReferenceUseCase(p,o).execute('Commande '+reference,'synthetic'))
    assert result.intent == Intent.UNKNOWN
    p.find_resolution_skus.assert_not_awaited()


def test_repository_error_is_not_hidden_as_user_clarification():
    p,o=ports()
    p.find_resolution_skus.side_effect=RuntimeError('isolated repository failure')
    with pytest.raises(RuntimeError):
        asyncio.run(ResolveOrderReferenceUseCase(p,o).execute('Commande ABC-123','synthetic'))


def test_missing_owner_never_queries_orders():
    p,o=ports(['ABC-123'])
    result=asyncio.run(ResolveOrderReferenceUseCase(p,o).execute('Commande ABC-123',None))
    assert result.intent == Intent.UNKNOWN
    o.find_by_order_id_and_customer_id.assert_not_awaited()


@pytest.mark.parametrize('intent',list(Intent))
def test_reference_proof_overrides_model_route_but_not_quantity(intent):
    p,o=ports(['DEMO-DRILL-0002'])
    classifier=SimpleNamespace(execute=AsyncMock(return_value=IntentResult(
        intent=intent, quantity=2, order_id='invented', product_name='invented')))
    result=asyncio.run(create_classify_node(classifier,ResolveOrderReferenceUseCase(p,o))(
        {'message':'Commande DEMO-DRILL-0002','customer_id':'synthetic'}))
    assert result['intent']==Intent.ORDER and result['quantity']==2
    assert result['product_name']=='DEMO-DRILL-0002' and result['order_id'] is None


def test_reference_ambiguity_cannot_inherit_an_old_preparation():
    product=Product('DEMO-DRILL-0002','Perceuse Synthétique V015 12V',ProductCategory.DRILL,1.,10,'507f1f77bcf86cd799439011')
    async def find(sku):return [product] if sku==product.sku else []
    p=SimpleNamespace(find_resolution_skus=AsyncMock(side_effect=find))
    o=SimpleNamespace(find_by_order_id_and_customer_id=AsyncMock(return_value=None))
    classifier=SimpleNamespace(execute=AsyncMock(return_value=IntentResult(intent=Intent.ORDER,quantity=3)))
    finalize=SimpleNamespace(execute=AsyncMock())
    graph=build_graph(classifier,CheckStockUseCase(p),SimpleNamespace(execute=AsyncMock()),
        PrepareOrderUseCase(CheckStockUseCase(p),ResolveProductUseCase(p)),finalize,
        checkpointer=MemorySaver(),get_order_status=GetOrderStatusUseCase(o),
        resolve_order_reference=ResolveOrderReferenceUseCase(p,o))
    async def scenario():
        cfg={'configurable':{'thread_id':'isolated'}}
        first=await graph.ainvoke({'message':'Commande 3 DEMO-DRILL-0002','customer_id':'synthetic'},cfg)
        assert first['active_order_confirmation']
        classifier.execute.return_value=IntentResult(intent=Intent.ORDER_STATUS,order_id='ABC-123')
        ambiguous=await graph.ainvoke({'message':'Commande ABC-123','customer_id':'synthetic'},cfg)
        assert ambiguous['intent']==Intent.UNKNOWN and ambiguous['pending_order'] is None
        assert ambiguous['active_order_confirmation'] is None
        assert 'Précisez' in ambiguous['response']
        after=await graph.ainvoke({'message':'oui','customer_id':'synthetic'},cfg)
        assert after['pending_order'] is None and after['active_order_confirmation'] is None
        finalize.execute.assert_not_awaited()
    asyncio.run(scenario())
