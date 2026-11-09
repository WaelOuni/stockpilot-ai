"""Exact final residuals through disposable Mongo/PG and persisted checkpoints."""
import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import psycopg
from psycopg import sql
import pytest

from evals.assistant_v025.campaign import CORPUS
from test_assistant_v025_integration import corpus_assistant  # noqa: F401
from test_tenant_assistant_integration import assistant, environment, db, orders, stock, http  # noqa: F401
from test_tenant_orders_mongo_integration import REQUESTS, context
from stockpilot.application.ports.tenant_knowledge import TenantDataConflict, TenantDataNotFound
from stockpilot.application.services.identity_authorization import AuthorizationDenied

pytestmark=pytest.mark.integration
CASES={c['id']:c for c in CORPUS}


def send(service, env, conv, turn, message, customer=None, actor='a1'):
    return asyncio.run(service.send(REQUESTS[actor],context(env.orders,actor),conv,turn,message,customer))


def open_conversation(corpus_assistant, name):
    (service,_,env),_=corpus_assistant
    service.llm.classify_intent.side_effect=TimeoutError('synthetic unavailable classifier')
    conv=service.store.open(REQUESTS['a1'],context(env.orders),name).conversation_id
    return service,env,conv


def database_facts(db):
    return [list(db[name].find({}).sort('_id',1)) for name in ('b2b_products','b2b_orders','b2b_order_operations')]


def test_G10_12_exact_abbreviated_preparation_is_persisted_idempotent_and_unreserved(corpus_assistant,db):
    service,env,conv=open_conversation(corpus_assistant,'final-order')
    before=list(db.b2b_products.find({}).sort('_id',1))
    message=CASES['G10-12']['message']
    turn=send(service,env,conv,'G10-12',message,'customer-org-a')
    assert turn['reply']['intent']=='ORDER_PREPARE' and turn['reply']['can_confirm']
    order=turn['reply']['order']
    assert order['status']=='prepared' and order['created_by']=='a1' and order['customer_id']=='customer-org-a'
    assert [(l['sku'],l['quantity']) for l in order['lines']]==[('TEST-A-DRILL-001',2)]
    assert send(service,env,conv,'G10-12',message,'customer-org-a')==turn
    assert db.b2b_orders.count_documents({})==1 and db.b2b_order_operations.count_documents({})==1
    assert list(db.b2b_products.find({}).sort('_id',1))==before
    persisted=service.store.turn(REQUESTS['a1'],context(env.orders),conv,'G10-12')
    assert persisted['input']['message']==message and persisted['action']['lines']==[{'sku':'TEST-A-DRILL-001','quantity':2}]
    assert persisted['action']['session']==context(env.orders).authorization_context_id
    service.llm.classify_intent.assert_not_awaited()


@pytest.mark.parametrize('identity',['G12-06','G12-12'])
def test_exact_confirmation_and_staleness_guidance_never_reserves_or_rearms(corpus_assistant,db,identity):
    service,env,conv=open_conversation(corpus_assistant,'final-guidance-'+identity)
    prepared=send(service,env,conv,'prepare',CASES['G10-12']['message'],'customer-org-a')
    if identity=='G12-12':
        db.b2b_products.update_one({'organization_id':'org-a','sku':'TEST-A-DRILL-001'}, {'$set':{'quantity':4,'price_minor':12345}})
    before=database_facts(db)
    turn=send(service,env,conv,identity,CASES[identity]['message'])
    reply=turn['reply']
    assert reply['intent']==('CONFIRMATION_REQUIRED' if identity=='G12-06' else 'ORDER_STATUS')
    assert not reply['can_confirm'] and 'bouton' in reply['text']
    assert 'prépar' in reply['text'].lower() and database_facts(db)==before
    if identity=='G12-12':
        assert reply['products'][0]['available']==1 and reply['products'][0]['price_minor']==12345
        assert 'stocks' in reply['text'] and 'prix' in reply['text'] and 'revérifi' in reply['text']
    order=prepared['reply']['order']
    with pytest.raises(TenantDataConflict):
        asyncio.run(service.action(REQUESTS['a1'],context(env.orders),conv,'confirm','cannot-use-old-proof',order['order_id'],order['preparation_digest']))
    assert database_facts(db)==before
    assert service.store.turn(REQUESTS['a1'],context(env.orders),conv,identity)['action'] is None


