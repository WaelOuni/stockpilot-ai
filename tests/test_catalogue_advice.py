import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from stockpilot.application.use_cases.catalogue_advice import (
    AdviceCriteria, CatalogueAdviceUseCase, ClarificationRequired, extract_criteria,
)
from stockpilot.application.use_cases.classify_intent import ClassifyIntentUseCase
from stockpilot.application.dto.intent_result import IntentResult
from stockpilot.application.workflows.stockpilot_workflow import build_graph
from stockpilot.domain.entities.product import Product
from stockpilot.domain.enums.intent import Intent
from stockpilot.domain.enums.product_category import ProductCategory as C
from stockpilot.infrastructure.persistence.mongodb.product_repository import MongoProductRepository


def run(awaitable):
    return asyncio.run(awaitable)


def product(sku='A', price=99, stock=2, category=C.DRILL):
    return Product(sku, f'Outil {sku}', category, price, stock)


def service(products, response='{"raisons":["prix","categorie","stock"]}'):
    repo = SimpleNamespace(find_advice_candidates=AsyncMock(return_value=products),
                           find_by_sku=AsyncMock(side_effect=lambda sku: next(p for p in products if p.sku == sku)))
    llm = SimpleNamespace(generate=AsyncMock(return_value=response))
    return CatalogueAdviceUseCase(repo, llm), repo, llm


@pytest.mark.parametrize('question, category, maximum, available, limit, exclusive', [
    ('Je cherche une perceuse à moins de 150 €.', C.DRILL, 150, None, 1, True),
    ('Quelles visseuses sont disponibles ?', C.SCREWDRIVER, None, True, 3, False),
    ('Propose-moi trois produits budget 150', None, 150, None, 3, False),
    ('Deux scies indisponibles à 150,50 euros maximum', C.SAW, 150.5, False, 2, False),
    ('Une ponceuse pas en stock', C.SANDER, None, False, 1, False),
])
def test_explicit_constraints(question, category, maximum, available, limit, exclusive):
    actual = extract_criteria(question)
    assert (actual.category, actual.maximum, actual.available, actual.limit, actual.exclusive_maximum) == (
        category, maximum, available, limit, exclusive)


@pytest.mark.parametrize('question', [
    'Propose-moi trois produits dans mon budget.', 'Une perceuse ou une scie',
    'Des perceuses budget zéro', 'Perceuse moins de -10 euros', 'Perceuse 100 dollars',
    'Perceuse entre 100 et 150 €', 'Perceuse disponible et indisponible',
    'Propose 20 produits budget 150', 'Une perceuse budget 0 euros',
])
def test_ambiguity_never_queries_catalogue(question):
    use, repo, llm = service([])
    result = run(use.execute(question))
    assert result['advice_status'] == 'CLARIFICATION_REQUIRED'
    assert result['recommendations'] == []
    repo.find_advice_candidates.assert_not_awaited()
    llm.generate.assert_not_awaited()


def test_multiple_candidates_deterministic_and_revalidated():
    use, repo, llm = service([product('B', 100), product('A', 100), product('C', 200), product('D', 80, category=C.SAW)])
    result = run(use.execute('Propose trois perceuses moins de 150 euros'))
    assert [p['sku'] for p in result['recommendations']] == ['A', 'B']
    assert all('plafond strict de 150' in p['explanation'] for p in result['recommendations'])
    assert repo.find_by_sku.await_count == 4
    assert llm.generate.await_count == 1


@pytest.mark.parametrize('stock,expected', [(0, 'NO_RESULTS'), (3, 'RESULTS')])
def test_availability_recheck(stock, expected):
    use, repo, _ = service([product()])
    repo.find_by_sku = AsyncMock(return_value=product(stock=stock))
    assert run(use.execute('Quelles perceuses disponibles ?'))['advice_status'] == expected


def test_strict_budget_excludes_equal_price():
    use, _, _ = service([product(price=150)])
    assert run(use.execute('Perceuse moins de 150 euros'))['advice_status'] == 'NO_RESULTS'
    assert run(use.execute('Perceuse budget 150 euros'))['advice_status'] == 'RESULTS'


@pytest.mark.parametrize('response', [
    '{"raisons":["brushless", "prix", "stock"]}',
    '{"raisons":["prix","categorie","stock"],"answer":"INVENTED 18V"}',
    'INVENTED 18V', '{"raisons":["prix","prix","stock"]}',
])
def test_hallucinated_model_text_cannot_be_rendered(response):
    use, _, _ = service([product()], response)
    result = run(use.execute('Une perceuse moins de 150 euros'))
    assert result['advice_status'] == 'RESULTS'
    assert 'INVENTED' not in str(result) and 'brushless' not in str(result)
    assert '99' in result['recommendations'][0]['explanation']


