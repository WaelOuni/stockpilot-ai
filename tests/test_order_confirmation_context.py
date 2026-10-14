"""A persisted preparation is not authorization after a conversation branch."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from stockpilot.application.dto.intent_result import IntentResult
from stockpilot.application.workflows.nodes import create_order_confirmation_node
from stockpilot.domain.enums.intent import Intent
from test_order_conversation import invoke, make_workflow


@pytest.mark.parametrize('confirmation', ['oui', 'oui confirme', 'Oui, je confirme', "c'est bon"])
def test_active_exact_proposal_confirms_once(confirmation):
    graph, _, _, orders, products, reservations, _ = make_workflow()
    prepared = invoke(graph, 'Commande-moi 3 Bosch X100', 'active')
    proposal = prepared['active_order_confirmation']
    assert proposal['operation_id'] == prepared['pending_order']['operation_id']
    assert proposal['product_document_id'] == products['Bosch X100'].document_id
    confirmed = invoke(graph, confirmation, 'active')
    assert confirmed['active_order_confirmation'] is None
    assert confirmed['pending_order']['operation_id'] == proposal['operation_id']
    assert confirmed['order_status'] == 'CONFIRMED' and 'créée' in confirmed['response']
    assert len(orders.orders) == 1 and reservations == [('Bosch X100', 3)]


@pytest.mark.parametrize('branch', ['Conseille-moi une perceuse', 'Est-elle disponible ?', 'Bonjour'])
@pytest.mark.parametrize('confirmation', ['oui', 'oui confirme'])
def test_conversation_branch_disarms_pending_order(branch, confirmation):
    graph, _, _, orders, products, reservations, _ = make_workflow()
    invoke(graph, 'Commande-moi 3 Bosch X100', 'branch')
    before = deepcopy(products)
    branched = invoke(graph, branch, 'branch')
    assert branched['pending_order'] is None
    assert branched['active_order_confirmation'] is None
    after = invoke(graph, confirmation, 'branch')
    assert after['confirmation_handled'] is False
    assert 'créée' not in after['response']
    assert not orders.orders and not reservations and products == before


def test_ambiguous_order_change_disarms_and_fresh_preparation_rearms():
    graph, classifier, _, orders, products, reservations, _ = make_workflow()
    old = invoke(graph, 'Commande-moi 3 Bosch X100', 'modify')
    classifier.execute.side_effect = None
    classifier.execute.return_value = IntentResult(intent=Intent.UNKNOWN)
    rejected = invoke(graph, 'plutôt 1.5 ou 2', 'modify')
    assert rejected['pending_order'] is None and rejected['active_order_confirmation'] is None
    assert 'quantité' in rejected['response']
    invoke(graph, 'oui', 'modify')
    assert not orders.orders and not reservations
    classifier.execute.return_value = IntentResult(intent=Intent.ORDER, product_name='Bosch X100', quantity=2)
    new = invoke(graph, 'Commande-moi 2 Bosch X100', 'modify')
    assert new['active_order_confirmation']['operation_id'] != old['active_order_confirmation']['operation_id']
    confirmed = invoke(graph, 'oui confirme', 'modify')
    assert confirmed['pending_order']['operation_id'] == new['pending_order']['operation_id']
    assert reservations == [('Bosch X100', 2)] and len(orders.orders) == 1


def test_two_preparations_confirm_only_latest_operation_and_product():
    graph, _, _, orders, products, reservations, _ = make_workflow()
    first = invoke(graph, 'Commande-moi 3 Bosch X100', 'two')
    second = invoke(graph, 'Commande-moi Makita D200', 'two')
    assert first['pending_order']['operation_id'] != second['pending_order']['operation_id']
    result = invoke(graph, 'oui', 'two')
    assert result['pending_order']['operation_id'] == second['pending_order']['operation_id']
    assert orders.orders[0].product_document_id == second['pending_order']['product_document_id']
    assert reservations == [('Makita D200', 1)]


def test_advice_then_new_explicit_order_has_a_new_confirmable_proposal():
    graph, _, _, orders, _, reservations, _ = make_workflow()
    old = invoke(graph, 'Commande-moi 3 Bosch X100', 'advice-new')
    invoke(graph, 'Conseille-moi une perceuse', 'advice-new')
    new = invoke(graph, 'Commande-moi Makita D200', 'advice-new')
    assert new['active_order_confirmation']['operation_id'] != old['pending_order']['operation_id']
    invoke(graph, 'oui confirme', 'advice-new')
    assert len(orders.orders) == 1 and reservations == [('Makita D200', 1)]


def test_explicit_resumption_after_branch_requires_preparation_before_reservation():
    graph, _, _, orders, _, reservations, _ = make_workflow()
    old = invoke(graph, 'Commande-moi 3 Bosch X100', 'resume')
    invoke(graph, 'Bonjour', 'resume')
    new = invoke(graph, 'Commande-moi 3 Bosch X100', 'resume')
    assert new['pending_order']['operation_id'] != old['pending_order']['operation_id']
    assert 'Confirmez-vous' in new['response']
    assert not orders.orders and not reservations


@pytest.mark.parametrize('tamper', ['missing', 'operation_id', 'customer_id', 'product_document_id', 'product_sku', 'product_name', 'quantity', 'intent', 'context'])
def test_restored_proposal_mismatch_can_only_recover_readonly(tamper):
    graph, _, _, _, _, _, _ = make_workflow()
    prepared = invoke(graph, 'Commande-moi 3 Bosch X100', 'proof')
    if tamper == 'missing':
        prepared.pop('active_order_confirmation')
    elif tamper == 'intent':
        prepared['last_relevant_intent'] = Intent.PRODUCT_ADVICE
    elif tamper == 'context':
        prepared['context_customer_id'] = 'other-owner'
    else:
        prepared['active_order_confirmation'][tamper] = 1 if tamper == 'quantity' else 'other'
    finalizer = SimpleNamespace(execute=AsyncMock(return_value=None))
    rejected = asyncio.run(create_order_confirmation_node(finalizer)({**prepared, 'message': 'oui'}))
    assert finalizer.execute.await_args.kwargs['replay_only'] is True
    assert rejected['active_order_confirmation'] is None
    assert 'Aucune nouvelle réservation' in rejected['response']


def test_classification_failure_durably_retires_previous_eligibility():
    graph, classifier, _, orders, products, reservations, _ = make_workflow()
    prepared = invoke(graph, 'Commande-moi 3 Bosch X100', 'failed-provider')
    before = deepcopy(products)
    classifier.execute.side_effect = RuntimeError('classifier unavailable')
    with pytest.raises(RuntimeError, match='classifier unavailable'):
        invoke(graph, 'Conseille-moi une perceuse', 'failed-provider')
    restored = graph.get_state({'configurable': {'thread_id': 'failed-provider'}}).values
    assert restored['active_order_confirmation'] is None
    assert restored['pending_order']['operation_id'] == prepared['pending_order']['operation_id']
    rejected = invoke(graph, 'oui confirme', 'failed-provider')
    assert rejected['order_status'] == 'PRODUCT_IDENTITY_REQUIRED'
    assert products == before and not reservations and not orders.orders


def test_confirmed_order_survives_branch_and_replay_without_new_reservation():
    graph, _, _, orders, products, reservations, _ = make_workflow()
    invoke(graph, 'Commande-moi 3 Bosch X100', 'finished')
    confirmed = invoke(graph, 'oui', 'finished')
    before = deepcopy(products)
    invoke(graph, 'Conseille-moi une perceuse', 'finished')
    replay = invoke(graph, 'oui confirme', 'finished')
    assert replay['created_order_id'] == confirmed['created_order_id']
    assert replay['pending_order']['operation_id'] == confirmed['pending_order']['operation_id']
    assert 'déjà confirmée' in replay['response']
    assert products == before and len(orders.orders) == 1 and reservations == [('Bosch X100', 3)]


def test_status_consultation_preserves_pending_record_but_not_eligibility():
    graph, _, _, orders, products, reservations, _ = make_workflow()
    prepared = invoke(graph, 'Commande-moi 3 Bosch X100', 'status')
    queried = invoke(graph, 'Où en est ma commande ?', 'status')
    assert queried['pending_order'] == prepared['pending_order']
    assert queried['active_order_confirmation'] is None
    before = deepcopy(products)
    rejected = invoke(graph, 'oui', 'status')
    assert rejected['order_status'] == 'PRODUCT_IDENTITY_REQUIRED'
    assert not orders.orders and not reservations and products == before
