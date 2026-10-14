"""Conversation/HTTP regressions observed in V12.10, without any live database."""
import asyncio
import json
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from customer_auth import configure_customer_auth, customer_headers
from test_order_conversation import make_workflow, invoke
from stockpilot.application.dto.intent_result import IntentResult
from stockpilot.application.order_transaction_errors import OrderOutcomeUnknownError
from stockpilot.application.workflows.nodes import create_order_confirmation_node
from stockpilot.application.workflows.stockpilot_workflow import build_graph
from stockpilot.application.use_cases.prepare_order import PrepareOrderUseCase
from stockpilot.domain.enums.intent import Intent
from stockpilot.domain.enums.order_status import OrderStatus
from stockpilot.main import app


@pytest.mark.parametrize("quantity", [0, -1, -37, 1.5, True])
def test_invalid_quantity_http_is_explicit_refusal_without_mutation(monkeypatch, quantity):
    workflow, classifier, _, repository, products, reservations, _ = make_workflow()
    # JSON extraction must retain the actual invalid value (not int(1.5) or int(True)).
    classification = IntentResult.model_validate_json(
        json.dumps({'intent': 'ORDER', 'product_name': 'Bosch X100', 'quantity': quantity}))
    assert type(classification.quantity) is type(quantity)
    classifier.execute.side_effect = None
    classifier.execute.return_value = classification
    configure_customer_auth(monkeypatch, app)
    monkeypatch.setattr(app.state, 'stockpilot_workflow', workflow)
    before = deepcopy(products)
    response = TestClient(app).post('/assistant', headers=customer_headers(),
        json={'thread_id': 'invalid', 'message': f'Commande-moi {quantity} Bosch X100'})
    assert response.status_code == 200
    state = response.json()
    assert state['order_status'] == 'INVALID_QUANTITY'
    assert state['pending_order'] is None and state['created_order_id'] is None
    assert state['last_product_sku'] is None and state['last_product_document_id'] is None
    assert 'entier strictement positif' in state['response']
    assert products == before and repository.orders == [] and reservations == []


def test_invalid_followup_clears_preparation_without_confirming_old_quantity():
    workflow, classifier, _, repository, products, reservations, _ = make_workflow()
    invoke(workflow, 'Commande-moi 3 Bosch X100', 'followup')
    before = deepcopy(products)
    classifier.execute.side_effect = None
    classifier.execute.return_value = IntentResult(intent=Intent.ORDER, product_name='Bosch X100', quantity=-3)
    rejected = invoke(workflow, 'Commande-moi -3 Bosch X100', 'followup')
    assert rejected['pending_order'] is None and rejected['order_status'] == 'INVALID_QUANTITY'
    classifier.execute.return_value = IntentResult(intent=Intent.UNKNOWN)
    after = invoke(workflow, 'oui', 'followup')
    assert after['pending_order'] is None
    assert products == before and not repository.orders and not reservations


@pytest.mark.parametrize('error', [ValueError('programming fault'), RuntimeError('internal failure')])
def test_untyped_internal_error_remains_http_500(monkeypatch, error):
    configure_customer_auth(monkeypatch, app)
    classifier = SimpleNamespace(execute=AsyncMock(return_value=IntentResult(
        intent=Intent.ORDER, product_name='Bosch X100', quantity=1)))
    stock = SimpleNamespace(execute=AsyncMock(side_effect=error))
    finalizer = SimpleNamespace(execute=AsyncMock())
    workflow = build_graph(classifier, stock, SimpleNamespace(execute=AsyncMock()),
        PrepareOrderUseCase(stock), finalizer, get_order_status=SimpleNamespace(execute=AsyncMock()))
    monkeypatch.setattr(app.state, 'stockpilot_workflow', workflow)
    response = TestClient(app, raise_server_exceptions=False).post('/assistant', headers=customer_headers(),
        json={'thread_id': 'internal', 'message': 'Commande-moi 1 Bosch X100'})
    assert response.status_code == 500 and response.json()['detail'] == 'Internal server error'
    finalizer.execute.assert_not_awaited()


