"""V13.C1 uses only the UUID database on an explicitly verified disposable Mongo."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from test_mongo_transaction_integration import database  # noqa: F401
from integration_targets import postgres_test_schema
from evals.fixtures.catalogue_advice import advice_graph, catalogue
from stockpilot.application.use_cases.catalogue_advice import CatalogueAdviceUseCase, extract_criteria
from stockpilot.infrastructure.persistence.mongodb.product_repository import MongoProductRepository
from stockpilot.infrastructure.persistence.postgres.checkpointer import open_checkpointer

pytestmark = pytest.mark.integration


@pytest.fixture
def repository(database):
    database.products.insert_many([{'sku': p.sku, 'name': p.name, 'category': p.category.value,
                                   'price': p.price, 'stock': p.stock} for p in catalogue()])
    return MongoProductRepository(database)


def test_mongo_synthetic_filters_identity_ambiguity_and_comparison_are_read_only(repository, database):
    use = CatalogueAdviceUseCase(repository, SimpleNamespace(generate=AsyncMock(side_effect=ConnectionError('Ollama offline'))))
    before = {name: list(database[name].find({})) for name in database.list_collection_names()}
    async def scenario():
        criteria = extract_criteria('Je cherche une perceuse disponible à moins de 100 euros.')
        assert [p.sku for p in await repository.find_advice_candidates(criteria)] == ['D-A', 'D-B', 'D-C']
        result = await use.execute('Je cherche une perceuse disponible à moins de 100 euros.')
        assert [p['sku'] for p in result['recommendations']] == ['D-A', 'D-B', 'D-C']
        inclusive = await use.execute('Propose cinq perceuses disponibles budget 100 euros')
        assert [p['sku'] for p in inclusive['recommendations']] == ['D-A', 'D-B', 'D-C', 'D-E']
        for message in ('Demander conseil sur la Perceuse Concurrence Synthétique V017',
                        'Peux-tu me conseiller sur la Perceuse Concurrence Synthétique V017',
                        'Conseille-moi sur SKU D-C',
                        'Conseille-moi sur Concurrence Synthétique',
                        'Conseille-moi sur la Perceuse Concurrence Synthétique V017',
                        'Conseille-moi sur Concurrence Synthétique V017', 'Conseille-moi sur le SKU D-C'):
            result = await use.execute(message)
            actual, = result['recommendations']
            stored = database.products.find_one({'sku': 'D-C'})
            assert (actual['sku'], actual['product_name'], actual['category'], actual['price'], actual['stock']) == (
                stored['sku'], stored['name'], stored['category'], stored['price'], stored['stock'])
        for prefix in ('Conseille-moi sur', 'Demander conseil sur'):
            ambiguous = await use.execute(prefix + ' la Perceuse Pro V017')
            absent = await use.execute(prefix + ' la Perceuse Inconnue V017')
            assert ambiguous['advice_status'] == 'CLARIFICATION_REQUIRED' and ambiguous['recommendations'] == []
            assert absent['advice_status'] == 'NO_RESULTS' and absent['recommendations'] == []
        compare = await use.execute('Compare le SKU D-H avec deux autres disponibles à moins de 100 euros')
        assert [p['sku'] for p in compare['recommendations']] == ['D-H', 'D-A', 'D-B']
        assert compare['recommendations'][0]['matches_criteria'] is False
    asyncio.run(scenario())
    assert before == {name: list(database[name].find({})) for name in database.list_collection_names()}


@pytest.mark.parametrize('field,value', [('stock', True), ('stock', 1.5), ('price', True), ('price', float('nan'))])
def test_mongo_targeted_raw_validation(repository, database, field, value):
    database.products.update_one({'sku': 'D-C'}, {'$set': {field: value}})
    use = CatalogueAdviceUseCase(repository, SimpleNamespace(generate=AsyncMock()))
    result = asyncio.run(use.execute('Conseille-moi sur le SKU D-C'))
    assert result['advice_status'] == 'CATALOGUE_UNAVAILABLE' and result['recommendations'] == []


def test_advice_constraints_and_references_survive_postgres_reconnection(repository):
    async def scenario(uri):
        config = {'configurable': {'thread_id': 'v13c1-persistent'}}
        async with open_checkpointer(uri) as saver:
            graph, _, _, _ = advice_graph(repository, saver)
            first = await graph.ainvoke({'message': 'Je cherche une perceuse disponible à moins de 100 euros.', 'customer_id': 'a'}, config)
            assert first['last_advice_skus'] == ['D-A', 'D-B', 'D-C']
        async with open_checkpointer(uri) as saver:
            graph, _, _, forbidden = advice_graph(repository, saver)
            modified = await graph.ainvoke({'message': 'Et si je monte le budget à 150 euros ?', 'customer_id': 'a'}, config)
            assert modified['last_advice_criteria']['maximum'] == 150
            assert modified['last_advice_criteria']['available'] is True
            compare = await graph.ainvoke({'message': 'Compare le premier avec deux autres.', 'customer_id': 'a'}, config)
            assert [p['sku'] for p in compare['recommendations']] == ['D-A', 'D-B', 'D-C']
            foreign = await graph.ainvoke({'message': 'Compare le premier avec deux autres.', 'customer_id': 'b'}, config)
            assert foreign['advice_status'] == 'CLARIFICATION_REQUIRED' and foreign['last_advice_skus'] == []
            forbidden.execute.assert_not_awaited()
    with postgres_test_schema() as uri:
        asyncio.run(scenario(uri))
