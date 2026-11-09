"""Exact corpus requests at real disposable PG/Mongo boundaries, no model claim.

These complement the opt-in real Ollama evidence. The generator is deliberately
synthetic here; assertions concern the actual PG scope, persisted authorization,
catalogue priority and absence of writes, never prose quality.
"""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from evals.assistant_v025.campaign import CORPUS
from evals.assistant_v025.real_rag import DOCUMENTS
from test_tenant_assistant_integration import assistant, send  # noqa: F401
from test_tenant_knowledge_integration import command, environment, ingest  # noqa: F401
from test_tenant_orders_mongo_integration import REQUESTS, context, db, orders  # noqa: F401
from test_tenant_stock_operations_mongo_integration import stock  # noqa: F401
from stockpilot.application.dto.intent_result import IntentResult
from stockpilot.application.services.identity_authorization import AuthorizationDenied
from stockpilot.application.services.tenant_retrieval import TenantRetrieval

pytestmark = pytest.mark.integration
CASES = [c for c in CORPUS if c['id'] in {f'G15-{i:02}' for i in range(1, 11)}]


@pytest.mark.parametrize('case', CASES, ids=lambda case: case['id'])
def test_original_rag_request_uses_only_current_authorized_pg_passages(environment, db, case):
    e = environment
    if case['id'] != 'G15-05':
        for doc in DOCUMENTS[:4]:
            ingest(e, command(doc['id'], 'ingest-' + doc['id'], doc['content']))
    ingest(e, command('foreign-guide', 'ingest-foreign', 'FOREIGN_DECOY_883: politique étrangère.'), 'admin-b')
    # The existing permission model is organization + RAG_READ, not a per-document
    # user ACL. A non-active document must also stay out of the model context.
    ingest(e, command('withdrawn-guide', 'ingest-withdrawn', 'WITHDRAWN_DECOY_991: texte retiré.'))
    e.repo.delete(REQUESTS['admin-a'], context(e.orders, 'admin-a'), 'withdrawn-guide',
                  operation_id='withdraw-document', expected_revision=1)
    generator = SimpleNamespace(generate=AsyncMock(return_value='Réponse synthétique de transport [S1].'))
    retrieval = TenantRetrieval(e.repo, e.embeddings, generator, model='synthetic-768', version='v1')
    ctx = context(e.orders)
    watched = ('b2b_orders', 'b2b_order_operations', 'b2b_products', 'b2b_stock_movements')
    before = {name: list(db[name].find({}).sort('_id', 1)) for name in watched}
    result = asyncio.run(retrieval.execute(REQUESTS['a1'], ctx, case['message']))
    if case['id'] == 'G15-05':
        assert result is None
        generator.generate.assert_not_awaited()
    else:
        prompt = generator.generate.await_args.kwargs['prompt']
        assert json.loads(prompt.split('<question>\n', 1)[1].split('\n</question>', 1)[0]) == case['message']
        encoded = prompt.split('<documents>\n', 1)[1].split('\n</documents>', 1)[0]
        passages = json.loads(encoded)
        assert len(passages) == 3
        assert {p['document_id'] for p in passages} <= {d['id'] for d in DOCUMENTS[:4]}
        assert not any(term in prompt + result for term in ('FOREIGN_DECOY_883', 'WITHDRAWN_DECOY_991'))
        assert all(p['content'] in {d['content'] for d in DOCUMENTS[:4]} for p in passages)
    generation_calls = generator.generate.await_count
    embedding_calls = e.embeddings.embed.await_count
    # Reuse the old context after revoking persisted membership: a cached context
    # or an earlier retrieval may not grant continuing read permission.
    db.b2b_memberships.update_one({'membership_id': 'm-a1'}, {'$set': {'status': 'suspended'}})
    with pytest.raises(AuthorizationDenied):
        asyncio.run(retrieval.execute(REQUESTS['a1'], ctx, case['message']))
    assert generator.generate.await_count == generation_calls
    assert e.embeddings.embed.await_count == embedding_calls
    assert {name: list(db[name].find({}).sort('_id', 1)) for name in watched} == before


def test_g15_08_document_price_never_overrides_current_catalogue(assistant, db):
    service, _, e = assistant
    message = next(c['message'] for c in CASES if c['id'] == 'G15-08')
    db.b2b_products.update_one({'organization_id': 'org-a', 'sku': 'DRILL-001'},
                               {'$set': {'sku': 'DEMO-01-0225'}})
    ingest(e, command('legacy-price', 'ingest-legacy',
                     'Le produit DEMO-01-0225 valait 1,00 EUR dans cet ancien export.'))
    send(assistant, 'Je cherche DEMO-01-0225.')
    before = list(db.b2b_products.find({}).sort('_id', 1))
    service.llm.classify_intent.return_value = IntentResult(intent='STOCK_CHECK', product_name='produit')
    reply = send(assistant, message)['reply']
    assert [p['sku'] for p in reply['products']] == ['DEMO-01-0225']
    assert reply['products'][0]['price_minor'] == 4900
    assert 'catalogue' in reply['text'].lower()
    service.llm.generate.assert_not_awaited()
    assert not reply['can_confirm'] and reply['order'] is None
    assert list(db.b2b_products.find({}).sort('_id', 1)) == before
    assert db.b2b_orders.count_documents({}) == 0
    assert db.b2b_order_operations.count_documents({}) == 0