@pytest.mark.parametrize('quantity,status', [(None, 'PENDING_CONFIRMATION'), (2, 'PENDING_CONFIRMATION'),
                                          (200, 'INSUFFICIENT_STOCK')])
def test_absent_valid_and_excessive_quantity_preserve_contract(quantity, status):
    workflow, classifier, _, repository, products, reservations, _ = make_workflow()
    before = deepcopy(products)
    classifier.execute.side_effect = None
    classifier.execute.return_value = IntentResult(intent=Intent.ORDER, product_name='Bosch X100', quantity=quantity)
    message = 'Commande-moi Bosch X100' if quantity is None else f'Commande-moi {quantity} Bosch X100'
    state = invoke(workflow, message, 'quantity')
    assert state['order_status'] == status
    if status == 'PENDING_CONFIRMATION':
        assert state['pending_order']['quantity'] == (1 if quantity is None else quantity)
    else:
        assert state['pending_order'] is None
    assert products == before and not repository.orders and not reservations


@pytest.mark.parametrize('message', ['oui', 'oui confirme', 'oui, confirme', 'OUI CONFIRME !',
                                   'oui je confirme', 'je confirme', 'confirme', "c'est bon", 'ok je confirme'])
def test_recognized_confirmation_and_immediate_replay_preserve_identity(message):
    workflow, classifier, _, repository, products, reservations, _ = make_workflow()
    prepared = invoke(workflow, 'Commande-moi 3 Bosch X100', 'confirm')
    first = invoke(workflow, message, 'confirm')
    before = deepcopy(products)
    replay = invoke(workflow, message, 'confirm')
    assert first['order_status'] == replay['order_status'] == 'CONFIRMED'
    assert replay['created_order_id'] == first['created_order_id']
    assert replay['pending_order']['operation_id'] == prepared['pending_order']['operation_id']
    assert 'déjà confirmée' in replay['response'] and 'Aucune nouvelle réservation' in replay['response']
    assert classifier.execute.await_count == 1
    assert products == before and len(repository.orders) == 1 and reservations == [('Bosch X100', 3)]


def test_unrelated_message_and_new_order_do_not_replay_previous_operation():
    workflow, classifier, _, repository, _, reservations, _ = make_workflow()
    invoke(workflow, 'Commande-moi 3 Bosch X100', 'new')
    first = invoke(workflow, 'oui', 'new')
    unrelated = invoke(workflow, 'bonjour', 'new')
    assert unrelated['confirmation_handled'] is False and unrelated['intent'] == 'UNKNOWN'
    new = invoke(workflow, 'Commande-moi Makita D200', 'new')
    assert new['order_status'] == 'PENDING_CONFIRMATION'
    assert new['pending_order']['operation_id'] != first['pending_order']['operation_id']
    assert new['pending_order']['product_name'] == 'Makita D200'
    assert len(repository.orders) == 1 and reservations == [('Bosch X100', 3)]


def terminal_fixture():
    workflow, _, _, repository, _, _, _ = make_workflow()
    invoke(workflow, 'Commande-moi 3 Bosch X100', 'terminal')
    state = invoke(workflow, 'oui', 'terminal')
    finalizer = SimpleNamespace(execute=AsyncMock(return_value=repository.orders[0]))
    return state, finalizer


def test_restored_confirmed_checkpoint_concurrent_replays_are_recovery_only():
    state, finalizer = terminal_fixture()
    async def run():
        # New nodes consume independently restored snapshots; no writer is exposed.
        return await asyncio.gather(*[create_order_confirmation_node(finalizer)(deepcopy(state)) for _ in range(2)])
    results = asyncio.run(run())
    assert all(result['created_order_id'] == state['created_order_id'] for result in results)
    assert all(result['pending_order']['operation_id'] == state['pending_order']['operation_id'] for result in results)
    assert all(call.kwargs['replay_only'] is True for call in finalizer.execute.await_args_list)


