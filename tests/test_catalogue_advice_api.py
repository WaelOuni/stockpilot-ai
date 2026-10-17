from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock

from customer_auth import configure_customer_auth, customer_headers
from evals.fixtures.catalogue_advice import advice_graph
from stockpilot.presentation.api.routes.assistant import router


def client():
    app = FastAPI()
    configure_customer_auth(None, app)
    graph, repo, _, forbidden = advice_graph()
    app.state.stockpilot_workflow = graph
    app.include_router(router)
    return TestClient(app), repo, forbidden


def ask(http, message, *, customer='a', thread='same'):
    response = http.post('/assistant', headers=customer_headers(customer), json={'message': message, 'thread_id': thread})
    assert response.status_code == 200
    return response.json()


def test_real_assistant_graph_contract_general_target_comparison_and_clarification():
    http, _, forbidden = client()
    first = ask(http, 'Je cherche une perceuse disponible à moins de 100 euros.')
    assert [p['sku'] for p in first['recommendations']] == ['D-A', 'D-B', 'D-C']
    followup = ask(http, 'Et si je monte le budget à 150 euros ?')
    assert followup['budget'] == 150
    targeted = ask(http, 'Conseille-moi sur la Perceuse Concurrence Synthétique V017.')
    assert [p['sku'] for p in targeted['recommendations']] == ['D-C']
    compare = ask(http, 'Compare cette perceuse avec deux autres.')
    assert [p['sku'] for p in compare['recommendations']] == ['D-C', 'D-O', 'D-A']
    assert [p['role'] for p in compare['recommendations']] == ['reference', 'alternative', 'alternative']
    clarify = ask(http, 'Compare cette perceuse avec deux autres.')
    assert clarify['advice_status'] == 'CLARIFICATION_REQUIRED' and clarify['recommendations'] == []
    absent = ask(http, 'Conseille-moi sur la Perceuse Inconnue V017.')
    assert absent['advice_status'] == 'NO_RESULTS' and absent['recommendations'] == []
    forbidden.execute.assert_not_awaited()


def test_assistant_customer_and_thread_namespace_and_controlled_catalogue_error():
    http, repo, _ = client()
    ask(http, 'Conseille-moi sur le SKU D-C')
    for customer, thread in [('a', 'different'), ('b', 'same')]:
        result = ask(http, 'Compare cette perceuse avec deux autres.', customer=customer, thread=thread)
        assert result['advice_status'] == 'CLARIFICATION_REQUIRED' and result['recommendations'] == []
    repo.find_advice_candidates = AsyncMock(side_effect=RuntimeError('private connection'))
    result = ask(http, 'Je cherche une perceuse.')
    assert result['advice_status'] == 'CATALOGUE_UNAVAILABLE'
    assert result['recommendations'] == [] and 'private connection' not in str(result)