def test_ollama_outage_keeps_verified_products_and_never_changes_other_intents():
    use, _, llm = service([product()])
    llm.generate.side_effect = ConnectionError('private endpoint')
    llm.classify_intent = AsyncMock(side_effect=ConnectionError('private endpoint'))
    classify = ClassifyIntentUseCase(llm)
    result = run(classify.execute('Je cherche une perceuse moins de 150 euros'))
    assert result.intent == Intent.PRODUCT_ADVICE
    answer = run(use.execute('Je cherche une perceuse moins de 150 euros'))
    assert answer['recommendations'][0]['sku'] == 'A'
    assert 'private' not in str(answer)
    for message in ['Commande-moi Bosch X100', 'Le stock Bosch X100 ?']:
        with pytest.raises(ConnectionError):
            run(classify.execute(message))


def test_mongo_failure_never_falls_back_to_rag_or_model_products():
    use, repo, llm = service([])
    repo.find_advice_candidates.side_effect = RuntimeError('private uri')
    result = run(use.execute('Une perceuse'))
    assert result['advice_status'] == 'CATALOGUE_UNAVAILABLE'
    assert 'private' not in str(result)
    llm.generate.assert_not_awaited()


def test_mongo_filters_price_category_stock_before_limit_and_stable_sort():
    collection = Mock()
    cursor = collection.find.return_value
    cursor.sort.return_value = cursor
    cursor.limit.return_value = [dict(_id='id', sku='A', name='Actual', category='DRILL', price=99, stock=2)]
    repository = MongoProductRepository({'products': collection})
    criteria = extract_criteria('Trois perceuses disponibles moins de 150 euros')
    assert run(repository.find_advice_candidates(criteria))[0].sku == 'A'
    collection.find.assert_called_once_with({'price': {'$gte': 0, '$lt': 150.0}, 'category': 'DRILL', 'stock': {'$gt': 0}})
    cursor.sort.assert_called_once_with([('price', 1), ('sku', 1), ('_id', 1)])
    cursor.limit.assert_called_once_with(3)


def test_production_advice_branch_uses_catalogue_not_rag_or_stock_name_resolution():
    catalogue, _, _ = service([product()])
    old = SimpleNamespace(execute=AsyncMock(side_effect=AssertionError('Do not use RAG names')))
    stock = SimpleNamespace(execute=AsyncMock(side_effect=AssertionError('No fuzzy identity')))
    graph = build_graph(
        SimpleNamespace(execute=AsyncMock(return_value=IntentResult(intent=Intent.PRODUCT_ADVICE))),
        stock, old, old, old, get_order_status=old, catalogue_advice=catalogue,
    )
    result = run(graph.ainvoke({'message': 'Une perceuse budget 150 euros', 'customer_id': 'a'}))
    assert result['recommendations'][0]['sku'] == 'A'
    old.execute.assert_not_awaited()
    stock.execute.assert_not_awaited()


def test_classifier_cannot_turn_explicit_advice_into_an_order_or_invent_budget():
    llm = SimpleNamespace(classify_intent=AsyncMock(return_value=IntentResult(
        intent=Intent.ORDER, budget=1, product_name='INVENTED', quantity=5)))
    result = run(ClassifyIntentUseCase(llm).execute('Je cherche une perceuse moins de 150 euros'))
    assert result.intent == Intent.PRODUCT_ADVICE
    use, repo, _ = service([product(price=100)])
    # Criteria are extracted from the message, never from the model's budget/name.
    assert run(use.execute('Je cherche une perceuse moins de 150 euros'))['recommendations'][0]['sku'] == 'A'
    assert repo.find_advice_candidates.await_args.args[0].maximum == 150


def test_same_thread_budget_followup_uses_user_evidence_not_llm_summary():
    from langgraph.checkpoint.memory import InMemorySaver
    catalogue, repo, _ = service([product('A'), product('B'), product('C')])
    classify = SimpleNamespace(execute=AsyncMock(return_value=IntentResult(intent=Intent.PRODUCT_ADVICE, budget=9999)))
    forbidden = SimpleNamespace(execute=AsyncMock(side_effect=AssertionError('Unrelated use case')))
    graph = build_graph(classify, forbidden, forbidden, forbidden, forbidden,
                        get_order_status=forbidden, catalogue_advice=catalogue, checkpointer=InMemorySaver())
    async def conversation():
        config = {'configurable': {'thread_id': 'same-owner'}}
        first = await graph.ainvoke({'message': 'Je cherche une perceuse moins de 150 euros', 'customer_id': 'a'}, config)
        assert first['budget'] == first['last_budget'] == 150
        assert first['product_name'] is None
        followup = await graph.ainvoke({'message': 'Propose-moi trois produits dans mon budget.', 'customer_id': 'a'}, config)
        assert len(followup['recommendations']) == 3
        assert repo.find_advice_candidates.await_args.args[0].maximum == 150
        # Another owner in even the same test namespace cannot inherit the budget.
        foreign = await graph.ainvoke({'message': 'Propose-moi trois produits dans mon budget.', 'customer_id': 'b'}, config)
        assert foreign['advice_status'] == 'CLARIFICATION_REQUIRED'
    run(conversation())


