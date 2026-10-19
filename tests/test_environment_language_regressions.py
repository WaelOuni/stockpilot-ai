"""Exact 5175 reports, on a small controlled catalogue shared by all cases."""
import asyncio
from unittest.mock import AsyncMock

import pytest

from evals.fixtures.catalogue_advice import MemoryCatalogue, advice_graph
from stockpilot.application.dto.intent_result import IntentResult
from stockpilot.application.order_quantity_evidence import concordant_quantity
from stockpilot.application.order_quantity_evidence import validate_evidence
from stockpilot.application.order_quantity_errors import InvalidOrderQuantityError
from stockpilot.application.use_cases.catalogue_advice import extract_criteria
from stockpilot.application.use_cases.prepare_order import PrepareOrderUseCase
from stockpilot.application.use_cases.resolve_product import ResolveProductUseCase
from stockpilot.application.use_cases.check_stock import CheckStockUseCase
from stockpilot.application.workflows.stockpilot_workflow import build_graph
from langgraph.checkpoint.memory import InMemorySaver
from types import SimpleNamespace
from stockpilot.domain.entities.product import Product
from stockpilot.domain.enums.intent import Intent
from stockpilot.domain.enums.product_category import ProductCategory as C

NAME = 'Northline Tools 18V Brushless Drill 0476'
SKU = 'DEMO-DRILL-0476'
NAMED = f'Peux-tu me conseiller sur perceuse {NAME} ?'
ORDER = f'Commande-moi 1 {NAME}'


def reported_catalogue():
    return MemoryCatalogue([
        Product('UNDER', 'Perceuse Sous Plafond', C.DRILL, 49.99, 2),
        Product('BOUND', 'Perceuse Au Plafond', C.DRILL, 50, 3),
        Product(SKU, NAME, C.DRILL, 52.69, 74, 'named-document'),
        Product('EMPTY', 'Perceuse En Rupture', C.DRILL, 40, 0),
    ])


@pytest.mark.parametrize('message,maximum,strict,expected', [
    ('Quelle perceuse est disponible ?', None, False, ['UNDER', 'BOUND', SKU]),
    ('Quelle perceuse est disponible moins 50 ?', 50, True, ['UNDER']),
    ('Quelle perceuse est disponible à moins de 50 ?', 50, True, ['UNDER']),
    ('Quelle perceuse est disponible maximum 50 ?', 50, False, ['UNDER', 'BOUND']),
])
def test_exact_reported_searches_check_price_bound_and_available_stock(message, maximum, strict, expected):
    graph, _, llm, forbidden = advice_graph(reported_catalogue())
    llm.classify_intent.return_value = IntentResult(intent=Intent.STOCK_CHECK, budget=999)
    result = asyncio.run(graph.ainvoke({'message': message, 'customer_id': 'a'},
        {'configurable': {'thread_id': 'reported-search'}}))
    assert result['intent'] == Intent.PRODUCT_ADVICE
    assert result['advice_status'] == 'RESULTS'
    assert [p['sku'] for p in result['recommendations']] == expected
    assert result['last_advice_criteria']['maximum'] == maximum
    assert result['last_advice_criteria']['exclusive_maximum'] is strict
    assert all(p['stock'] > 0 for p in result['recommendations'])
    forbidden.execute.assert_not_awaited()


def test_named_report_already_returns_only_the_exact_sku_on_main():
    repo = reported_catalogue()
    repo.find_advice_candidates = AsyncMock(side_effect=AssertionError('No generic substitution'))
    graph, _, _, forbidden = advice_graph(repo)
    result = asyncio.run(graph.ainvoke({'message': NAMED, 'customer_id': 'a'},
        {'configurable': {'thread_id': 'reported-name'}}))
    assert [p['sku'] for p in result['recommendations']] == [SKU]
    assert result['recommendations'][0]['price'] == 52.69
    assert result['last_advice_criteria']['mode'] == 'product'
    repo.find_advice_candidates.assert_not_awaited()
    forbidden.execute.assert_not_awaited()


