"""Quantity provenance, including the real V12.20 message with a wrong extractor."""
import asyncio
from types import SimpleNamespace
from copy import deepcopy
import pytest
from unittest.mock import AsyncMock
from fastapi.testclient import TestClient
from customer_auth import configure_customer_auth, customer_headers
from test_order_conversation import make_workflow
from stockpilot.application.dto.intent_result import IntentResult
from stockpilot.domain.enums.intent import Intent
from stockpilot.main import app


def test_real_decimal_message_with_truncated_extraction_is_not_prepared(monkeypatch):
    workflow, classifier, _, repository, products, reservations, _ = make_workflow()
    classifier.execute = AsyncMock(return_value=IntentResult(
        intent=Intent.ORDER, product_name='Bosch X100', quantity=1))
    configure_customer_auth(monkeypatch, app)
    monkeypatch.setattr(app.state, 'stockpilot_workflow', workflow)
    before = deepcopy(products)
    response = TestClient(app).post('/assistant', headers=customer_headers(), json={
        'thread_id': 'real-fraction', 'message': 'Je veux commander 1.5 DEMO-DRILL-0002'})
    assert response.status_code == 200
    assert response.json()['order_status'] == 'INVALID_QUANTITY'
    assert response.json()['pending_order'] is None
    assert products == before and not repository.orders and not reservations

from test_order_conversation import invoke
from stockpilot.application.order_quantity_evidence import (
    concordant_quantity, validate_evidence,
)
from stockpilot.application.order_quantity_errors import InvalidOrderQuantityError
from stockpilot.application.workflows.nodes import create_order_confirmation_node


@pytest.mark.parametrize('lexeme', ['1.5', '1,5', '2.75', '2,75', '1.0', '1,0',
    '0', '-1', '-37', '−2', '1e3', '1e-1', '1 000', '1\u202f000', '1-2',
    'un et demi', 'une demi-unité', 'deux mille', 'une dizaine', 'plusieurs',
    'onze', 'vingt-et-un', '1 ½',
    '999999999999999999999999999999'])
def test_source_invalid_even_if_model_invents_one(lexeme):
    with pytest.raises(InvalidOrderQuantityError):
        concordant_quantity(f'Je veux commander {lexeme} DEMO-DRILL-0002', 1)


@pytest.mark.parametrize('message,quantity', [
    ('Je veux commander 2 DEMO-DRILL-0002', 2),
    ('Je veux commander DEMO-DRILL-0002', 1),
    ('Je veux commander une perceuse 18V', 1),
    ('Je veux commander deux perceuses 18V', 2),
    ('Je veux commander 2 DEMO-18-230-0002', 2),
    ('Je veux commander DEMO-18-230-0002', 1),
    ('Je veux commander 2 perceuses 18V 2 Ah', 2),
    ('Je veux commander 2 perceuses 18V et 2 Ah', 2),
    ('Je veux commander une perceuse 230V et 20 mm', 1),
    ('Commandez-moi deux DEMO-DRILL-0002', 2),
    ('Je veux commander 2 forets 20 mm 230V', 2),
    ('Je veux commander 2 forets 1,5 mm', 2),
    ('Peux-tu me commander "Scie Ambigue V012" ?', 1),
    ('PEUX-TU me commander Scie Ambigue V012 ?', 1),
    ("Et si j'en demande 2 ?", 2),
])
def test_counts_and_specs_have_distinct_roles(message, quantity):
    evidence = concordant_quantity(message, quantity)
    assert evidence.quantity == quantity
    assert validate_evidence(evidence.to_dict()) == evidence
    if evidence.start is not None:
        assert message[evidence.start:evidence.end] in ['2', 'une', 'deux']


@pytest.mark.parametrize('message,extracted', [
    ('Je veux commander 2 DEMO-DRILL-0002', 1),
    ('Je veux commander DEMO-DRILL-0002', 2),
    ('Je veux commander une perceuse 18V', 18),
    ('Je veux commander 2 forets 20 mm', 20),
    ('Je veux commander 2 DEMO-DRILL-0002', 2.0),
    ('Je veux commander 2 DEMO-DRILL-0002', True),
    ('Je veux commander 2 DEMO-DRILL-0002', '2'),
    ('Je veux commander 2 DEMO-DRILL-0002', float('nan')),
    ('Je veux commander 2 DEMO-DRILL-0002', float('inf')),
    ('Je veux commander 2 perceuses et 3 scies', 2),
    ('Je veux commander 2 perceuses et trois scies', 2),
    ('Je veux commander 2 ou 3 DEMO-DRILL-0002', 2),
    ('Je veux commander perceuse modèle 20', 20),
    ('Achats : 2 DEMO-DRILL-0002', 2),
])
def test_contradictory_or_ambiguous_extraction_is_not_authority(message, extracted):
    with pytest.raises(InvalidOrderQuantityError):
        concordant_quantity(message, extracted)


@pytest.mark.parametrize('intent', [Intent.ORDER, Intent.UNKNOWN, Intent.STOCK_CHECK, Intent.PRODUCT_ADVICE])
@pytest.mark.parametrize('message', ["Et si j'en commande 1,5 ?", "plutôt -2",
    'Je veux commander 1.5 Bosch X100', "Et si j'en demande 1 000 ?",
    'plutôt un et demi', 'finalement beaucoup'])
