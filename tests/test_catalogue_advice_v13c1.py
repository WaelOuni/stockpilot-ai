import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from evals.fixtures.catalogue_advice import MemoryCatalogue, advice_graph, catalogue
from stockpilot.application.dto.advice_criteria import AdviceCriteria
from stockpilot.application.dto.intent_result import IntentResult
from stockpilot.application.use_cases.catalogue_advice import CatalogueAdviceUseCase, extract_criteria
from stockpilot.application.use_cases.prepare_order import PrepareOrderUseCase
from stockpilot.application.use_cases.check_stock import StockResult
from stockpilot.application.workflows.stockpilot_workflow import build_graph
from stockpilot.domain.enums.intent import Intent
from stockpilot.domain.enums.product_category import ProductCategory as C
from stockpilot.infrastructure.persistence.mongodb.product_repository import MongoProductRepository
from langgraph.checkpoint.memory import InMemorySaver


def run(coro):
    return asyncio.run(coro)


def service(products=None):
    repo = MemoryCatalogue(products)
    llm = SimpleNamespace(generate=AsyncMock(return_value='{"raisons":["prix","categorie","stock"]}'))
    return CatalogueAdviceUseCase(repo, llm), repo, llm


def skus(result):
    return [p['sku'] for p in result['recommendations']]


@pytest.mark.parametrize('message,limit', [
    ('Je cherche une perceuse.', 3), ('Je voudrais une visseuse.', 3),
    ('Peux-tu me recommander une scie ?', 3), ('Je cherche un outil disponible.', 3),
    ('Montre-moi un seul produit.', 1), ('Propose exactement une perceuse.', 1),
    ('Affiche 2 produits.', 2), ('Donne-moi trois perceuses.', 3),
    ('Je cherche une seule perceuse.', 1),
])
def test_grammatical_articles_and_explicit_presentation_counts(message, limit):
    assert extract_criteria(message).limit == limit
    graph, _, _, forbidden = advice_graph()
    result = run(graph.ainvoke({'message': message, 'customer_id': 'a'}, {'configurable': {'thread_id': 'count'}}))
    assert result['intent'] == Intent.PRODUCT_ADVICE
    assert 0 < len(result['recommendations']) <= limit
    forbidden.execute.assert_not_awaited()


def test_acceptance_general_search_has_three_two_or_zero_real_matches():
    for products, expected in [(None, ['D-A', 'D-B', 'D-C']), (catalogue()[:2], ['D-A', 'D-B']), ([], [])]:
        use, _, _ = service(products)
        result = run(use.execute('Je cherche une perceuse disponible à moins de 100 euros.'))
        assert skus(result) == expected
        assert result['advice_status'] == ('RESULTS' if expected else 'NO_RESULTS')


@pytest.mark.parametrize('message,expected,status', [
    ('Peux-tu me conseiller sur la Perceuse Concurrence Synthétique V017 ?', ['D-C'], 'RESULTS'),
    ('Conseille-moi sur Concurrence Synthétique V017.', ['D-C'], 'RESULTS'),
    ('Conseille-moi sur la Perceuse Atelier.', ['D-A'], 'RESULTS'),
    ('Conseille-moi sur le SKU D-C.', ['D-C'], 'RESULTS'),
    ('Conseille-moi sur la Perceuse Pro V017.', [], 'CLARIFICATION_REQUIRED'),
    ('Conseille-moi sur la Perceuse Inconnue V017.', [], 'NO_RESULTS'),
    ('Conseille-moi sur le SKU UNKNOWN-17.', [], 'NO_RESULTS'),
    ('Conseille-moi sur les SKU D-A et D-B.', [], 'CLARIFICATION_REQUIRED'),
    ('Conseille-moi sur la Perceuse .* V017.', [], 'NO_RESULTS'),
])
def test_targeted_identity_never_substitutes_generic_products(message, expected, status):
    use, repo, _ = service()
    repo.find_advice_candidates = AsyncMock(side_effect=AssertionError('No automatic alternatives'))
    result = run(use.execute(message))
    assert skus(result) == expected
    assert result['advice_status'] == status
    repo.find_advice_candidates.assert_not_awaited()


