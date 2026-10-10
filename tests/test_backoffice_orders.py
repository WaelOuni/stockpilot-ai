import asyncio
from copy import deepcopy
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from customer_auth import CUSTOMER_JWT_SECRET, customer_headers
from test_list_customer_orders import FakeCursor, document
from stockpilot.application.use_cases.backoffice_orders import (
    GetBackofficeOrderUseCase, ListBackofficeOrdersUseCase,
)
from stockpilot.application.use_cases.get_customer_order import GetCustomerOrderUseCase
from stockpilot.application.use_cases.list_customer_orders import ListCustomerOrdersUseCase
from stockpilot.infrastructure.config.settings import Settings
from stockpilot.infrastructure.persistence.mongodb.order_repository import MongoOrderRepository
from stockpilot.main import app, order_repository
from stockpilot.observability import JsonFormatter


TOKEN = "test-only-backoffice-orders-token"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
PATHS = ["/backoffice/orders", "/backoffice/orders/tie-z"]


@pytest.fixture
def api(monkeypatch):
    rows = [
        document("tie-a"), document("tie-z", "customer-b"),
        document("old", created_at="2026-10-09T00:00:00+00:00"),
        document("legacy", None, "2026-10-11T00:00:00+00:00"),
    ]
    rows[1].update({
        "_id": "private-mongo-id", "reserved_product_id": "private-product-id",
        "events": [{"status": "CREATED", "source": "SYSTEM",
                    "occurred_at": rows[1]["created_at"]}],
    })
    collection = MagicMock()
    collection.find.side_effect = lambda query: FakeCursor(
        [deepcopy(row) for row in rows if all(row.get(key) == value for key, value in query.items())],
        [("find", query)],
    )
    collection.find_one.side_effect = lambda query: next(
        (deepcopy(row) for row in rows if all(row.get(key) == value for key, value in query.items())),
        None,
    )
    repository = MongoOrderRepository({"orders": collection})
    monkeypatch.setattr(app.state, "settings", Settings(
        backoffice_api_token=TOKEN, customer_jwt_secret=CUSTOMER_JWT_SECRET, _env_file=None,
    ))
    monkeypatch.setattr(app.state, "list_backoffice_orders_use_case", ListBackofficeOrdersUseCase(repository))
    monkeypatch.setattr(app.state, "get_backoffice_order_use_case", GetBackofficeOrderUseCase(repository))
    monkeypatch.setattr(app.state, "list_customer_orders_use_case", ListCustomerOrdersUseCase(repository))
    monkeypatch.setattr(app.state, "get_customer_order_use_case", GetCustomerOrderUseCase(repository))
    before = deepcopy(rows)
    yield TestClient(app), collection, repository
    assert rows == before
    for method in ("insert_one", "insert_many", "update_one", "update_many",
                   "find_one_and_update", "delete_one", "delete_many", "create_index"):
        getattr(collection, method).assert_not_called()


def test_authenticated_list_all_owners_and_legacy(api):
    client, collection, _ = api
    response = client.get(PATHS[0], headers=HEADERS)
    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"items", "limit", "offset", "count"}
    assert (payload["limit"], payload["offset"], payload["count"]) == (20, 0, 4)
    assert [item["order_id"] for item in payload["items"]] == ["legacy", "tie-z", "tie-a", "old"]
    assert [item["customer_id"] for item in payload["items"]] == [
        None, "customer-b", "customer-a", "customer-a",
    ]
    assert payload["items"][0]["events"] == []
    collection.find.assert_called_once_with({})
    assert TOKEN not in response.text


@pytest.mark.parametrize("limit,offset,expected", [
    (1, 0, ["legacy"]), (2, 1, ["tie-z", "tie-a"]),
    (100, 3, ["old"]), (20, 4, []), (20, 10**9, []),
])
def test_api_pagination_and_stable_sort(api, limit, offset, expected):
    client, _, _ = api
    response = client.get(PATHS[0], params={"limit": limit, "offset": offset}, headers=HEADERS)
    assert response.status_code == 200
    page = response.json()
    assert [item["order_id"] for item in page["items"]] == expected
    assert (page["limit"], page["offset"], page["count"]) == (limit, offset, len(expected))


def test_detail_events_and_no_internal_identifiers(api):
    client, collection, _ = api
    response = client.get(PATHS[1], headers=HEADERS)
    assert response.status_code == 200
    assert response.json() == {
        "order_id": "tie-z", "customer_id": "customer-b",
        "product_name": "Bosch X100", "quantity": 2, "status": "CREATED",
        "created_at": "2026-10-10T00:00:00Z",
        "events": [{"status": "CREATED", "source": "SYSTEM",
                    "occurred_at": "2026-10-10T00:00:00Z"}],
    }
    collection.find_one.assert_called_once_with({"order_id": "tie-z"})
    listing = client.get(PATHS[0], headers=HEADERS).json()
    for item in listing["items"]:
        assert set(item) == {
            "order_id", "customer_id", "product_name", "quantity", "status", "created_at", "events",
        }


