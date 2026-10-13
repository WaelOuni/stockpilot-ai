"""Natural request grammar must not turn model hints into product authority."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from stockpilot.application.use_cases.resolve_product import ResolveProductUseCase, ResolutionStatus
from stockpilot.application.use_cases.prepare_order import PrepareOrderUseCase
from stockpilot.application.workflows.nodes import create_order_node
from stockpilot.domain.entities.product import Product
from stockpilot.domain.enums.product_category import ProductCategory

FORMULATIONS = [
    'Peux-tu me commander {name} ?',
    'Tu peux me commander {name} ?',
    'Commande-moi {name}.',
    'Je voudrais commander {name}.',
    'Je souhaite acheter {name}.',
    'Je veux commander 1 {name}.',
    'Pourriez-vous me réserver {name} ?',
    'Serait-il possible de commander {name} ?',
]
TARGET = Product('DEMO-SAW-0001', 'Scie Ambigue V012', ProductCategory.SAW, 25., 10,
                 '507f1f77bcf86cd799439011')


def repo(products):
    from stockpilot.application.use_cases.resolve_product import normalize_name
    async def skus(sku):
        return [p for p in products if p.sku.casefold() == sku.casefold()]
    async def candidates(text, *, exact, limit):
        # Literal names/tokens, no model-dependent fixture result.
        words = normalize_name(text).split()
        return [p for p in products if (normalize_name(p.name) == normalize_name(text)
                if exact else all(w in normalize_name(p.name).split() for w in words))][:limit]
    return SimpleNamespace(find_resolution_skus=AsyncMock(side_effect=skus),
                           find_resolution_candidates=AsyncMock(side_effect=candidates))


@pytest.mark.parametrize('phrase', FORMULATIONS)
@pytest.mark.parametrize('duplicate', [False, True])
def test_natural_names_unique_or_ambiguous(phrase, duplicate):
    products = [TARGET] + ([replace(TARGET, sku='DEMO-SAW-0002', document_id='other')] if duplicate else [])
    repository = repo(products)
    prepare = PrepareOrderUseCase(SimpleNamespace(), ResolveProductUseCase(repository))
    result = asyncio.run(create_order_node(prepare)({'message': phrase.format(name=TARGET.name),
                        'product_name': TARGET.name, 'quantity': 1}))
    assert result['order_status'] == ('AMBIGUOUS' if duplicate else 'PENDING_CONFIRMATION')
    if duplicate:
        assert result['pending_order'] is None
    else:
        assert result['pending_order']['product_sku'] == TARGET.sku
        assert result['pending_order']['product_document_id'] == TARGET.document_id
    assert TARGET.stock == 10  # Preparation has no writer dependency.


@pytest.mark.parametrize('phrase', FORMULATIONS)
def test_explicit_sku_wins_over_wrong_model(phrase):
    result = asyncio.run(ResolveProductUseCase(repo([TARGET])).execute(
        'Invented name', phrase.format(name=TARGET.sku)))
    assert result.product == TARGET


@pytest.mark.parametrize('phrase', FORMULATIONS)
def test_missing_name_never_uses_model_fallback(phrase):
    result = asyncio.run(ResolveProductUseCase(repo([TARGET])).execute(
        TARGET.name, phrase.format(name='Scie Absente V999')))
    assert result.status == ResolutionStatus.NOT_FOUND


@pytest.mark.parametrize('extra', ['12V', 'Pro', 'Max', 'Reserve', 'A'])
def test_unknown_prefix_and_short_hint_preserve_discriminants(extra):
    short = replace(TARGET, name='Bosch D461')
    complete = replace(TARGET, sku='OTHER-001', name='Bosch D461 '+extra)
    result = asyncio.run(ResolveProductUseCase(repo([short, complete])).execute(
        short.name, 'Serait-il possible de commander '+complete.name+' ?'))
    assert result.product == complete
    missing = asyncio.run(ResolveProductUseCase(repo([short])).execute(
        short.name, 'Peux-tu me commander '+complete.name+' ?'))
    assert missing.status == ResolutionStatus.NOT_FOUND


def test_arbitrary_unknown_prefix_is_not_erased_by_correct_hint():
    result = asyncio.run(ResolveProductUseCase(repo([TARGET])).execute(
        TARGET.name, 'Variante inconnue '+TARGET.name))
    assert result.status == ResolutionStatus.NOT_FOUND


def test_request_words_inside_product_are_literal():
    target = replace(TARGET, name='Kit Acheter Reserve A 12V')
    result = asyncio.run(ResolveProductUseCase(repo([target])).execute(
        'Kit', 'Peux-tu me commander '+target.name+' ?'))
    assert result.product == target


@pytest.mark.parametrize('message', [
    'PEUX-TU ME COMMANDER {name} ?',
    'Peux-tu\u00a0me commander {name} ?',
    'Peux tu me commander {name} !',
    'J’aimerais acheter {name}.',
    'Pourriez-vous me réserver "{name}" ?',
    'Tu pourrais me reserver {name}.',
])
def test_accents_case_spacing_apostrophes_and_punctuation(message):
    target = replace(TARGET, name='Élite Kit [D.4]+ (12V)')
    result = asyncio.run(ResolveProductUseCase(repo([target])).execute(
        'Elite Kit', message.format(name=target.name)))
    assert result.product == target


@pytest.mark.parametrize('message', [
    'Peux-tu me commander {name} 24V ?',
    'Peux-tu me commander {name} Pro ?',
    'Peux-tu me commander {name} et une variante inconnue ?',
])
def test_suffix_constraints_are_not_lost_to_exact_model_hint(message):
    result = asyncio.run(ResolveProductUseCase(repo([TARGET])).execute(
        TARGET.name, message.format(name=TARGET.name)))
    assert result.status == ResolutionStatus.NOT_FOUND


@pytest.mark.parametrize('message', ['Commander PEUX-TU', 'SKU: PEUX-TU', 'PEUX-TU'])
def test_modal_shaped_sku_outside_request_prefix_is_authoritative(message):
    target = replace(TARGET, sku='PEUX-TU')
    result = asyncio.run(ResolveProductUseCase(repo([target])).execute('Wrong hint', message))
    assert result.product == target


def test_unknown_modal_shaped_sku_does_not_fall_back_to_model_name():
    result = asyncio.run(ResolveProductUseCase(repo([TARGET])).execute(TARGET.name, 'Commander PEUX-TU'))
    assert result.status == ResolutionStatus.NOT_FOUND


def test_punctuation_inside_quoted_name_is_preserved():
    target = replace(TARGET, name='Kit Élite V012!')
    result = asyncio.run(ResolveProductUseCase(repo([target])).execute('Kit Élite',
        'Peux-tu me commander "Kit Élite V012!" ?'))
    assert result.product == target