def test_reference_disappeared_or_changed_after_resolution_is_not_rendered():
    for current in (None, replace(catalogue()[2], name='Changed name'), replace(catalogue()[2], sku='OTHER')):
        use, repo, _ = service()
        repo.find_advice_by_sku = AsyncMock(return_value=current)
        result = run(use.execute('Conseille-moi sur le SKU D-C'))
        assert skus(result) == []
        assert result['advice_status'] == 'CLARIFICATION_REQUIRED'


def test_comparison_preserves_out_of_budget_reference_and_filters_alternatives():
    use, _, llm = service()
    result = run(use.execute('Compare le SKU D-H avec deux autres disponibles à moins de 100 euros'))
    assert skus(result) == ['D-H', 'D-A', 'D-B']
    reference, *alternatives = result['recommendations']
    assert reference['role'] == 'reference' and reference['matches_criteria'] is False
    assert 'hors budget' in reference['explanation']
    assert 'respecte le plafond' not in reference['explanation']
    assert [p['price_difference'] for p in alternatives] == [-60, -40]
    assert all(p['role'] == 'alternative' and p['matches_criteria'] for p in alternatives)
    assert len(set(skus(result))) == 3
    assert 'caractéristiques techniques ne sont pas renseignées' in result['response']
    assert llm.generate.await_count == 1


@pytest.mark.parametrize('message', ['Compare cette perceuse avec deux autres.', 'Compare le premier avec deux autres.', 'Montre-moi deux alternatives à ce produit.'])
def test_anaphoric_comparison_requires_authorized_reference(message):
    use, _, _ = service()
    result = run(use.execute(message))
    assert result['advice_status'] == 'CLARIFICATION_REQUIRED'
    assert skus(result) == []


def test_comparison_cap_no_duplicates_and_no_available_alternatives():
    use, repo, _ = service()
    assert run(use.execute('Compare le SKU D-C avec cinq autres'))['advice_status'] == 'CLARIFICATION_REQUIRED'
    repo.find_advice_candidates = AsyncMock(return_value=[catalogue()[2], catalogue()[0], catalogue()[0]])
    result = run(use.execute('Compare le SKU D-C avec quatre autres'))
    assert skus(result) == ['D-C', 'D-A']
    assert result['recommendations'][0]['matches_criteria'] is True
    repo.find_advice_candidates.return_value = []
    assert skus(run(use.execute('Compare le SKU D-C avec deux autres'))) == ['D-C']


def test_structured_followups_replacement_removal_selection_and_independent_search():
    graph, _, _, forbidden = advice_graph()
    async def scenario():
        config = {'configurable': {'thread_id': 'structured'}}
        async def ask(message):
            return await graph.ainvoke({'message': message, 'customer_id': 'a'}, config)
        first = await ask('Je cherche une perceuse disponible à moins de 100 euros.')
        assert skus(first) == ['D-A', 'D-B', 'D-C']
        raised = await ask('Et si je monte le budget à 150 euros ?')
        assert raised['last_advice_criteria']['maximum'] == 150
        assert raised['last_advice_criteria']['exclusive_maximum'] is False
        assert raised['last_advice_criteria']['available'] is True
        assert raised['last_category'] == C.DRILL
        two = await ask('Montre-moi seulement deux produits.')
        assert skus(two) == ['D-A', 'D-B']
        lowered = await ask('Et si je descends à 80 € ?')
        assert skus(lowered) == ['D-A', 'D-B']
        assert lowered['last_budget'] == 80
        cheapest = await ask('Et parmi ceux-là, lequel est le moins cher ?')
        assert skus(cheapest) == ['D-A']
        comparison = await ask('Compare cette perceuse avec deux autres.')
        assert skus(comparison) == ['D-A', 'D-B']
        assert comparison['recommendations'][0]['role'] == 'reference'
        ambiguous = await ask('Compare cette perceuse avec deux autres.')
        assert ambiguous['advice_status'] == 'CLARIFICATION_REQUIRED'
        assert ambiguous['last_advice_skus'] == []
        await ask('Je cherche une perceuse disponible à moins de 100 euros.')
        changed = await ask('Et si je veux une visseuse plutôt ?')
        assert skus(changed) == ['S-A']
        assert changed['last_budget'] == 100 and changed['last_advice_criteria']['available'] is True
        no_budget = await ask('Et sans plafond de budget ?')
        assert no_budget['last_budget'] is None
        independent = await ask('Je cherche une scie.')
        assert skus(independent) == ['W-A']
        assert independent['last_advice_criteria']['available'] is None
        assert independent['last_budget'] is None
        assert len(independent['last_advice_skus']) <= 5
        assert independent['last_product_name'] is None and independent['last_quantity'] is None
    run(scenario())
    forbidden.execute.assert_not_awaited()


