import pytest
from stockpilot.application.services.tenant_catalogue_discovery import (
    is_low_stock_request,
    low_stock_criteria,
)
from stockpilot.application.use_cases.catalogue_advice import ClarificationRequired
from stockpilot.domain.entities.tenant_product import TenantProductQuery


@pytest.mark.parametrize(
    "amount", ["moins de 150 euros", "moins de 150 €", "moins de 150", "maximum 150"]
)
def test_catalogue_availability_does_not_turn_a_budget_into_stock(amount):
    assert not is_low_stock_request("je cherche 2 perceuses disponibles " + amount)


@pytest.mark.parametrize(
    "values",
    [
        {"maximum_available": True},
        {"maximum_available": -1},
        {"maximum_available": "5"},
        {"maximum_available": 2**63},
        {"sort_by_available": 1},
        {"categories": ["perceuses"]},
        {"categories": ({"$ne": None},)},
    ],
)
def test_internal_stock_query_rejects_coercion_and_injected_filters(values):
    with pytest.raises(ValueError):
        TenantProductQuery(**values)


@pytest.mark.parametrize(
    "question,maximum",
    [
        ("Quels produits ont moins de 5 unités disponibles ?", 4),
        ("Quels produits ont moins de 5 disponibles ?", 4),
        ("Montre-moi les stocks inférieurs à 5 unités.", 4),
        ("Quels articles sont presque épuisés ?", 4),
        ("Liste les produits avec 5 unités disponibles ou moins.", 5),
        ("Montre les produits en rupture de stock.", 0),
        ("QUELS PRODUITS ONT MOINS DE 5 UNITES DISPONIBLES ?", 4),
        ("quelles perceuses ont 5 unités ou moins disponibles ?", 5),
        ("quelles perceuses ont moins de 0 unités disponibles ?", -1),
    ],
)
def test_natural_stock_quantity_routing_and_criteria(question, maximum):
    assert is_low_stock_request(question)
    criteria = low_stock_criteria(question, ("perceuses", "scies"))
    assert criteria.maximum == maximum
    assert criteria.categories == (("perceuses",) if "perceuses" in question else ())


@pytest.mark.parametrize(
    "question",
    [
        "Quels robots ont moins de 5 unités disponibles ?",
        "Quels articles inconnus sont presque épuisés ?",
        "Quels produits ont moins de 5 unités et moins de 3 unités disponibles ?",
    ],
)
def test_quantity_normalization_never_broadens_unknown_or_ambiguous_criteria(question):
    assert is_low_stock_request(question)
    with pytest.raises(ClarificationRequired):
        low_stock_criteria(question, ("perceuses",))


@pytest.mark.parametrize(
    "question",
    [
        "Je cherche 2 perceuses disponibles à moins de 300 euros",
        "Je cherche SKU BDEMO-perceuses-0",
        "Je cherche des perceuses avec moins de 300 euros",
    ],
)
def test_product_and_budget_searches_remain_outside_low_stock(question):
    assert not is_low_stock_request(question)


@pytest.mark.parametrize("phrase,minimum,maximum", [
    ("Quels produits ont que 5 unités disponibles ?", 5, 5),
    ("Quels produits avec 5 unités disponibles ?", 5, 5),
    ("produits avec exactement 5 unités disponibles", 5, 5),
    ("produits ayant seulement 5 unités disponibles", 5, 5),
    ("produits ont 5 unités disponibles", 5, 5),
    ("produits avec moins de 15 unités disponibles", 0, 14),
    ("produits avec 15 unités disponibles ou moins", 0, 15),
    ("produits avec au moins 5 unités disponibles", 5, None),
    ("produits avec plus de 5 unités disponibles", 6, None),
    ("produits avec entre 1 et 14 unités disponibles", 1, 14),
    ("produits en rupture", 0, 0),
])
def test_v024_explicit_available_intervals(phrase, minimum, maximum):
    assert is_low_stock_request(phrase)
    criteria = low_stock_criteria(phrase, ("perceuses",))
    assert (criteria.minimum, criteria.maximum) == (minimum, maximum)


@pytest.mark.parametrize("phrase", [
    "produits avec entre 14 et 1 unités disponibles",
    "produits avec exactement 5.5 unités disponibles",
    "produits avec exactement -5 unités disponibles",
    "produits avec exactement 5 et exactement 6 unités disponibles",
    "produits avec au moins 9223372036854775808 unités disponibles",
    "produits avec entre 0 et 9223372036854775808 unités disponibles",
    "produits avec plus de 9223372036854775807 unités disponibles",
])
def test_v024_invalid_stock_constraints_never_broaden(phrase):
    with pytest.raises(ClarificationRequired):
        low_stock_criteria(phrase, ())


@pytest.mark.parametrize("phrase", [
    "Je cherche une perceuse avec 5 euros",
    "Je cherche une perceuse avec exactement 5 euros",
    "Je cherche SKU DEMO-01-0225",
    "Je cherche la perceuse Northline 18V 0476",
])
def test_v024_budget_and_reference_digits_are_not_stock(phrase):
    assert not is_low_stock_request(phrase)


@pytest.mark.parametrize("value", [True, -1, "5", 1.5, 2**63, {"$gte": 0}])
def test_v024_minimum_bound_is_strict(value):
    with pytest.raises(ValueError):
        TenantProductQuery(minimum_available=value)


def test_v024_interval_validation():
    with pytest.raises(ValueError):
        TenantProductQuery(minimum_available=6, maximum_available=5)
