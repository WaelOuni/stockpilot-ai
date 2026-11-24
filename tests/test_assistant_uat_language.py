"""Exact reported UAT phrases; read semantics independent of model output."""
import pytest
from stockpilot.application.services.tenant_assistant_catalogue import TenantAssistantCatalogue
from stockpilot.application.services.tenant_catalogue_discovery import low_stock_criteria


def test_short_available_budget_phrase_is_not_a_named_product():
    criteria = TenantAssistantCatalogue.criteria("perceuse dispo avec moins de 100 euros", None)
    assert criteria.mode == "search" and criteria.reference is None
    assert criteria.category.value == "DRILL" and criteria.available
    assert criteria.maximum == 100 and criteria.exclusive_maximum


def test_available_drills_question_is_generic():
    criteria = TenantAssistantCatalogue.criteria("Est-ce que vous avez des perceuses disponibles ?", None)
    assert criteria.mode == "search" and criteria.reference is None
    assert criteria.category.value == "DRILL" and criteria.available


def test_five_cheapest_products_are_a_bounded_search():
    criteria = TenantAssistantCatalogue.criteria("Quels sont les 5 produits les moins chers de mon catalogue ?", None)
    assert criteria.mode == "search" and criteria.reference is None and criteria.limit == 5


def test_almost_out_excludes_zero_but_complete_out_is_exact_zero():
    low = low_stock_criteria("Montre-moi les produits qui sont presque en rupture de stock", ())
    zero = low_stock_criteria("Quels produits sont complètement en rupture de stock ?", ())
    assert (low.minimum, low.maximum) == (1, 4)
    assert (zero.minimum, zero.maximum) == (0, 0)


def test_future_risk_is_explicitly_unavailable_from_a_stock_snapshot():
    criteria = low_stock_criteria("les produits qui vont s'épuiser bientôt", ())
    assert criteria.future_risk is True and criteria.minimum == 1


@pytest.mark.parametrize("phrase", ['Je cherche "Perceuse Dispo 100"', "Je cherche SKU dispo-100", "Je cherche une perceuse Atlas dispo", "Je cherche une perceuse disponible inconnue"])
def test_unrecognized_identity_words_are_never_stripped(phrase):
    criteria = TenantAssistantCatalogue.criteria(phrase, None)
    assert criteria.reference is not None