def test_ordinal_selection_cheapest_and_live_catalogue_recheck():
    graph, repo, _, _ = advice_graph()
    async def scenario():
        config = {'configurable': {'thread_id': 'ordinal'}}
        async def ask(message):
            return await graph.ainvoke({'message': message, 'customer_id': 'a'}, config)
        await ask('Je cherche une perceuse disponible à moins de 100 euros.')
        comparison = await ask('Compare le premier avec deux autres.')
        assert skus(comparison) == ['D-A', 'D-B', 'D-C']
        repo.products = [replace(p, price=110) if p.sku == 'D-A' else p for p in repo.products]
        cheapest = await ask('Quelle est la moins chère entre ces trois perceuses ?')
        assert skus(cheapest) == ['D-B']
    run(scenario())


@pytest.mark.parametrize('other_customer,other_thread', [('a', 'new'), ('b', 'same')])
def test_no_context_across_thread_or_customer(other_customer, other_thread):
    graph, _, _, _ = advice_graph()
    async def scenario():
        await graph.ainvoke({'message': 'Conseille-moi sur le SKU D-C', 'customer_id': 'a'}, {'configurable': {'thread_id': 'same'}})
        result = await graph.ainvoke({'message': 'Compare cette perceuse avec deux autres.', 'customer_id': other_customer}, {'configurable': {'thread_id': other_thread}})
        assert skus(result) == []
        assert result['advice_status'] == 'CLARIFICATION_REQUIRED'
    run(scenario())


def test_advice_cannot_select_an_order_and_pending_order_is_disarmed():
    classify = SimpleNamespace(execute=AsyncMock(side_effect=lambda msg: IntentResult(
        intent=Intent.ORDER, product_name=None, quantity=2) if msg.startswith('Commande') else
        IntentResult(intent=Intent.PRODUCT_ADVICE) if 'Conseille' in msg else
        IntentResult(intent=Intent.UNKNOWN)))
    stock = SimpleNamespace(execute=AsyncMock(return_value=StockResult(sku='D-C', name='Perceuse Concurrence Synthétique V017',
        stock=5, available=True, document_id='507f1f77bcf86cd799439011')))
    finalizer = SimpleNamespace(execute=AsyncMock(side_effect=AssertionError('No confirmation')))
    use, _, _ = service()
    prepare = PrepareOrderUseCase(stock)
    graph = build_graph(classify, stock, finalizer, prepare, finalizer,
        get_order_status=finalizer, catalogue_advice=use, checkpointer=InMemorySaver())
    async def scenario():
        config = {'configurable': {'thread_id': 'order'}}
        await graph.ainvoke({'message': 'Conseille-moi sur le SKU D-C', 'customer_id': 'a'}, config)
        order = await graph.ainvoke({'message': 'Commande-moi deux de ceux-là', 'customer_id': 'a'}, config)
        assert order['pending_order'] is None
        stock.execute.assert_not_awaited()
        assert order['last_advice_skus'] == []
        # Prepare a fully explicit ORDER, then retire its persisted confirmation with Advice.
        classify.execute.side_effect = lambda msg: IntentResult(intent=Intent.ORDER, product_name='Perceuse Concurrence Synthétique V017', quantity=2) if msg.startswith('Commande') else IntentResult(intent=Intent.PRODUCT_ADVICE) if 'Conseille' in msg else IntentResult(intent=Intent.UNKNOWN)
        prepared = await graph.ainvoke({'message': 'Commande-moi 2 Perceuse Concurrence Synthétique V017', 'customer_id': 'a'}, config)
        assert prepared['active_order_confirmation'] is not None
        advice = await graph.ainvoke({'message': 'Conseille-moi sur le SKU D-C', 'customer_id': 'a'}, config)
        assert advice['active_order_confirmation'] is None and advice['pending_order'] is None
        confirmation = await graph.ainvoke({'message': 'oui confirme', 'customer_id': 'a'}, config)
        assert confirmation['pending_order'] is None
    run(scenario())
    finalizer.execute.assert_not_awaited()


