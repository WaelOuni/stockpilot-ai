"""Recorded model outputs through real ASGI, Mongo and reopened PostgreSQL."""
import asyncio,json,os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4
import pytest
from test_mongo_transaction_integration import database
from test_postgres_checkpoint_integration import checkpoint_uri
from test_v1233_reference_checkpoint_integration import harness,post,seed,OWNER
from test_v1234_reference_ownership_integration import as_customer
from stockpilot.application.dto.intent_result import IntentResult
from stockpilot.application.use_cases.classify_intent import ClassifyIntentUseCase
from stockpilot.domain.enums.intent import Intent
from stockpilot.infrastructure.persistence.postgres.checkpointer import open_checkpointer
from stockpilot.presentation.api.routes.assistant import customer_thread_key
from test_v1238_classifier_regression import A,B,RAW_A,RAW_B
pytestmark=pytest.mark.integration

def setup(database):
    _,products,orders,tx,classifier,app=harness(database)
    vis=database.products.insert_one({'sku':'DEMO-SCREW-0001','name':'Vis Synthétiques V016','category':'OTHER','price':1.,'stock':60}).inserted_id
    conc=database.products.insert_one({'sku':'DEMO-CONC-0001','name':'Perceuse Concurrence V016','category':'DRILL','price':1.,'stock':12}).inserted_id
    llm=SimpleNamespace(classify_intent=AsyncMock())
    llm.pipeline_trace=[]
    async def traced(message):
        normalized=await ClassifyIntentUseCase(llm).execute(message)
        llm.pipeline_trace.append({'message':message,'raw_validated':llm.classify_intent.return_value.model_dump(mode='json'),'normalized':normalized.model_dump(mode='json')})
        return normalized
    classifier.execute.side_effect=traced
    return vis,conc,products,orders,tx,llm,app

def frozen(db):return {n:list(db[n].find({}).sort('_id',1)) for n in ['products','orders']}

def evidence(name,value):
    folder=os.environ.get('V1238_EVIDENCE_DIR')
    if folder:
        path=Path(folder);path.mkdir(parents=True,exist_ok=True)
        (path/(name+'.json')).write_text(json.dumps(value,indent=2,ensure_ascii=False,default=str))

async def cp(saver,t):
    item=await saver.aget_tuple({'configurable':{'thread_id':customer_thread_key(OWNER,t)}})
    assert item
    return item.checkpoint['channel_values']


def test_recorded_demi_douzaine_prepares_confirms_six_and_reopens_read_only(database,checkpoint_uri):
    vis,_,_,_,tx,llm,app=setup(database);t=uuid4().hex;before=frozen(database)
    llm.classify_intent.return_value=IntentResult.model_validate_json(RAW_A)
    async def scenario():
        async with open_checkpointer(checkpoint_uri) as saver:
            api=app(saver);response=await post(api,t,A);assert response.status_code==200;prepared=response.json()
            assert prepared['order_status']=='PENDING_CONFIRMATION' and prepared['quantity']==6
            p=prepared['pending_order'];assert p['quantity']==6 and p['product_document_id']==str(vis)
            assert p['quantity_evidence']['quantity']==6 and p['quantity_evidence']['message']==A
            assert frozen(database)==before
            persisted=await cp(saver,t);assert persisted['active_order_confirmation']['operation_id']==p['operation_id']
            confirmed=(await post(api,t,'oui confirme')).json();assert confirmed['order_status']=='CONFIRMED'
            order=database.orders.find_one({'operation_id':p['operation_id']});assert order and order['quantity']==6 and str(order['reserved_product_id'])==str(vis)
            assert database.products.find_one({'_id':vis})['stock']==54
        after=frozen(database)
        async with open_checkpointer(checkpoint_uri) as saver:
            replay=(await post(app(saver),t,'oui')).json()
            assert replay['created_order_id']==confirmed['created_order_id'] and 'déjà' in replay['response'].lower()
            assert replay['pending_order']['operation_id']==p['operation_id']
            assert frozen(database)==after
            evidence('A-pipeline',{'message':A,'raw':RAW_A,'classification_stages':llm.pipeline_trace,'before':before,'prepared':prepared,'prepared_checkpoint':persisted,'confirmed':confirmed,'reopened_checkpoint':await cp(saver,t),'replay':replay,'after':after})
        tx.products.reserve_stock_sku.assert_called_once()
    asyncio.run(scenario())