@pytest.mark.parametrize('message', [
    'Quelle perceuse est disponible sous 50 ?',
    'Quelle perceuse est disponible moins de 50 ?',
    'Quelle perceuse est disponible moins 50 ?',
])
def test_elliptical_question_retains_a_strict_cap_and_no_product_reference(message):
    criteria = extract_criteria(message)
    assert criteria.maximum == 50 and criteria.exclusive_maximum is True
    assert criteria.reference is None and criteria.mode == 'search'


def test_reported_order_separates_count_from_verified_numeric_model_suffix():
    # The full name must be supplied by catalogue resolution, never by the LLM.
    evidence = concordant_quantity(ORDER, 1, product_name=NAME)
    assert evidence.quantity == 1
    assert ORDER[evidence.start:evidence.end] == '1'


def test_unverified_standalone_model_number_remains_ambiguous():
    with pytest.raises(InvalidOrderQuantityError):
        concordant_quantity(ORDER, 1)


@pytest.mark.parametrize('extracted', [18, 476, -1, 0, 1.5, True, '1'])
def test_voltage_or_model_extraction_never_becomes_requested_quantity(extracted):
    with pytest.raises(InvalidOrderQuantityError):
        concordant_quantity(ORDER, extracted, product_name=NAME)


@pytest.mark.parametrize('field,value', [('product_name', 'Other Drill 0476'),
    ('quantity', 18), ('start', 0), ('message', f'Commande-moi 1.5 {NAME}')])
def test_numeric_model_proof_is_recomputed_and_bound_to_prepared_product(field, value):
    evidence = concordant_quantity(ORDER, 1, product_name=NAME).to_dict()
    evidence[field] = value
    with pytest.raises(InvalidOrderQuantityError):
        validate_evidence(evidence, product_name=NAME)


@pytest.mark.parametrize('name', [NAME + ' Pro', NAME.replace('0476', '0477'), NAME.replace('18V', '12V')])
def test_fallback_cannot_use_a_shorter_model_hint_for_an_unknown_name(name):
    use = PrepareOrderUseCase(None, ResolveProductUseCase(reported_catalogue()))
    with pytest.raises(InvalidOrderQuantityError):
        asyncio.run(use.quantity_evidence(f'Commande-moi 1 {name}', 1))


def test_duplicate_named_identity_cannot_authorize_numeric_model_shielding():
    repo = reported_catalogue()
    repo.products.append(Product('OTHER-SKU', NAME, C.DRILL, 40, 2))
    use = PrepareOrderUseCase(None, ResolveProductUseCase(repo))
    with pytest.raises(InvalidOrderQuantityError):
        asyncio.run(use.quantity_evidence(ORDER, 1))


def test_named_quantity_proof_requires_the_same_current_catalogue_name_on_inheritance():
    use = PrepareOrderUseCase(None, ResolveProductUseCase(reported_catalogue()))
    evidence = asyncio.run(use.quantity_evidence(ORDER, 1))
    inherited = asyncio.run(use.quantity_evidence('et la perceuse', 1, evidence.to_dict()))
    assert inherited == evidence
    use._resolver.repository.products.clear()
    with pytest.raises(InvalidOrderQuantityError):
        asyncio.run(use.quantity_evidence('et la perceuse', 1, evidence.to_dict()))


def order_graph(repo=None):
    repo = repo or reported_catalogue()
    check = CheckStockUseCase(repo)
    classifier = SimpleNamespace(execute=AsyncMock(return_value=IntentResult(
        intent=Intent.ORDER, product_name=NAME, quantity=1)))
    forbidden = SimpleNamespace(execute=AsyncMock(side_effect=AssertionError('Must not finalize')))
    graph = build_graph(classifier, check, forbidden,
        PrepareOrderUseCase(check, ResolveProductUseCase(repo)), forbidden,
        checkpointer=InMemorySaver(), get_order_status=forbidden)
    return graph, classifier, forbidden