@pytest.mark.parametrize('field,value', [('price', True), ('price', float('nan')), ('price', float('inf')),
    ('stock', True), ('stock', 1.5), ('stock', -1)])
def test_strict_current_mongo_read_rejects_malformed_raw_facts(field, value):
    doc = dict(_id='id', sku='D-C', name='Actual', category='DRILL', price=99, stock=2)
    doc[field] = value
    collection = Mock()
    collection.find.return_value.limit.return_value = [doc]
    with pytest.raises(ValueError, match='Malformed'):
        run(MongoProductRepository({'products': collection}).find_advice_by_sku('D-C'))


def test_named_product_catalogue_failure_and_invalid_llm_are_controlled():
    use, repo, llm = service()
    repo.find_advice_by_sku = AsyncMock(side_effect=ConnectionError('private URI'))
    result = run(use.execute('Conseille-moi sur le SKU D-C'))
    assert result['advice_status'] == 'CATALOGUE_UNAVAILABLE' and 'private' not in str(result)
    llm.generate.assert_not_awaited()
    use, _, llm = service()
    llm.generate.return_value = '{"product":"INVENTED", "power":"999W"}'
    result = run(use.execute('Conseille-moi sur le SKU D-C'))
    assert skus(result) == ['D-C'] and 'INVENTED' not in str(result) and '999W' not in str(result)


def test_stock_constraint_is_an_advice_followup_and_can_be_removed():
    graph, _, _, _ = advice_graph()
    async def scenario():
        config = {'configurable': {'thread_id': 'stock-constraint'}}
        async def ask(message):
            return await graph.ainvoke({'message': message, 'customer_id': 'a'}, config)
        await ask('Je cherche une perceuse disponible à moins de 100 euros.')
        result = await ask('Compare le premier avec deux autres en stock.')
        assert skus(result) == ['D-A', 'D-B', 'D-C']
        changed = await ask('Et si je veux une visseuse en stock plutôt ?')
        assert skus(changed) == ['S-A'] and changed['budget'] == 100
        removed = await ask('Et sans contrainte de disponibilité ?')
        assert removed['last_advice_criteria']['available'] is None
        assert removed['budget'] == 100 and removed['category'] == C.SCREWDRIVER
    run(scenario())


def test_comparison_budget_followup_keeps_verified_reference_and_count():
    graph, _, _, _ = advice_graph()
    async def scenario():
        config = {'configurable': {'thread_id': 'compare-budget'}}
        await graph.ainvoke({'message': 'Compare le SKU D-H avec quatre autres disponibles.', 'customer_id': 'a'}, config)
        result = await graph.ainvoke({'message': 'Et si je descends à 80 euros ?', 'customer_id': 'a'}, config)
        assert skus(result) == ['D-H', 'D-A', 'D-B']
        assert result['last_advice_criteria']['limit'] == 4
        assert result['recommendations'][0]['role'] == 'reference'
        assert result['recommendations'][0]['matches_criteria'] is False
        assert 'hors budget' in result['recommendations'][0]['explanation']
    run(scenario())


@pytest.mark.parametrize('suffix,expected', [('Max', ['P-2']), ('Max 12V', []), ('Max Inconnue', [])])
def test_named_product_suffix_is_never_dropped_as_an_incomplete_budget(suffix, expected):
    from stockpilot.domain.entities.product import Product
    use, _, _ = service([Product('P-1', 'Perceuse Pro', C.DRILL, 50, 2),
                         Product('P-2', 'Perceuse Pro Max', C.DRILL, 60, 2)])
    result = run(use.execute(f'Conseille-moi sur la Perceuse Pro {suffix}.'))
    assert skus(result) == expected