@pytest.mark.parametrize('identity',['G14-01','G14-02','G14-04','G14-05','G14-06','G14-07'])
def test_exact_history_requests_preserve_snapshots_and_reverify_current_data(corpus_assistant,db,identity):
    service,env,conv=open_conversation(corpus_assistant,'final-history-'+identity)
    original_message='Quel est le stock de TEST-A-DRILL-001 ?'
    if identity=='G14-07':
        db.b2b_products.update_one({'organization_id':'org-a','sku':'TEST-A-DRILL-001'}, {'$set':{'quantity':8}})
    old=send(service,env,conv,'historical',original_message)
    if identity=='G14-07':
        # Synthetic historical observation, not VM data. Set only its timestamp
        # to yesterday; the product fixture is explicitly observed at five units.
        assert old['reply']['products'][0]['available']==5
        with psycopg.connect(env.uri) as connection:
            connection.execute(sql.SQL('UPDATE {}.assistant_turns SET created_at=%s WHERE conversation_id=%s AND turn_id=%s').format(sql.Identifier(env.schema)),
                               (datetime.now(UTC)-timedelta(days=1),conv,'historical'))
    if identity=='G14-04':
        send(service,env,conv,'second','Quel est le stock de DEMO-01-0225 ?')
    saved=deepcopy(service.store.turn(REQUESTS['a1'],context(env.orders),conv,'historical'))
    db.b2b_products.update_one({'organization_id':'org-a','sku':'TEST-A-DRILL-001'},{'$inc':{'quantity':7},'$set':{'price_minor':12345}})
    before=database_facts(db)
    result=send(service,env,conv,identity,CASES[identity]['message'])['reply']
    assert result['intent'] not in {'RAG','CLARIFICATION_REQUIRED'}
    assert not result['can_confirm'] and result['order'] is None
    if identity in ('G14-01','G14-02'):
        assert original_message in result['text']
    elif identity=='G14-05':
        assert result['products']==old['reply']['products'] and old['reply']['text'] in result['text']
    else:
        assert result['products'][0]['available']==old['reply']['products'][0]['available']+7
        assert result['products'][0]['price_minor']==12345 and 'actuelle' in result['text']
        if identity=='G14-04':
            assert result['intent']=='COMPARISON' and [p['sku'] for p in result['products']]==['TEST-A-DRILL-001','DEMO-01-0225']
    assert service.store.turn(REQUESTS['a1'],context(env.orders),conv,'historical')==saved
    assert database_facts(db)==before
    service.llm.classify_intent.assert_not_awaited()
    # Conversation owner and tenant are rechecked even for deterministic text.
    for actor in ('a3','b1'):
        with pytest.raises((AuthorizationDenied,TenantDataNotFound)):
            send(service,env,conv,'foreign-'+actor,CASES[identity]['message'],actor=actor)
    other=service.store.open(REQUESTS['a1'],context(env.orders),'empty-'+identity).conversation_id
    absent=send(service,env,other,'absent',CASES[identity]['message'])['reply']
    assert absent['intent']=='CLARIFICATION_REQUIRED' and not absent['products']


@pytest.mark.parametrize('change',['role','session','organization','identity'])
def test_history_rechecks_permissions_session_tenant_and_product_identity(corpus_assistant,db,change,monkeypatch):
    service,env,conv=open_conversation(corpus_assistant,'history-revocation')
    send(service,env,conv,'prior','Quel est le stock de TEST-A-DRILL-001 ?')
    old_context=context(env.orders)
    if change=='role':
        db.b2b_memberships.update_one({'user_id':'a1'},{'$set':{'status':'suspended'}})
    elif change=='session':
        monkeypatch.setattr(env.orders[1],'current',lambda request: replace(old_context,authorization_context_id='new-synthetic-session'))
    elif change=='organization':
        db.b2b_memberships.update_one({'user_id':'a1'},{'$set':{'organization_id':'org-b'}})
    else:
        db.b2b_products.delete_one({'organization_id':'org-a','sku':'TEST-A-DRILL-001'})
    before=database_facts(db)
    if change=='identity':
        reply=send(service,env,conv,'refresh',CASES['G14-06']['message'])['reply']
        assert reply['intent']=='CLARIFICATION_REQUIRED' and not reply['products']
    else:
        with pytest.raises((AuthorizationDenied,TenantDataNotFound)):
            asyncio.run(service.send(REQUESTS['a1'],old_context,conv,'refresh',CASES['G14-06']['message']))
    assert database_facts(db)==before
