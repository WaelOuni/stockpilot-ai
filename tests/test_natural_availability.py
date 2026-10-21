"""Bounded grammar keeps general discovery separate from exact identity."""

import pytest

from stockpilot.application.use_cases.catalogue_advice import extract_criteria

GENERAL = [
    "Je cherche une perceuse",
    "Y a-t-il une perceuse disponible ?",
    "Y a il une perceuse disponible",
    "Quelle perceuse est disponible ?",
    "Avez-vous des perceuses en stock ?",
    "Montre-moi les perceuses disponibles",
]


@pytest.mark.parametrize("question", GENERAL)
def test_general_availability_is_search(question):
    c = extract_criteria(question)
    assert c.mode == "search" and c.reference is None
    assert c.category.value == "DRILL"
    assert c.available == (None if question == GENERAL[0] else True)


@pytest.mark.parametrize(
    "question",
    [
        "Je cherche une perceuse disponible à moins de 50 €",
        "Quelles perceuses coûtent moins de 50 € ?",
        "Montre-moi les perceuses sous 50",
    ],
)
def test_price_filter_is_strict(question):
    c = extract_criteria(question)
    assert c.mode == "search" and c.reference is None
    assert c.maximum == 50 and c.exclusive_maximum


@pytest.mark.parametrize(
    "question",
    [
        "La Perceuse Northline 18V 0476 A est-elle en stock ?",
        "Conseille-moi la Perceuse Northline 18V 0476 A",
        "Avez-vous la Perceuse Northline 18V 0476 A disponible ?",
        "Y a il une Perceuse Northline 18V 0476 A disponible",
    ],
)
def test_unknown_name_discriminants_survive(question):
    c = extract_criteria(question)
    assert c.mode == "product"
    assert c.reference == "perceuse northline 18v 0476 a"


@pytest.mark.parametrize(
    "followup", ["Seulement celles qui sont disponibles", "Et moins de 50 € ?"]
)
def test_followups_preserve_search_context(followup):
    c = extract_criteria(followup, extract_criteria("Je cherche une perceuse sous 100"))
    assert c.reference is None and c.mode == "search" and c.category.value == "DRILL"
    assert c.maximum == (50 if "50" in followup else 100)
    assert c.exclusive_maximum