def test_partial_name_and_single_result_followup_pass_through_real_classifier():
    graph, _, _, forbidden = advice_graph()
    async def scenario():
        config = {'configurable': {'thread_id': 'partial-and-count'}}
        result = await graph.ainvoke({'message': 'Conseille-moi sur Concurrence Synthétique V017.', 'customer_id': 'a'}, config)
        assert skus(result) == ['D-C']
        await graph.ainvoke({'message': 'Je cherche une perceuse disponible à moins de 100 euros.', 'customer_id': 'a'}, config)
        one = await graph.ainvoke({'message': 'Montre-moi seulement un produit.', 'customer_id': 'a'}, config)
        assert skus(one) == ['D-A']
        two = await graph.ainvoke({'message': 'Montre-moi deux produits.', 'customer_id': 'a'}, config)
        assert skus(two) == ['D-A', 'D-B'] and two['budget'] == 100
    run(scenario())
    forbidden.execute.assert_not_awaited()


@pytest.mark.parametrize('message,expected', [
    ('Je cherche une perceuse disponible, à moins de 100 euros.', ['D-A', 'D-B', 'D-C']),
    ('Je cherche une perceuse à moins de 100 euros et disponible.', ['D-A', 'D-B', 'D-C']),
    ('Je cherche une perceuse non disponible.', ['D-O']),
])
def test_generic_constraints_with_connectors_stay_generic(message, expected):
    use, _, _ = service()
    result = run(use.execute(message))
    assert skus(result) == expected
    assert extract_criteria(message).mode == 'search'


def test_new_comparison_reference_uses_its_category_unless_explicitly_overridden():
    graph, _, _, _ = advice_graph()
    async def scenario():
        config = {'configurable': {'thread_id': 'new-reference-category'}}
        await graph.ainvoke({'message': 'Je cherche une perceuse disponible à moins de 100 euros.', 'customer_id': 'a'}, config)
        result = await graph.ainvoke({'message': 'Compare le SKU S-A avec deux autres.', 'customer_id': 'a'}, config)
        assert skus(result) == ['S-A']
        assert result['category'] == C.SCREWDRIVER
        explicit = await graph.ainvoke({'message': 'Compare le SKU S-A avec deux autres perceuses disponibles.', 'customer_id': 'a'}, config)
        assert skus(explicit) == ['S-A', 'D-A', 'D-B']
        followup = await graph.ainvoke({'message': 'Et si je monte le budget à 150 euros ?', 'customer_id': 'a'}, config)
        assert skus(followup) == ['S-A', 'D-A', 'D-B']
        assert followup['category'] == C.DRILL
    run(scenario())


@pytest.mark.parametrize('name', ['Concurrence Synthétique', 'Perceuse Concurrence Synthétique'])
def test_two_discriminating_name_tokens_resolve_only_in_read_only_advice(name):
    from stockpilot.application.use_cases.resolve_product import ResolveProductUseCase, ResolutionStatus
    graph, repo, _, forbidden = advice_graph()
    async def scenario():
        result = await graph.ainvoke({'message': f'Conseille-moi sur {name}.', 'customer_id': 'a'},
                                    {'configurable': {'thread_id': 'partial-two'}})
        assert skus(result) == ['D-C']
        # ORDER keeps its stricter resolution policy.
        assert (await ResolveProductUseCase(repo).execute(name)).status == ResolutionStatus.NOT_FOUND
        repo.products.append(replace(catalogue()[2], sku='D-X', name='Perceuse Concurrence Synthétique V018'))
        ambiguous = await graph.ainvoke({'message': f'Conseille-moi sur {name}.', 'customer_id': 'a'},
                                       {'configurable': {'thread_id': 'partial-two'}})
        assert ambiguous['advice_status'] == 'CLARIFICATION_REQUIRED' and skus(ambiguous) == []
    run(scenario())
    forbidden.execute.assert_not_awaited()