@pytest.mark.parametrize('message', ['oui pour une autre commande', 'oui commande 2 Makita D200',
                                   'confirme operation OTHER-001', 'non', 'bonjour'])
def test_bounded_confirmation_does_not_associate_another_message(message):
    state, finalizer = terminal_fixture()
    result = asyncio.run(create_order_confirmation_node(finalizer)({**state, 'message': message}))
    assert result == {'confirmation_handled': False}
    finalizer.execute.assert_not_awaited()


@pytest.mark.parametrize('status', ['ORDER_OUTCOME_UNKNOWN', 'RESERVATION_FAILED',
                                   'ORDER_CREATION_FAILED', 'PRODUCT_IDENTITY_REQUIRED', 'CANCELLED'])
def test_uncertain_or_failed_state_cannot_reenter_confirmation(status):
    state, finalizer = terminal_fixture()
    state['order_status'] = status
    state['pending_order']['status'] = status
    result = asyncio.run(create_order_confirmation_node(finalizer)(state))
    assert result == {'confirmation_handled': False}
    finalizer.execute.assert_not_awaited()


@pytest.mark.parametrize('outcome', ['missing', 'unknown', 'cancelled', 'wrong_order', 'wrong_operation', 'wrong_owner'])
def test_terminal_replay_failures_never_claim_success_or_reserve(outcome):
    state, finalizer = terminal_fixture()
    if outcome == 'missing':
        finalizer.execute.return_value = None
    elif outcome == 'unknown':
        finalizer.execute.side_effect = OrderOutcomeUnknownError('unavailable read')
    elif outcome == 'cancelled':
        finalizer.execute.return_value = replace(finalizer.execute.return_value, status=OrderStatus.CANCELLED)
    elif outcome == 'wrong_order':
        finalizer.execute.return_value = replace(finalizer.execute.return_value, order_id='other-order')
    elif outcome == 'wrong_operation':
        finalizer.execute.return_value = replace(finalizer.execute.return_value, operation_id='other-operation')
    elif outcome == 'wrong_owner':
        state['customer_id'] = 'another-customer'
    if outcome in ('wrong_order', 'wrong_operation', 'wrong_owner'):
        with pytest.raises(ValueError):
            asyncio.run(create_order_confirmation_node(finalizer)(state))
        if outcome == 'wrong_owner':
            finalizer.execute.assert_not_awaited()
    else:
        result = asyncio.run(create_order_confirmation_node(finalizer)(state))
        assert result['order_status'] == ('CANCELLED' if outcome == 'cancelled' else 'ORDER_OUTCOME_UNKNOWN')
        assert 'déjà confirmée' not in result['response']
        assert finalizer.execute.await_args.kwargs['replay_only'] is True


def test_confirmed_replay_after_status_of_another_order_clears_stale_identity():
    workflow, _, _, repository, products, reservations, _ = make_workflow()
    invoke(workflow, 'Commande-moi 3 Bosch X100', 'first')
    first = invoke(workflow, 'oui', 'first')
    invoke(workflow, 'Commande-moi Makita D200', 'second')
    second = invoke(workflow, 'oui', 'second')
    queried = invoke(workflow, f"Statut commande {second['created_order_id']}", 'first')
    assert queried['intent'] == Intent.ORDER_STATUS
    assert queried['queried_order_id'] == second['created_order_id']
    before = deepcopy(products)
    reservation_count = len(reservations)
    replay = invoke(workflow, 'oui confirme', 'first')
    assert replay['intent'] == Intent.ORDER and replay['confirmation_handled'] is True
    assert replay['order_status_result'] is None
    assert replay['queried_order_id'] is None and replay['queried_order_status'] is None
    assert replay['created_order_id'] == first['created_order_id']
    assert replay['pending_order']['operation_id'] == first['pending_order']['operation_id']
    assert first['created_order_id'] in replay['response']
    assert second['created_order_id'] not in replay['response']
    assert products == before and len(reservations) == reservation_count
    assert len(repository.orders) == 2
