"""Actual V12.37 classifications replayed without trusting model quantities/routes."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from stockpilot.application.dto.intent_result import IntentResult
from stockpilot.application.use_cases.classify_intent import ClassifyIntentUseCase
from stockpilot.application.use_cases.resolve_order_reference import ResolveOrderReferenceUseCase
from stockpilot.application.workflows.nodes import create_classify_node
from stockpilot.domain.enums.intent import Intent

A='Commande une demi-douzaine de Vis Synthétiques V016'
B='Commande 999 DEMO-CONC-0001'
RAW_A='{"intent":"ORDER","product_name":"Vis Synthétiques V016","quantity":-6}'
RAW_B='{"intent":"ORDER_STATUS","order_id":"999 DEMO-CONC-0001","quantity":null}'

def test_actual_raw_negative_lexical_classification_uses_user_evidence():
    raw=IntentResult.model_validate_json(RAW_A)
    llm=SimpleNamespace(classify_intent=AsyncMock(return_value=raw))
    normalized=asyncio.run(ClassifyIntentUseCase(llm).execute(A))
    assert raw.quantity == -6  # preserve evidence of what the model returned
    assert normalized.quantity == 6
    assert normalized.product_name == raw.product_name


def test_actual_raw_status_classification_enters_exact_product_order():
    p=SimpleNamespace(find_resolution_skus=AsyncMock(return_value=[SimpleNamespace(sku='DEMO-CONC-0001')]))
    o=SimpleNamespace(find_by_order_id_and_customer_id=AsyncMock(return_value=None))
    llm=SimpleNamespace(classify_intent=AsyncMock(return_value=IntentResult.model_validate_json(RAW_B)))
    node=create_classify_node(ClassifyIntentUseCase(llm),ResolveOrderReferenceUseCase(p,o))
    result=asyncio.run(node({'message':B,'customer_id':'synthetic-owner'}))
    assert result['intent']==Intent.ORDER
    assert result['order_id'] is None and result['product_name']=='DEMO-CONC-0001'
    o.find_by_order_id_and_customer_id.assert_awaited_once_with('DEMO-CONC-0001','synthetic-owner')

@pytest.mark.parametrize('phrase,expected',[
 ('une demi-douzaine de',6),('une demi douzaine de',6),('demi-douzaine de',6),
 ('une douzaine de',12),('douzaine de',12),('une  demi-douzaine de',6),
])
@pytest.mark.parametrize('extracted',[-6,1,12,None])
def test_supported_lexical_count_is_proven_not_model_authored(phrase,expected,extracted):
    raw=IntentResult(intent=Intent.ORDER,quantity=extracted,product_name='Vis Synthétiques V016')
    result=asyncio.run(ClassifyIntentUseCase(SimpleNamespace(classify_intent=AsyncMock(return_value=raw))).execute('Commande '+phrase+' Vis Synthétiques V016'))
    assert result.quantity==expected and raw.quantity==extracted

@pytest.mark.parametrize('message',[
 'Commande -6 Vis Synthétiques V016','Commande moins six vis','Commande 6 vis',
 'Commande 1.5 vis','Commande 1,5 vis','Commande 1.5abc vis',
 'Commande une paire de vis','Commande un lot de vis',
 'Commande une demi-douzaine de vis ou 12 vis','Commande une demi-douzaine de vis 20',
 'Commande DEMI-DOUZAINE-001','Commande une "demi-douzaine de vis"',
 'plutôt une demi-douzaine',
])
def test_no_general_rewrite_of_digit_or_uncertain_quantities(message):
    raw=IntentResult(intent=Intent.ORDER,quantity=-6)
    result=asyncio.run(ClassifyIntentUseCase(SimpleNamespace(classify_intent=AsyncMock(return_value=raw))).execute(message))
    assert result.quantity==-6

@pytest.mark.parametrize('kind', ['product','order','collision','unknown','foreign','duplicates'])
@pytest.mark.parametrize('reference',['DEMO-CONC-0001','ABC-123','550e8400-e29b-41d4-a716-446655440000'])
def test_counted_frame_resolves_namespaces_without_read_write_confusion(kind,reference):
    products=[SimpleNamespace(sku=reference)]*(2 if kind=='duplicates' else 1) if kind in {'product','collision','duplicates'} else []
    order=SimpleNamespace(order_id=reference,customer_id='owner') if kind in {'order','collision'} else None
    p=SimpleNamespace(find_resolution_skus=AsyncMock(return_value=products));o=SimpleNamespace(find_by_order_id_and_customer_id=AsyncMock(return_value=order))
    result=asyncio.run(ResolveOrderReferenceUseCase(p,o).execute('Commande 999 '+reference,'owner'))
    assert result.intent==(Intent.ORDER if kind=='product' else Intent.UNKNOWN)
    o.find_by_order_id_and_customer_id.assert_awaited_once_with(reference,'owner')

@pytest.mark.parametrize('message',[
 'Montre-moi la commande ORD-TEST-001','Statut de la commande ORD-TEST-001',
 'Où en est la commande 999 ?','Commande 2 DEMO-CONC-0001 extra',
 'Commande -6 DEMO-CONC-0001','Commande 0 DEMO-CONC-0001',
 'Commande 1.5 DEMO-CONC-0001','Commande 1,5 DEMO-CONC-0001',
 'Commande 1.5abc DEMO-CONC-0001',
])
def test_outside_complete_valid_counted_frame_is_not_reinterpreted(message):
    p=SimpleNamespace(find_resolution_skus=AsyncMock());o=SimpleNamespace(find_by_order_id_and_customer_id=AsyncMock())
    assert asyncio.run(ResolveOrderReferenceUseCase(p,o).execute(message,'owner')) is None
    p.find_resolution_skus.assert_not_awaited();o.find_by_order_id_and_customer_id.assert_not_awaited()


def test_explicit_positive_six_keeps_normal_concordance():
    from stockpilot.application.order_quantity_evidence import concordant_quantity
    message='Commande 6 vis'
    raw=IntentResult(intent=Intent.ORDER,quantity=6,product_name='vis')
    normalized=asyncio.run(ClassifyIntentUseCase(SimpleNamespace(classify_intent=AsyncMock(return_value=raw))).execute(message))
    assert normalized.quantity==6
    assert concordant_quantity(message,normalized.quantity).quantity==6
    # A generic product word still requires the ordinary strict resolution.