def ask(graph, message):
    return asyncio.run(graph.ainvoke({'message': message, 'customer_id': 'a'},
        {'configurable': {'thread_id': 'review-quantity'}}))


@pytest.mark.parametrize('name', ['seulement 1.5 Perceuse Northline 0476',
    'seulement deux Perceuse Northline 0476', 'pas trois mais deux Perceuse Northline 0476',
    'Perceuse Northline 1.5 0476', 'Perceuse Northline quantité deux 0476', 'seulement 0476'])
def test_catalogue_names_cannot_mask_invalid_quantities_before_model_suffix(name):
    graph, classifier, finalizer = order_graph(MemoryCatalogue([
        Product('ADVERSE', name, C.DRILL, 10, 8, 'adverse-document')]))
    classifier.execute.return_value = IntentResult(intent=Intent.ORDER, product_name=name, quantity=1)
    rejected = ask(graph, 'Commande-moi ' + name)
    assert rejected['order_status'] == 'INVALID_QUANTITY'
    assert rejected.get('pending_order') is None and rejected.get('active_order_confirmation') is None
    classifier.execute.return_value = IntentResult(intent=Intent.UNKNOWN)
    assert not ask(graph, 'oui confirme').get('created_order_id')
    finalizer.execute.assert_not_awaited()


def test_numeric_model_fallback_requires_an_explicit_count_before_the_name():
    use = PrepareOrderUseCase(None, ResolveProductUseCase(reported_catalogue()))
    with pytest.raises(InvalidOrderQuantityError):
        asyncio.run(use.quantity_evidence('Commande-moi ' + NAME, 1))


def test_inherited_named_proof_cannot_arm_a_different_product():
    repo = reported_catalogue()
    repo.products.append(Product('BOSCH-X100', 'Bosch X100', C.DRILL, 10, 8, 'bosch-document'))
    graph, classifier, finalizer = order_graph(repo)
    first = ask(graph, ORDER)
    assert first['order_status'] == 'PENDING_CONFIRMATION'
    classifier.execute.return_value = IntentResult(intent=Intent.UNKNOWN)
    changed = ask(graph, 'de Bosch X100')
    assert changed['order_status'] == 'INVALID_QUANTITY'
    assert changed.get('pending_order') is None and changed.get('active_order_confirmation') is None
    assert not ask(graph, 'oui confirme').get('created_order_id')
    finalizer.execute.assert_not_awaited()


@pytest.mark.parametrize('intent', [Intent.PRODUCT_ADVICE, Intent.STOCK_CHECK, Intent.ORDER_STATUS])
@pytest.mark.parametrize('prepared', [False, True])
def test_numeric_model_fallback_preserves_classification_conflict_refusal(intent, prepared):
    graph, classifier, finalizer = order_graph()
    if prepared:
        assert ask(graph, ORDER)['order_status'] == 'PENDING_CONFIRMATION'
    classifier.execute.return_value = IntentResult(intent=intent, product_name=NAME, quantity=1)
    rejected = ask(graph, ORDER)
    assert rejected['order_status'] == 'INVALID_QUANTITY'
    assert rejected.get('pending_order') is None and rejected.get('active_order_confirmation') is None
    classifier.execute.return_value = IntentResult(intent=Intent.UNKNOWN)
    assert not ask(graph, 'oui confirme').get('created_order_id')
    finalizer.execute.assert_not_awaited()


@pytest.mark.parametrize('count', ['-1', '−2', '0', '1.5', '1,5', '1e3', '1 000',
    'un et demi', 'une paire de', 'plusieurs', 'onze'])
def test_named_model_never_hides_an_invalid_requested_quantity(count):
    with pytest.raises(InvalidOrderQuantityError):
        concordant_quantity(f'Commande-moi {count} {NAME}', 1, product_name=NAME)