def test_invalid_followup_disarms_old_pending_even_with_wrong_classification(intent, message):
    workflow, classifier, _, repository, products, reservations, _ = make_workflow()
    invoke(workflow, 'Commande-moi 3 Bosch X100', 'invalid-followup')
    before = deepcopy(products)
    classifier.execute.side_effect = None
    classifier.execute.return_value = IntentResult(intent=intent, quantity=1)
    rejected = invoke(workflow, message, 'invalid-followup')
    assert rejected['order_status'] == 'INVALID_QUANTITY'
    assert rejected['pending_order'] is None
    classifier.execute.return_value = IntentResult(intent=Intent.UNKNOWN)
    after = invoke(workflow, 'oui confirme', 'invalid-followup')
    assert after['pending_order'] is None
    assert not repository.orders and not reservations and products == before


@pytest.mark.parametrize('quantity', [1.5, 2.0, True, '2', None, 0, -1])
def test_restored_pending_invalid_type_never_calls_finalizer(quantity):
    workflow, _, _, _, _, _, _ = make_workflow()
    pending = invoke(workflow, 'Commande-moi 3 Bosch X100', 'corrupt')
    pending['pending_order']['quantity'] = quantity
    finalizer = SimpleNamespace(execute=AsyncMock())
    result = asyncio.run(create_order_confirmation_node(finalizer)({**pending, 'message': 'oui'}))
    assert result['order_status'] == 'INVALID_QUANTITY'
    assert result['pending_order'] is None and result['confirmation_handled'] is True
    finalizer.execute.assert_not_awaited()


@pytest.mark.parametrize('tamper', ['quantity', 'span', 'message'])
def test_checkpoint_proof_recomputed_before_reservation(tamper):
    workflow, _, _, _, _, _, _ = make_workflow()
    pending = invoke(workflow, 'Commande-moi 3 Bosch X100', 'proof')
    if tamper == 'quantity':
        pending['pending_order']['quantity'] = 1
    elif tamper == 'span':
        pending['pending_order']['quantity_evidence']['start'] = 0
    else:
        pending['pending_order']['quantity_evidence']['message'] = 'Commande-moi 1.5 Bosch X100'
    finalizer = SimpleNamespace(execute=AsyncMock())
    result = asyncio.run(create_order_confirmation_node(finalizer)({**pending, 'message': 'oui'}))
    assert result['order_status'] == 'INVALID_QUANTITY'
    finalizer.execute.assert_not_awaited()


def test_legacy_unproven_pending_only_reconciles_readonly():
    workflow, _, _, _, _, _, _ = make_workflow()
    pending = invoke(workflow, 'Commande-moi 3 Bosch X100', 'legacy')
    pending['pending_order'].pop('quantity_evidence')
    finalizer = SimpleNamespace(execute=AsyncMock(return_value=None))
    result = asyncio.run(create_order_confirmation_node(finalizer)({**pending, 'message': 'oui'}))
    assert finalizer.execute.await_args.kwargs['replay_only'] is True
    assert result['order_status'] == 'PRODUCT_IDENTITY_REQUIRED'
    assert 'quantité' in result['response']


def test_new_correct_order_after_refusal_can_confirm_and_replay():
    workflow, classifier, _, repository, products, reservations, _ = make_workflow()
    classifier.execute.side_effect = None
    classifier.execute.return_value = IntentResult(intent=Intent.ORDER, product_name='Bosch X100', quantity=1)
    assert invoke(workflow, 'Commande-moi 1.5 Bosch X100', 'recover')['pending_order'] is None
    classifier.execute.return_value = IntentResult(intent=Intent.ORDER, product_name='Bosch X100', quantity=2)
    pending = invoke(workflow, 'Commande-moi 2 Bosch X100', 'recover')
    first = invoke(workflow, 'oui confirme', 'recover')
    before = deepcopy(products)
    replay = invoke(workflow, 'oui confirme', 'recover')
    assert replay['created_order_id'] == first['created_order_id']
    assert replay['pending_order']['operation_id'] == pending['pending_order']['operation_id']
    assert len(repository.orders) == 1 and reservations == [('Bosch X100', 2)]
    assert products == before


def test_new_decimal_request_never_inherits_previous_default_proof():
    workflow, classifier, _, repository, _, reservations, _ = make_workflow()
    classifier.execute.side_effect = None
    classifier.execute.return_value = IntentResult(intent=Intent.ORDER, product_name='Bosch X100')
    assert invoke(workflow, 'Commande-moi Bosch X100', 'default-proof')['pending_order']['quantity'] == 1
    rejected = invoke(workflow, 'Commande-moi 1.5 Bosch X100', 'default-proof')
    assert rejected['pending_order'] is None and rejected['order_status'] == 'INVALID_QUANTITY'
    assert not repository.orders and not reservations


def test_default_count_checkpoint_scalar_corruption_cannot_be_inherited():
    from test_context_resolution import setup_workflow, invoke as send
    workflow, classifier, _, repository, _, reservations = setup_workflow()
    initial = send(workflow, 'Je veux commander 2')
    config = {'configurable': {'thread_id': 'continuity'}}
    asyncio.run(workflow.aupdate_state(config, {'last_quantity': 1.5}))
    result = send(workflow, 'de Bosch X100')
    assert result['order_status'] == 'INVALID_QUANTITY' and result['pending_order'] is None
    assert not repository.orders and not reservations
