"""Navigation through real guarded Mongo/PG and private persisted checkpoints."""
import asyncio
from copy import deepcopy

import pytest

from evals.assistant_v025.campaign import CORPUS
from test_assistant_v025_integration import corpus_assistant
from test_tenant_assistant_integration import assistant, environment, db, orders, stock, http  # noqa: F401
from test_tenant_orders_mongo_integration import REQUESTS, context
from test_assistant_v025_navigation import expected_rows, SEED
from stockpilot.application.ports.tenant_knowledge import TenantDataNotFound
from stockpilot.application.services.identity_authorization import AuthorizationDenied

pytestmark = pytest.mark.integration


def send(service, env, conv, identity, question):
    return asyncio.run(service.send(REQUESTS['a1'], context(env.orders), conv, identity, question))


def test_original_navigation_and_refinement_replay_through_persisted_checkpoints(corpus_assistant, db):
    (service, _, env), rows = corpus_assistant
    service.llm.classify_intent.side_effect = AssertionError('Navigation never calls a classifier')
    before = list(db.b2b_products.find({}).sort('_id', 1))
    for case in (c for c in CORPUS if c['id'].startswith('G08')):
        identity = case['id']
        conv = service.store.open(REQUESTS['a1'], context(env.orders), 'navigation-' + identity).conversation_id
        original = send(service, env, conv, 'seed', SEED)
        snapshot = deepcopy(original['reply'])
        reply = send(service, env, conv, 'navigate', case['message'])['reply']
        if identity in ('G08-12', 'G08-13', 'G08-14'):
            assert reply['intent'] == 'CLARIFICATION_REQUIRED' and not reply['products'], identity
        else:
            selected = expected_rows(rows, minimum=1 if identity in ('G08-10', 'G08-11') else 0,
                                     sort='price' if identity == 'G08-08' else 'sku' if identity == 'G08-09' else 'available')
            offset = 5 if identity in ('G08-01', 'G08-02', 'G08-03', 'G08-04') else 0
            if identity == 'G08-06': offset = (len(selected) - 1) // 5 * 5
            assert [p['sku'] for p in reply['products']] == [r['sku'] for r in selected[offset:offset + 5]], identity
            assert reply['total'] == len(selected)
            if identity == 'G08-04':
                compared = send(service, env, conv, 'compare-page', 'Compare les deux premières.')['reply']
                assert [p['sku'] for p in compared['products']] == [p['sku'] for p in reply['products'][:2]]
        assert service.store.turn(REQUESTS['a1'], context(env.orders), conv, 'seed')['reply'] == snapshot
    case = next(c for c in CORPUS if c['id'] == 'G13-04')
    conv = service.store.open(REQUESTS['a1'], context(env.orders), 'navigation-G13-04').conversation_id
    for n, question in enumerate(case['message'].split('⇒')):
        reply = send(service, env, conv, str(n), question.strip())['reply']
    selected = expected_rows(rows, minimum=1, maximum=4)
    assert [p['sku'] for p in reply['products']] == [r['sku'] for r in selected[5:10]]
    assert reply['total'] == len(selected)
    assert list(db.b2b_products.find({}).sort('_id', 1)) == before
    assert db.b2b_orders.count_documents({}) == db.b2b_order_operations.count_documents({}) == 0


def test_navigation_beyond_first_hundred_membership_and_revocation(corpus_assistant, db):
    (service, _, env), rows = corpus_assistant
    service.llm.classify_intent.side_effect = TimeoutError('synthetic offline provider')
    conv = service.store.open(REQUESTS['a1'], context(env.orders), 'navigation-large').conversation_id
    original = send(service, env, conv, 'seed', 'Quels produits ont au moins 0 unités disponibles ?')
    selected = expected_rows(rows, maximum=100)
    assert len(selected) > 100
    current = send(service, env, conv, 'page-25', 'Montre la page 25.')['reply']
    assert [p['sku'] for p in current['products']] == [r['sku'] for r in selected[120:125]]
    assert current['total'] == len(selected)
    # The exact member is outside both the first five cards and current page.
    target = selected[90]['sku']
    member = send(service, env, conv, 'member', f'Le SKU {target} est-il dans les 227 résultats ?')['reply']
    assert [p['sku'] for p in member['products']] == [target]
    for sku in ('TEST-B-001', 'TEST-UNKNOWN-404'):
        missing = send(service, env, conv, sku, f'Le SKU {sku} est-il dans les 227 résultats ?')['reply']
        assert missing['products'] == [] and missing['total'] == 0
        assert 'Aucune correspondance' in missing['text']
    # Current conditions are evaluated again; historic observed cards stay fixed.
    db.b2b_products.update_one({'organization_id': 'org-a', 'sku': target}, {'$set': {'status': 'inactive'}})
    assert not send(service, env, conv, 'removed', f'Le SKU {target} est-il dans les résultats ?')['reply']['products']
    assert service.store.turn(REQUESTS['a1'], context(env.orders), conv, 'seed')['reply'] == original['reply']
    fresh = service.store.open(REQUESTS['a1'], context(env.orders), 'navigation-fresh').conversation_id
    assert send(service, env, fresh, 'next', 'Montre la page suivante.')['reply']['intent'] == 'CLARIFICATION_REQUIRED'
    for actor in ('a3', 'b1'):
        with pytest.raises((AuthorizationDenied, TenantDataNotFound)):
            asyncio.run(service.send(REQUESTS[actor], context(env.orders, actor), conv, 'foreign', 'Montre la page suivante.'))
    db.b2b_memberships.update_one({'user_id': 'a1'}, {'$set': {'status': 'suspended'}})
    with pytest.raises(AuthorizationDenied): send(service, env, conv, 'revoked', 'Montre la page suivante.')
    assert db.b2b_orders.count_documents({}) == 0
