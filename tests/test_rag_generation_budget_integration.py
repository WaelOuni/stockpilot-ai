"""Real composition, dedicated Mongo/PG and failure/history/tenant semantics."""
import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from rag_budget_support import FIXTURE, settings, wire
from test_tenant_assistant_integration import assistant as assistant  # noqa: F401
from test_tenant_knowledge_integration import command, ingest, environment as environment  # noqa: F401
from test_tenant_orders_mongo_integration import REQUESTS, context, db as db, orders as orders  # noqa: F401
from test_tenant_stock_operations_mongo_integration import stock as stock  # noqa: F401
from stockpilot.application.ports.tenant_knowledge import TenantDataUnavailable, TenantDataNotFound
from stockpilot.infrastructure.identity.bff_assistant import compose_assistant

pytestmark = pytest.mark.integration
QUESTION = 'Résume les documents de mon organisation.'


@pytest.mark.parametrize('truncated', [True, False])
def test_real_composition_budget_failure_or_cited_history_stays_tenant_scoped(assistant, db, monkeypatch, truncated):
    original, _, e = assistant
    service = compose_assistant(settings(e.uri,e.schema), SimpleNamespace(authorization=e.orders[0].authorization, catalogue=original.catalogue.catalogue, orders=e.orders[0]))
    def response(payload):
        if truncated: return FIXTURE['truncated']
        docs=json.loads(payload['prompt'].split('<documents>\n')[1].split('\n</documents>')[0])
        return {'done_reason':'stop','response':json.dumps({'answerable':True,'excerpts':[{'source_id':p['source'],'excerpt':p['content']} for p in docs]})}
    requests, _ = wire(monkeypatch,response)
    ingest(e,command(content='Le musée synthétique ouvre à neuf heures.'))
    ingest(e,command(content='FOREIGN_SYNTHETIC_ONLY', operation='foreign-operation'),actor='admin-b')
    watched=('b2b_products','b2b_orders','b2b_order_operations','b2b_stock_movements')
    before={n:list(db[n].find({}).sort('_id',1)) for n in watched}
    actor,ctx=REQUESTS['a1'],context(e.orders)
    conv=service.store.open(actor,ctx,uuid4().hex).conversation_id
    turn_id=uuid4().hex
    if truncated:
        with pytest.raises(TenantDataUnavailable):
            asyncio.run(service.send(actor,ctx,conv,turn_id,QUESTION,read_only=True))
        turn=service.read(actor,ctx,conv)['items'][0]
        assert turn['execution']['state']=='failed' and turn['execution']['code']=='response_decode_error'
        stored=service.store.turn(actor,ctx,conv,turn_id)
        assert stored['reply']['_read_attempt']['phase']=='generation'
        assert turn['reply']['intent']=='ASSISTANT_UNAVAILABLE' and turn['operation'] is None
        assert not turn['reply'].get('cited_sources') and not turn['reply'].get('can_confirm')
        assert '_read_attempt' not in turn['reply']
    else:
        result=asyncio.run(service.send(actor,ctx,conv,turn_id,QUESTION,read_only=True))
        assert result['status']=='completed' and '[S1]' in result['reply']['text']
        assert 'FOREIGN_SYNTHETIC_ONLY' not in result['reply']['text']
        saved=service.store.turn(actor,ctx,conv,turn_id)
        assert len(saved['reply']['cited_sources'])==1 and saved['reply']['cited_sources'][0]['organization_id']=='org-a'
        assert service.read(actor,ctx,conv)['items'][0]['reply']['text']==result['reply']['text']
    generated=[p for path,p in requests if path=='/api/generate']
    assert len(generated)==1 and generated[0]['options']=={'num_predict':1024,'temperature':0}
    assert 'FOREIGN_SYNTHETIC_ONLY' not in generated[0]['prompt']
    with pytest.raises(TenantDataNotFound):service.read(REQUESTS['b1'],context(e.orders,'b1'),conv)
    assert {n:list(db[n].find({}).sort('_id',1)) for n in watched}==before