def test_recorded_counted_sku_checks_stock_and_cannot_confirm_after_reopen(database,checkpoint_uri):
    _,conc,_,_,tx,llm,app=setup(database);t=uuid4().hex;before=frozen(database)
    llm.classify_intent.return_value=IntentResult.model_validate_json(RAW_B)
    async def scenario():
        async with open_checkpointer(checkpoint_uri) as saver:
            response=await post(app(saver),t,B);assert response.status_code==200;state=response.json()
            assert state['intent']=='ORDER' and state['order_status']=='INSUFFICIENT_STOCK'
            assert state['quantity']==999 and state['order_id'] is None
            assert state.get('pending_order') is None and state.get('active_order_confirmation') is None
            persisted=await cp(saver,t);assert persisted['quantity']==999 and persisted['order_status']=='INSUFFICIENT_STOCK'
        llm.classify_intent.return_value=IntentResult(intent=Intent.UNKNOWN)
        async with open_checkpointer(checkpoint_uri) as saver:
            yes=(await post(app(saver),t,'oui')).json()
            assert not yes.get('created_order_id') and not yes.get('active_order_confirmation')
            assert frozen(database)==before
            evidence('B-pipeline',{'message':B,'raw':RAW_B,'classification_stages':llm.pipeline_trace,'stock':12,'product_document_id':str(conc),'response':state,'checkpoint':persisted,'yes_after_reopen':yes,'before':before,'after':frozen(database)})
        tx.products.reserve_stock_sku.assert_not_called()
    asyncio.run(scenario())

@pytest.mark.parametrize('intent',[Intent.ORDER,Intent.ORDER_STATUS])
@pytest.mark.parametrize('message',['Commande -6 Vis Synthétiques V016','Commande moins six vis','Commande 1.5 vis','Commande 1,5 vis','Commande 1.5abc vis','Commande une paire de vis'])
def test_invalid_user_count_never_accepts_model_positive_six(database,checkpoint_uri,message,intent):
    _,_,_,_,tx,llm,app=setup(database);before=frozen(database)
    llm.classify_intent.return_value=IntentResult(intent=intent,quantity=6,product_name='Vis Synthétiques V016',order_id='fake-status')
    async def scenario():
        async with open_checkpointer(checkpoint_uri) as saver:
            api=app(saver);t=uuid4().hex;d=(await post(api,t,message)).json()
            assert d['order_status']=='INVALID_QUANTITY' and not d.get('active_order_confirmation')
            d=(await post(api,t,'oui')).json();assert not d.get('created_order_id')
            assert frozen(database)==before;tx.products.reserve_stock_sku.assert_not_called()
    asyncio.run(scenario())


def test_counted_reference_collision_and_customer_ownership(database,checkpoint_uri):
    _,_,_,orders,tx,llm,app=setup(database);reference='DEMO-CONC-0001'
    llm.classify_intent.return_value=IntentResult(intent=Intent.ORDER_STATUS,order_id='2 '+reference)
    async def scenario():
        await seed(orders,reference);before=frozen(database)
        async with open_checkpointer(checkpoint_uri) as saver:
            api=app(saver);t=uuid4().hex;d=(await post(api,t,'Commande 2 '+reference)).json()
            assert d['intent']=='UNKNOWN' and d['reference_clarification'] and not d.get('active_order_confirmation')
            llm.classify_intent.return_value=IntentResult(intent=Intent.UNKNOWN)
        async with open_checkpointer(checkpoint_uri) as saver:
            d=(await post(app(saver),t,'oui confirme')).json();assert not d.get('created_order_id')
            llm.classify_intent.return_value=IntentResult(intent=Intent.ORDER_STATUS,order_id='2 '+reference)
            foreign=(await as_customer(app(saver),'other-owner',uuid4().hex,'Commande 2 '+reference)).json()
            assert foreign['order_status']=='PENDING_CONFIRMATION' and foreign['pending_order']['quantity']==2
            assert OWNER not in json.dumps(foreign) and foreign.get('queried_order_status') is None
        assert frozen(database)==before;tx.products.reserve_stock_sku.assert_not_called()
    asyncio.run(scenario())


def test_ambiguous_quantity_change_revokes_lexical_preparation_durably(database,checkpoint_uri):
    _,_,_,_,tx,llm,app=setup(database);t=uuid4().hex;before=frozen(database)
    async def scenario():
        llm.classify_intent.return_value=IntentResult.model_validate_json(RAW_A)
        async with open_checkpointer(checkpoint_uri) as saver:
            api=app(saver);d=(await post(api,t,A)).json();assert d['pending_order']['quantity']==6
            llm.classify_intent.return_value=IntentResult(intent=Intent.ORDER,quantity=2)
            d=(await post(api,t,'plutôt 1.5 ou 2')).json();assert not d.get('active_order_confirmation') and not d.get('pending_order')
        llm.classify_intent.return_value=IntentResult(intent=Intent.UNKNOWN)
        async with open_checkpointer(checkpoint_uri) as saver:
            d=(await post(app(saver),t,'oui')).json();assert not d.get('created_order_id') and not d.get('active_order_confirmation')
        assert frozen(database)==before;tx.products.reserve_stock_sku.assert_not_called()
    asyncio.run(scenario())
