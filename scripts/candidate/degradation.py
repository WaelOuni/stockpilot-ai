"""Isolated child-process fault checks; never stop the shared provider or candidate databases."""
from scripts.candidate.runtime import api_python


def main():
    result=api_python('''
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from types import SimpleNamespace
from unittest.mock import AsyncMock
from stockpilot import main
from stockpilot.application.use_cases.catalogue_advice import CatalogueAdviceUseCase
from stockpilot.application.workflows.stockpilot_workflow import build_graph
import jwt,time
class DownCatalogue:
    async def find_advice_candidates(self, criteria):
        raise ConnectionError('isolated simulated catalogue failure')
forbidden=SimpleNamespace(execute=AsyncMock(side_effect=AssertionError('Mutation forbidden')))
main.app.state.stockpilot_workflow=build_graph(main.classify_intent_use_case,forbidden,forbidden,forbidden,forbidden,
    get_order_status=forbidden,catalogue_advice=CatalogueAdviceUseCase(DownCatalogue(),main.ollama),checkpointer=InMemorySaver())
secret=main.settings.customer_jwt_secret.get_secret_value()
signed=jwt.encode({'customer_id':'candidate-fault','exp':int(time.time())+120},secret,algorithm='HS256')
headers={'Authorization':'Bearer '+signed,'Origin':'https://localhost:5193'}
client=TestClient(main.app,base_url='https://localhost:5193')
response=client.post('/assistant',headers=headers,json={'message':'Je cherche une perceuse disponible à moins de 100 euros.','thread_id':'catalogue-fault'})
assert response.status_code==200
assert response.json()['advice_status']=='CATALOGUE_UNAVAILABLE'
assert response.json()['recommendations']==[]
forbidden.execute.assert_not_awaited()
main.app.state.settings=main.settings.model_copy(update={'deployment_environment':'production_saas'})
assert client.get('/health').status_code==200
closed=client.post('/assistant',headers=headers,json={'message':'Je cherche une perceuse','thread_id':'saas-fault'})
assert closed.status_code==503
assert 'tenant isolation' in closed.json()['detail']
print(json.dumps({'catalogue_unavailable':'PASS','saas_fail_closed':'PASS',
'middleware_order':[middleware.cls.__name__ for middleware in main.app.user_middleware]}))
''')
    print(result)


if __name__ == '__main__':
    main()
