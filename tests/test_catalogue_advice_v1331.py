"""Advice request grammar must not become product identity evidence (V13.3.1)."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from evals.fixtures.catalogue_advice import MemoryCatalogue, catalogue
from stockpilot.application.dto.intent_result import IntentResult
from stockpilot.application.use_cases.catalogue_advice import (
    CatalogueAdviceUseCase, extract_reference, is_catalogue_advice,
)
from stockpilot.application.use_cases.classify_intent import ClassifyIntentUseCase
from stockpilot.domain.entities.product import Product
from stockpilot.domain.enums.intent import Intent
from stockpilot.domain.enums.product_category import ProductCategory


CASES = [
    ('Demander conseil sur la Perceuse Concurrence Synthétique V017',
     'perceuse concurrence synthetique v017', 'RESULTS', ['D-C']),
    ('Conseille-moi sur la Perceuse Concurrence Synthétique V017',
     'perceuse concurrence synthetique v017', 'RESULTS', ['D-C']),
    ('Peux-tu me conseiller sur la Perceuse Concurrence Synthétique V017',
     'perceuse concurrence synthetique v017', 'RESULTS', ['D-C']),
    ('Conseille-moi sur SKU D-C', 'SKU D-C', 'RESULTS', ['D-C']),
    ('Demander conseil sur la Perceuse Inconnue V017',
     'perceuse inconnue v017', 'NO_RESULTS', []),
    ('Demander conseil sur la Perceuse Pro V017',
     'perceuse pro v017', 'CLARIFICATION_REQUIRED', []),
]


@pytest.mark.parametrize('message,reference,status,expected', CASES)
def test_supported_request_prefix_is_removed_without_losing_product_evidence(message, reference, status, expected):
    assert extract_reference(message) == reference


@pytest.mark.parametrize('message,reference,status,expected', CASES)
@pytest.mark.parametrize('model', ['order', 'stock', 'offline'])
def test_explicit_advice_is_protected_from_model_routing(message, reference, status, expected, model):
    classify = AsyncMock(side_effect=ConnectionError('model unavailable')) if model == 'offline' else AsyncMock(
        return_value=IntentResult(intent=Intent.ORDER if model == 'order' else Intent.STOCK_CHECK,
                                  product_name='INVENTED', quantity=4))
    assert is_catalogue_advice(message)
    assert asyncio.run(ClassifyIntentUseCase(SimpleNamespace(classify_intent=classify)).execute(message)).intent == Intent.PRODUCT_ADVICE


@pytest.mark.parametrize('message,reference,status,expected', CASES)
def test_targeted_advice_resolves_only_verified_identity_without_generic_alternatives(message, reference, status, expected):
    # Recreate the reported facts synthetically, without reading either DEMO.
    products = [replace(p, price=49.9) if p.sku == 'D-C' else p for p in catalogue()]
    repo = MemoryCatalogue(products)
    repo.find_resolution_candidates = AsyncMock(wraps=repo.find_resolution_candidates)
    repo.find_advice_by_sku = AsyncMock(wraps=repo.find_advice_by_sku)
    repo.find_advice_candidates = AsyncMock(side_effect=AssertionError('No generic substitution'))
    llm = SimpleNamespace(generate=AsyncMock(return_value='{"raisons":["prix","categorie","stock"]}'))
    result = asyncio.run(CatalogueAdviceUseCase(repo, llm).execute(message))
    assert result['advice_status'] == status
    assert [p['sku'] for p in result['recommendations']] == expected
    if expected:
        actual, = result['recommendations']
        assert (actual['product_name'], actual['category'], actual['price'], actual['stock']) == (
            'Perceuse Concurrence Synthétique V017', 'DRILL', 49.9, 5)
        repo.find_advice_by_sku.assert_any_await('D-C')
    else:
        llm.generate.assert_not_awaited()
    repo.find_advice_candidates.assert_not_awaited()
    assert all('demander conseil sur la ' not in call.args[0] for call in repo.find_resolution_candidates.await_args_list)


@pytest.mark.parametrize('message,reference', [
    ('  DEMANDER  conseil\tsur la Perceuse Concurrence Synthétique V017 ? ',
     'perceuse concurrence synthetique v017'),
    ('Demander conseil sur la Perceuse Concurrence Synthétique V018',
     'perceuse concurrence synthetique v018'),
    ('Demander un avis sur la Perceuse Concurrence Synthétique V017',
     'demander un avis sur la perceuse concurrence synthetique v017'),
    ('Demander conseil surveillance V017', 'demander conseil surveillance v017'),
])
def test_prefix_is_anchored_bounded_and_keeps_unknown_discriminants(message, reference):
    assert extract_reference(message) == reference


@pytest.mark.parametrize('name,message', [
    ('Conseille sur Mesure V017', 'Demander conseil sur le Conseille sur Mesure V017'),
    ('Demander Conseil Sur V017', 'Conseille-moi sur le Demander Conseil Sur V017'),
])
def test_prefix_removal_is_not_recursive_and_keeps_request_words_inside_product_names(name, message):
    repo = MemoryCatalogue([Product('P-1', name, ProductCategory.OTHER, 75, 3)])
    llm = SimpleNamespace(generate=AsyncMock(return_value='{"raisons":["prix","categorie","stock"]}'))
    result = asyncio.run(CatalogueAdviceUseCase(repo, llm).execute(message))
    assert [p['sku'] for p in result['recommendations']] == ['P-1']


@pytest.mark.parametrize('constraint', ['disponible', 'en stock'])
def test_explicit_nominal_advice_is_not_mistaken_for_a_named_stock_check(constraint):
    message = f'Demander conseil sur la Perceuse Concurrence Synthétique V017 {constraint}'
    assert is_catalogue_advice(message)
    assert extract_reference(message) == 'perceuse concurrence synthetique v017'


@pytest.mark.parametrize('message', [
    'La Perceuse Concurrence Synthétique V017 est-elle disponible ?',
    'Commande-moi 1 Perceuse Concurrence Synthétique V017',
    'Demander conseil sur la Perceuse Concurrence Synthétique V017 puis commander 1 produit',
])
def test_stock_and_explicit_order_routing_guards_are_unchanged(message):
    assert not is_catalogue_advice(message)