def test_advice_cards_clear_on_next_stock_turn_and_threads_remain_isolated():
    from langgraph.checkpoint.memory import InMemorySaver
    catalogue, _, _ = service([product()])
    classify = SimpleNamespace(execute=AsyncMock(side_effect=[
        IntentResult(intent=Intent.PRODUCT_ADVICE),
        IntentResult(intent=Intent.STOCK_CHECK, product_name='Outil A'),
        IntentResult(intent=Intent.PRODUCT_ADVICE),
    ]))
    stock = SimpleNamespace(execute=AsyncMock(return_value=product()))
    forbidden = SimpleNamespace(execute=AsyncMock(side_effect=AssertionError('Mutation forbidden')))
    graph = build_graph(classify, stock, forbidden, forbidden, forbidden,
                        get_order_status=forbidden, catalogue_advice=catalogue, checkpointer=InMemorySaver())
    async def conversation():
        first = {'configurable': {'thread_id': 'first'}}
        await graph.ainvoke({'message': 'Une perceuse budget 150 euros', 'customer_id': 'a'}, first)
        status = await graph.ainvoke({'message': 'Stock Outil A ?', 'customer_id': 'a'}, first)
        assert status['recommendations'] == []
        second = await graph.ainvoke({'message': 'Propose-moi trois produits dans mon budget.', 'customer_id': 'a'},
                                    {'configurable': {'thread_id': 'second'}})
        assert second['advice_status'] == 'CLARIFICATION_REQUIRED'
    run(conversation())


@pytest.mark.parametrize('question', ['Perceuse moins de 150', 'Perceuse max 150', 'Perceuse budget: 150'])
def test_numeric_ceiling_without_currency_is_not_ignored(question):
    assert extract_criteria(question).maximum == 150


@pytest.mark.parametrize('question', ['Perceuse 1 500 euros', 'Perceuse 150,123 euros'])
def test_ambiguous_numeric_format_requires_clarification(question):
    with pytest.raises(ClarificationRequired):
        extract_criteria(question)


@pytest.mark.parametrize('price,stock', [(True, 2), (99, True), (99, -1)])
def test_malformed_mongo_facts_cannot_be_recommended(price, stock):
    collection = Mock()
    cursor = collection.find.return_value
    cursor.sort.return_value = cursor
    cursor.limit.return_value = [dict(_id='id', sku='A', name='Actual', category='DRILL', price=price, stock=stock)]
    repository = MongoProductRepository({'products': collection})
    with pytest.raises(ValueError, match='Malformed'):
        run(repository.find_advice_candidates(AdviceCriteria(category=C.DRILL)))


@pytest.mark.parametrize('database', ['stockpilot', 'stockpilot_demo', 'postgres'])
def test_no_seed_browser_harness_refuses_non_e2e_targets(database, monkeypatch):
    import scripts.verify_advice_browser as harness
    monkeypatch.setenv('MONGO_DATABASE', database)
    monkeypatch.setattr(harness, 'MongoClient', lambda *args, **kwargs: pytest.fail('Must not connect'))
    with pytest.raises(ValueError, match='Explicit stockpilot_e2e'):
        harness.settings()


@pytest.mark.parametrize('uri', ['mongodb://localhost/stockpilot?replicaSet=rs0', 'mongodb://localhost/stockpilot_e2e'])
def test_no_seed_browser_harness_refuses_wrong_uri_before_connection(uri, monkeypatch):
    import scripts.verify_advice_browser as harness
    monkeypatch.setenv('MONGO_DATABASE', 'stockpilot_e2e')
    monkeypatch.setenv('E2E_MONGO_URI', uri)
    with pytest.raises(ValueError, match='E2E Mongo URI'):
        harness.settings()


def test_named_stock_question_keeps_original_stock_path():
    llm = SimpleNamespace(classify_intent=AsyncMock(return_value=IntentResult(
        intent=Intent.STOCK_CHECK, product_name='Bosch X100', raw_product='perceuse Bosch X100')))
    result = run(ClassifyIntentUseCase(llm).execute('Quelles perceuses Bosch X100 sont disponibles ?'))
    assert result.intent == Intent.STOCK_CHECK
    assert result.product_name == 'Bosch X100'