def test_legacy_detail_and_missing(api):
    client, _, _ = api
    legacy = client.get("/backoffice/orders/legacy", headers=HEADERS)
    assert legacy.status_code == 200
    assert legacy.json()["customer_id"] is None
    assert legacy.json()["events"] == []
    missing = client.get("/backoffice/orders/absent", headers=HEADERS)
    assert missing.status_code == 404
    assert missing.json() == {"detail": "Commande introuvable (NOT_FOUND)."}


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("headers", [
    {}, {"Authorization": "Bearer wrong"}, {"Authorization": f"Basic {TOKEN}"},
    customer_headers(),
])
def test_authentication_precedes_reads(api, path, headers):
    client, collection, _ = api
    response = client.get(path, headers=headers)
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid back-office credentials."}
    assert response.headers["WWW-Authenticate"] == "Bearer"
    collection.find.assert_not_called()
    collection.find_one.assert_not_called()


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("secret", [None, "", " "])
def test_missing_configuration_is_503(api, monkeypatch, path, secret):
    client, collection, _ = api
    monkeypatch.setattr(app.state, "settings", Settings(backoffice_api_token=secret, _env_file=None))
    response = client.get(path, headers=HEADERS)
    assert response.status_code == 503
    assert response.json() == {"detail": "Back-office authentication is not configured."}
    collection.find.assert_not_called()
    collection.find_one.assert_not_called()


@pytest.mark.parametrize("query", ["limit=0", "limit=101", "limit=x", "offset=-1", "offset=1.5"])
def test_invalid_http_pagination_before_read(api, query):
    client, collection, _ = api
    assert client.get(PATHS[0] + "?" + query, headers=HEADERS).status_code == 422
    collection.find.assert_not_called()


def test_application_results_and_repository_query(api):
    _, collection, repository = api
    orders = asyncio.run(ListBackofficeOrdersUseCase(repository).execute(2, 1))
    assert [order.order_id for order in orders] == ["tie-z", "tie-a"]
    collection.find.assert_called_once_with({})
    found = asyncio.run(GetBackofficeOrderUseCase(repository).execute("legacy"))
    assert found.customer_id is None and found.events == ()
    assert asyncio.run(GetBackofficeOrderUseCase(repository).execute("absent")) is None


@pytest.mark.parametrize("limit,offset", [(0, 0), (101, 0), (True, 0), (20, -1), (20, False)])
def test_application_invalid_pagination_never_reads(limit, offset):
    repository = MagicMock()
    repository.find_all = AsyncMock()
    with pytest.raises(ValueError):
        asyncio.run(ListBackofficeOrdersUseCase(repository).execute(limit, offset))
    repository.find_all.assert_not_awaited()


def test_customer_endpoints_still_enforce_owner_and_hide_owner_field(api):
    client, _, _ = api
    assert client.get("/orders", headers=HEADERS).status_code == 401
    assert client.get("/orders/tie-z", headers=HEADERS).status_code == 401
    response = client.get("/orders", headers=customer_headers())
    assert [item["order_id"] for item in response.json()["items"]] == ["tie-a", "old"]
    assert all("customer_id" not in item for item in response.json()["items"])
    assert client.get("/orders/tie-z", headers=customer_headers()).status_code == 404
    assert client.get("/orders/legacy", headers=customer_headers()).status_code == 404


@pytest.mark.parametrize("path,method", [(PATHS[0], "find"), (PATHS[1], "find_one")])
def test_failures_sanitized_and_secret_not_logged(api, path, method, caplog):
    client, collection, _ = api
    getattr(collection, method).side_effect = RuntimeError(f"private failure {TOKEN}")
    with caplog.at_level(logging.INFO, logger="stockpilot"):
        response = client.get(path, headers=HEADERS)
    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error"}
    assert TOKEN not in response.text
    records = [record for record in caplog.records if record.name.startswith("stockpilot")]
    assert records
    assert all(TOKEN not in JsonFormatter().format(record) for record in records)


def test_composition_and_openapi():
    assert app.state.list_backoffice_orders_use_case.order_repository is order_repository
    assert app.state.get_backoffice_order_use_case.order_repository is order_repository
    schema = app.openapi()
    for path in ("/backoffice/orders", "/backoffice/orders/{order_id}"):
        assert schema["paths"][path]["get"]["security"] == [
            {"BackofficeBearer": []}, {"AdminSessionCookie": []},
        ]
    assert schema["paths"]["/orders"]["get"]["security"] == [{"CustomerBearer": []}]
