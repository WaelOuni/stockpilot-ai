"""Corpus IDs through real Mongo/PG/LangGraph; guarded disposable fixtures only."""
import asyncio
from copy import deepcopy

import pytest

from evals.assistant_v025.campaign import CORPUS, PRIOR, oracle, products, verify
from test_tenant_assistant_integration import assistant, environment, db, orders, stock, http  # noqa: F401
from test_tenant_catalogue_mongo_integration import document
from test_tenant_orders_mongo_integration import REQUESTS, context
from stockpilot.application.dto.intent_result import IntentResult
from stockpilot.application.services.identity_authorization import AuthorizationDenied

pytestmark = pytest.mark.integration


@pytest.fixture
def corpus_assistant(assistant, db):
    rows = products()
    # The imported UUID database fixture attests the service before any write.
    db.b2b_products.delete_many({})
    for r in rows:
        value = document(r['org'], r['sku'], price=r['price'], quantity=r['quantity'])
        value.update(name=r['name'], category=r['category'], reserved=r['reserved'], currency=r['currency'], status=r['status'])
        db.b2b_products.insert_one(value)
    return assistant, rows


def run(corpus_assistant, case, actor='a1', turn=None, customer=None):
    (service, _, env), _ = corpus_assistant
    ctx = context(env.orders, actor)
    conv = service.store.open(REQUESTS[actor], ctx, 'corpus-' + case['id']).conversation_id
    if case['id'] in PRIOR:
        asyncio.run(service.send(REQUESTS[actor], ctx, conv, 'prior-' + case['id'], PRIOR[case['id']]))
    return asyncio.run(service.send(REQUESTS[actor], ctx, conv, turn or case['id'], case['message'], customer)), conv


def test_supported_corpus_reads_use_real_scoped_filters_and_immutable_history(corpus_assistant, db):
    (service, _, env), rows = corpus_assistant
    service.llm.classify_intent.side_effect = AssertionError('Explicit discovery must not call the model')
    before = list(db.b2b_products.find({}).sort('_id', 1))
    cases = [c for c in CORPUS if oracle(c)['kind'] in ('list','exact','comparison','categories') and not oracle(c).get('empty')]
    for case in cases:
        turn, conv = run(corpus_assistant,case)
        assert turn['status']=='completed', case['id']
        verify(case,turn['reply'],rows,'org-a',before,list(db.b2b_products.find({}).sort('_id',1)),[])
        historical=deepcopy(turn['reply'])
        if turn['reply'].get('catalogue_search'):
            page=service.catalogue_results(REQUESTS['a1'],context(env.orders),conv,turn['turn_id'])
            assert page['total']==turn['reply']['total']
            assert service.store.turn(REQUESTS['a1'],context(env.orders),conv,turn['turn_id'])['reply']==historical
    assert db.b2b_orders.count_documents({})==0
    assert db.b2b_order_operations.count_documents({})==0


@pytest.mark.parametrize('role',['READ_ONLY','USER','MANAGER','ORGANIZATION_ADMIN'])
@pytest.mark.parametrize('actor',['a1','b1'])
def test_reported_queries_under_every_role_and_tenant(corpus_assistant,db,role,actor):
    (service,_,env), rows=corpus_assistant
    db.b2b_memberships.update_one({'user_id':actor},{'$set':{'role':role}})
    before=list(db.b2b_products.find({}).sort('_id',1))
    service.llm.classify_intent.side_effect=TimeoutError('synthetic provider outage')
    org='org-a' if actor=='a1' else 'org-b'
    for identity in ('G01-02','G06-01'):
        case=next(c for c in CORPUS if c['id']==identity)
        turn,conv=run(corpus_assistant,case,actor)
        verify(case,turn['reply'],rows,org,before,list(db.b2b_products.find({}).sort('_id',1)),[])
        from stockpilot.application.ports.tenant_knowledge import TenantDataNotFound
        for foreign in ('a3','b1') if actor=='a1' else ('a1','a3'):
            with pytest.raises((AuthorizationDenied,TenantDataNotFound)):
                service.read(REQUESTS[foreign],context(env.orders,foreign),conv)


@pytest.mark.parametrize('case',[c for c in CORPUS if c['id'].startswith('G10')],ids=lambda c:c['id'])
def test_order_corpus_prepares_only_verified_lines_without_reservation(corpus_assistant,db,case):
    (service,_,env),_=corpus_assistant
    service.llm.classify_intent.return_value=IntentResult(intent='ORDER')
    before=list(db.b2b_products.find({}).sort('_id',1))
    turn,conv=run(corpus_assistant,case,customer='customer-org-a')
    if case['id'] in ('G10-12','G10-14','G10-15'):
        assert turn['reply']['intent']=='CLARIFICATION_REQUIRED'
        assert turn['reply']['order'] is None
        assert db.b2b_orders.count_documents({})==0
    else:
        assert turn['reply']['intent']=='ORDER_PREPARE'
        order=turn['reply']['order']
        expected_sku='TEST-A-DRILL-001' if case['id']=='G10-10' else 'TEST-A-DRILL-002' if case['id']=='G10-11' else 'DEMO-01-0225'
        quantity=4 if case['id']=='G10-10' else 3 if case['id']=='G10-07' else 1 if case['id'] in ('G10-03','G10-11','G10-13') else 2
        assert order['status']=='prepared' and order['organization_id']=='org-a' and order['created_by']=='a1'
        assert [(line['sku'],line['quantity']) for line in order['lines']]==[(expected_sku,quantity)]
        assert turn['reply']['can_confirm'] is True
        replay=asyncio.run(service.send(REQUESTS['a1'],context(env.orders),conv,case['id'],case['message'],'customer-org-a'))
        assert replay==turn and db.b2b_orders.count_documents({})==1
    assert before==list(db.b2b_products.find({}).sort('_id',1))


@pytest.mark.parametrize('case',[c for c in CORPUS if c['id'].startswith('G11')],ids=lambda c:c['id'])
def test_order_negative_corpus_cannot_bypass_evidence(corpus_assistant,db,case):
    (service,_,_),_=corpus_assistant
    service.llm.classify_intent.return_value=IntentResult(intent='ORDER')
    before=list(db.b2b_products.find({}).sort('_id',1))
    turn,_=run(corpus_assistant,case,customer='customer-org-a')
    if case['id']=='G11-07':
        assert turn['reply']['intent']=='ORDER_PREPARE'
        assert turn['reply']['order']['lines'][0]['quantity']==6
        assert turn['reply']['order']['status']=='prepared'
    else:
        assert turn['reply']['intent']=='CLARIFICATION_REQUIRED'
        assert turn['reply']['order'] is None and not turn['reply']['can_confirm']
        assert db.b2b_orders.count_documents({})==0
    assert before==list(db.b2b_products.find({}).sort('_id',1))


def test_read_only_cannot_prepare_even_with_valid_corpus_order(corpus_assistant,db):
    (service,_,_),_=corpus_assistant
    service.llm.classify_intent.return_value=IntentResult(intent='ORDER')
    db.b2b_memberships.update_one({'user_id':'a1'},{'$set':{'role':'READ_ONLY'}})
    case=next(c for c in CORPUS if c['id']=='G10-01')
    with pytest.raises(AuthorizationDenied): run(corpus_assistant,case,customer='customer-org-a')
    assert db.b2b_orders.count_documents({})==0
