"""Explicit preparation prefixes preserve the original lexical quantity proof."""
import pytest

from stockpilot.application.order_quantity_errors import InvalidOrderQuantityError
from stockpilot.application.order_quantity_evidence import (
    concordant_quantity, explicit_order_request, inspect_quantity, validate_evidence,
)

PREFIXES = [
    "Prépare une commande de",
    "Prépare-moi une commande de",
    "Je voudrais préparer une commande de",
    "Peux-tu préparer une commande de",
    "Préparez une commande de",
]


@pytest.mark.parametrize("prefix", PREFIXES)
@pytest.mark.parametrize("product", ["DEMO-01-0225", "du produit DEMO-01-0225"])
def test_preparation_keeps_original_text_and_exact_quantity_span(prefix, product):
    message = f"{prefix} 2 unités de {product}" if not product.startswith("du ") else f"{prefix} 2 unités {product}"
    assert explicit_order_request(message)
    evidence = concordant_quantity(message, 2)
    assert evidence.message == message
    assert message[evidence.start:evidence.end] == "2"
    assert evidence.kind == "explicit" and evidence.quantity == 2
    assert validate_evidence(evidence.to_dict()) == evidence


@pytest.mark.parametrize("quantity", ["0", "-2", "−2", "1.5", "1,5", "1e2", "2 ou 3", "deux et demi", "plusieurs", "2 000", "½", ".5", "2abc"])
@pytest.mark.parametrize("prefix", PREFIXES[:3])
def test_preparation_prefix_never_weakens_quantity_validation(prefix, quantity):
    with pytest.raises(InvalidOrderQuantityError):
        inspect_quantity(f"{prefix} {quantity} unités du produit DEMO-01-0225")


@pytest.mark.parametrize("message", [
    "Quels produits ont 2 unités disponibles ?",
    "Je cherche 2 produits sous 100 euros",
    "Comment préparer une commande de 2 unités de DEMO-01-0225 ?",
    "Ne prépare pas une commande de 2 unités de DEMO-01-0225",
    "Je voudrais savoir comment préparer une commande de 2 unités de DEMO-01-0225",
])
def test_search_question_or_negation_is_not_a_preparation(message):
    assert not explicit_order_request(message)
    with pytest.raises(InvalidOrderQuantityError):
        inspect_quantity(message)


def test_model_quantity_and_modified_persisted_evidence_remain_untrusted():
    message = "Prépare une commande de 2 unités du produit DEMO-01-0225"
    with pytest.raises(InvalidOrderQuantityError):
        concordant_quantity(message, 1)
    proof = inspect_quantity(message).to_dict()
    for field, value in [("quantity", 3), ("start", 0), ("message", message.replace(" 2 ", " 3 "))]:
        with pytest.raises(InvalidOrderQuantityError):
            validate_evidence({**proof, field: value})

@pytest.mark.parametrize("prefix", PREFIXES)
@pytest.mark.parametrize("tail", ["DEMO-01-0225", "le produit DEMO-01-0225", "SKU DEMO-01-0225"])
def test_new_preparation_wording_requires_an_explicit_count(prefix, tail):
    with pytest.raises(InvalidOrderQuantityError):
        inspect_quantity(f"{prefix} {tail}")
