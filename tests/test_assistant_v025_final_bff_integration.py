"""Exact G18-15 at the real BFF/permission/Mongo boundary, disposable stores only."""
import pytest

from evals.assistant_v025.campaign import CORPUS
from test_b2b_http_mongo_integration import http, db, orders, stock  # noqa: F401

pytestmark = pytest.mark.integration


def test_g18_15_user_cannot_reply_to_another_users_administrative_ticket(http):
    message = next(case['message'] for case in CORPUS if case['id'] == 'G18-15')
    root = '/b2b/api/contact/requests'
    # a1 is a real persisted USER. Both another owner in the same tenant and
    # a foreign tenant have genuine contact records, not guessed identifiers.
    membership = http.db.b2b_memberships.find_one({'user_id': 'a1'})
    assert membership['role'] == 'USER'
    tickets = []
    for actor in ('a3', 'b1'):
        response = http.browser(actor).post(root, json={
            'operation_id': 'g18-15-create-' + actor,
            'subject': 'Administrative question', 'category': 'access',
            'description': 'Synthetic authorized owner question.',
        })
        assert response.status_code == 200
        tickets.append(response.json()['request_id'])
    watched = ('b2b_contact_requests', 'b2b_contact_replies', 'b2b_contact_operations',
               'b2b_orders', 'b2b_order_operations', 'b2b_products', 'b2b_stock_movements')
    before = {name: list(http.db[name].find({}).sort('_id', 1)) for name in watched}
    client = http.browser('a1')
    for index, ticket in enumerate(tickets):
        path = f'{root}/{ticket}/replies'
        payload = {'operation_id': f'g18-15-denied-{index}', 'body': message}
        for _ in range(2):
            result = client.post(path, json=payload)
            assert result.status_code == 403
            assert result.json() == {'code': 'FORBIDDEN'}
        assert client.get(f'{root}/{ticket}').status_code == 404
    assert {name: list(http.db[name].find({}).sort('_id', 1)) for name in watched} == before
    # Denial is a permission boundary, not a broken route: an authorized
    # same-tenant administrator can reply explicitly to the real owner ticket.
    response = http.browser('admin-a').post(f'{root}/{tickets[0]}/replies', json={
        'operation_id': 'g18-15-authorized-admin', 'body': 'Synthetic administrator answer.',
    })
    assert response.status_code == 200
    assert http.db.b2b_contact_replies.count_documents({}) == 1
    assert http.db.b2b_orders.count_documents({}) == 0
