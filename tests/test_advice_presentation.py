"""Presentation consumes current catalogue facts; it cannot add business facts."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from evals.fixtures.catalogue_advice import MemoryCatalogue, catalogue
from stockpilot.application.dto.advice_criteria import AdviceCriteria
from stockpilot.application.use_cases.advice_presentation import advice_summary, catalogue_number, no_results_summary
from stockpilot.application.use_cases.catalogue_advice import CatalogueAdviceUseCase
from stockpilot.domain.entities.product import Product
from stockpilot.domain.enums.product_category import ProductCategory as C


def answer(question, products=None, *, model='{"raisons":["prix","stock","categorie"]}', **context):
    llm = SimpleNamespace(generate=AsyncMock(return_value=model))
    use_case = CatalogueAdviceUseCase(MemoryCatalogue(products), llm)
    result = asyncio.run(use_case.execute(question, **context))
    assert llm.generate.await_count <= 1
    return result


@pytest.mark.parametrize('category,noun', [
    (C.DRILL, 'perceuses'), (C.SCREWDRIVER, 'visseuses'),
    (C.SAW, 'scies'), (C.SANDER, 'ponceuses'),
])
def test_general_summary_uses_actual_count_readable_category_and_constraints(category, noun):
    products = [Product(str(i), f'Modèle {i}', category, 20 + i, 2) for i in range(3)]
    result = answer(f'Je cherche des {noun} disponibles à moins de 100 euros.', products)
    assert result['response'].startswith(f'J’ai trouvé 3 {noun} disponibles à moins de 100.')
    assert 'prix croissant' in result['response']
    assert '(s)' not in str(result)
    assert '€' not in str(result) and 'euro' not in str(result)
    assert 'Seuls la catégorie' not in result['response']
    for recommendation in result['recommendations']:
        assert 'plafond strict de 100' in recommendation['explanation']
        assert 'Prix catalogue :' not in recommendation['explanation']
        assert 'Catégorie catalogue :' not in recommendation['explanation']
        assert 'Disponibilité vérifiée :' not in recommendation['explanation']


def test_actual_cardinality_and_inclusive_budget_are_not_misrepresented():
    result = answer('Je cherche une perceuse disponible budget 100', [catalogue()[3]])
    assert result['response'].startswith('J’ai trouvé 1 perceuse disponible au prix maximum de 100.')
    assert 'plafond inclusif de 100' in result['recommendations'][0]['explanation']
    assert result['recommendations'][0]['price'] == 100
    assert 'à moins de' not in result['response']
    assert answer('Je cherche une perceuse disponible à moins de 100', [catalogue()[3]])['advice_status'] == 'NO_RESULTS'


@pytest.mark.parametrize('stock,description', [
    (5, '5 unités en stock'), (1, '1 unité en stock'),
    (0, 'indisponible au moment de la recherche : aucune unité en stock'),
])
def test_targeted_summary_uses_verified_name_and_current_stock(stock, description):
    target = replace(catalogue()[2], stock=stock, price=49.9)
    result = answer('Conseille-moi sur SKU D-C', [target])
    assert result['response'].startswith(f'Voici la fiche de {target.name}.')
    assert description in result['response']
    assert len(result['recommendations']) == 1
    assert result['recommendations'][0]['price'] == 49.9
    assert 'caractéristiques techniques' not in result['response']
    assert 'devise' not in result['response']


def test_targeted_product_outside_requested_budget_is_warned_in_main_summary():
    result = answer('Conseille-moi sur SKU D-C à moins de 50 euros')
    assert result['advice_status'] == 'RESULTS'
    assert 'ne respecte pas tous vos critères' in result['response']
    assert 'n’est pas une correspondance valide' in result['response']
    assert 'hors critères' in result['recommendations'][0]['explanation']


def test_comparison_explains_only_available_facts_and_preserves_budget_warning():
    result = answer('Compare SKU D-H avec deux autres disponibles à moins de 100 euros')
    assert [p['sku'] for p in result['recommendations']] == ['D-H', 'D-A', 'D-B']
    assert '2 alternatives' in result['response']
    assert 'différences de prix et de disponibilité' in result['response']
    assert 'caractéristiques techniques ne sont pas renseignées' in result['response']
    assert 'hors budget' in result['response']
    assert 'hors budget' in result['recommendations'][0]['explanation']
    assert 'plus performant' not in str(result)


def test_zero_or_single_alternative_is_explicit_and_grammatical():
    only_reference = answer('Compare SKU D-C avec deux autres', [catalogue()[2]])
    assert 'Aucune alternative ne correspond' in only_reference['response']
    one_alternative = answer('Compare SKU D-C avec deux autres', catalogue()[:1] + catalogue()[2:3])
    assert '1 alternative.' in one_alternative['response']
    assert '1 alternatives' not in one_alternative['response']


def test_no_result_copy_retains_requested_constraints_without_substitution():
    result = answer('Je cherche une perceuse disponible à moins de 25 euros', [])
    assert result['advice_status'] == 'NO_RESULTS'
    assert result['recommendations'] == []
    assert 'perceuses disponibles à moins de 25' in result['response']
    assert 'modifier votre budget ou vos critères' in result['response']
    assert '€' not in result['response']


def test_unavailable_search_copy_does_not_claim_availability():
    result = answer('Je cherche une perceuse indisponible')
    assert result['response'].startswith('J’ai trouvé 1 perceuse indisponible.')
    assert result['recommendations'][0]['stock'] == 0


def test_cheapest_copy_names_the_currently_verified_presented_product():
    result = answer('Et parmi ceux-là, lequel est le moins cher ?',
                    previous=AdviceCriteria(category=C.DRILL, maximum=100, available=True),
                    previous_skus=('D-B', 'D-C'))
    assert result['recommendations'][0]['sku'] == 'D-B'
    assert 'Perceuse Compact' in result['response']
    assert 'prix le plus bas : 80,00.' in result['response']
    assert '€' not in result['response']


@pytest.mark.parametrize('model', ['invented 18 V', '{"raisons":["prix","prix","stock"]}'])
def test_invalid_model_copy_still_falls_back_to_verified_presentation(model):
    result = answer('Je cherche une perceuse disponible', model=model)
    assert len(result['recommendations']) == 3
    assert 'Présentation déterministe' in result['response']
    assert 'invented' not in str(result) and '18 V' not in str(result)


@pytest.mark.parametrize('value,price,expected', [
    (49.9, True, '49,90'), (0, True, '0,00'), (100, False, '100'),
    (12345.67, True, '12\u202f345,67'), (0.001, True, '0,001'),
    (1e-7, True, '0,0000001'), (-60, True, '-60,00'),
])
def test_numbers_are_french_without_implied_currency_or_hidden_precision(value, price, expected):
    assert catalogue_number(value, price=price) == expected


def test_fractional_price_difference_has_no_binary_float_artifacts():
    reference = replace(catalogue()[2], price=49.9)
    alternative = replace(catalogue()[0], price=60)
    result = answer('Compare SKU D-C avec deux autres', [reference, alternative])
    assert 'Écart de prix avec la référence : +10,10.' in result['recommendations'][1]['explanation']


def test_display_retains_minimum_and_maximum_criteria_without_assuming_currency():
    criteria = AdviceCriteria(category=C.DRILL, minimum=50, maximum=100, exclusive_maximum=True)
    assert 'à partir de 50 et à moins de 100' in advice_summary(criteria, catalogue()[:3])
    assert 'à partir de 50 et à moins de 100' in no_results_summary(criteria)
