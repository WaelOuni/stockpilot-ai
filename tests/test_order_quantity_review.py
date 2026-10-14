"""V12.23 independent review regressions; failures block publication."""
from copy import deepcopy

import pytest

from test_order_conversation import make_workflow, invoke
from stockpilot.application.dto.intent_result import IntentResult
from stockpilot.application.order_quantity_errors import InvalidOrderQuantityError
from stockpilot.application.order_quantity_evidence import concordant_quantity
from stockpilot.domain.enums.intent import Intent


@pytest.mark.parametrize('message', ['plutôt 1.5 ou 2', 'plutôt 2 ou 3'])
def test_ambiguous_quantity_followup_disarms_old_preparation(message):
    workflow, classifier, _, repository, products, reservations, _ = make_workflow()
    invoke(workflow, 'Commande-moi 3 Bosch X100', 'review-ambiguity')
    before = deepcopy(products)
    classifier.execute.side_effect = None
    classifier.execute.return_value = IntentResult(intent=Intent.UNKNOWN)
    rejected = invoke(workflow, message, 'review-ambiguity')
    assert rejected['pending_order'] is None, 'An ambiguous quantity must disarm the old pending order'
    after = invoke(workflow, 'oui confirme', 'review-ambiguity')
    assert after['pending_order'] is None
    assert products == before and not repository.orders and not reservations


@pytest.mark.parametrize('count', ['une douzaine', 'une paire'])
def test_compound_count_cannot_be_replaced_by_article_one(count):
    with pytest.raises(InvalidOrderQuantityError):
        concordant_quantity(f'Commande {count} de DEMO-DRILL-0002', 1)


@pytest.mark.parametrize('token', ['1.5abc', '1.5-DEMO-DRILL-0002'])
def test_numeric_token_is_never_partially_consumed(token):
    with pytest.raises(InvalidOrderQuantityError):
        concordant_quantity(f'Commande {token} DEMO-DRILL-0002', 1)


def test_common_written_length_is_a_specification_not_a_second_count():
    assert concordant_quantity('Commande 3 câbles de 2 mètres', 3).quantity == 3
