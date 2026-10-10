import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient

from customer_auth import (
    CUSTOMER_JWT_SECRET,
    configure_customer_auth,
    customer_headers,
    customer_token,
)
from stockpilot.application.use_cases.list_customer_orders import ListCustomerOrdersUseCase
from stockpilot.domain.entities.order import Order
from stockpilot.domain.enums.order_status import OrderStatus
from stockpilot.infrastructure.config.settings import Settings
from stockpilot.infrastructure.persistence.mongodb.order_repository import MongoOrderRepository
from stockpilot.main import app, order_repository


def make_order(order_id="order-a", customer_id="customer-a"):
    return Order(
        order_id=order_id, operation_id=f"operation-{order_id}",
        product_name="Bosch X100", quantity=2, status=OrderStatus.CREATED,
        created_at=datetime(2026, 10, 10, tzinfo=timezone.utc),
        customer_id=customer_id,
    )


def document(order_id, customer_id="customer-a", created_at="2026-10-10T00:00:00+00:00"):
    result = {
        "order_id": order_id, "operation_id": f"operation-{order_id}",
        "product_name": "Bosch X100", "quantity": 2, "status": "CREATED",
        "created_at": created_at,
    }
    if customer_id is not None:
        result["customer_id"] = customer_id
    return result


class FakeCursor:
    def __init__(self, documents, calls):
        self.documents = documents
        self.calls = calls
        self.offset = None
        self.page_size = None

    def sort(self, fields):
        assert fields == [("created_at", -1), ("order_id", -1)]
        self.calls.append(("sort", fields))
        self.documents = sorted(
            self.documents, key=lambda row: (row["created_at"], row["order_id"]),
            reverse=True,
        )
        return self

    def skip(self, offset):
        self.calls.append(("skip", offset))
        self.offset = offset
        return self

    def limit(self, limit):
        self.calls.append(("limit", limit))
        self.page_size = limit
        return self

    def __iter__(self):
        assert [call[0] for call in self.calls] == ["find", "sort", "skip", "limit"]
        assert self.offset is not None and self.page_size is not None
        self.calls.append(("iterate",))
        return iter(self.documents[self.offset:self.offset + self.page_size])


class FakeCollection:
    def __init__(self, documents):
        self.documents = documents
        self.calls = []

    def find(self, query):
        assert query == {"customer_id": "customer-a"}
        self.calls.append(("find", query))
        matching = [
            row for row in self.documents
            if row.get("customer_id") == query["customer_id"]
        ]
        return FakeCursor(matching, self.calls)


def repository_for(documents):
    collection = FakeCollection(documents)
    return MongoOrderRepository({"orders": collection}), collection


def install_api_use_case(monkeypatch, orders=None, error=None):
    configure_customer_auth(monkeypatch, app)
    use_case = SimpleNamespace(
        execute=AsyncMock(return_value=[] if orders is None else orders, side_effect=error),
    )
    monkeypatch.setattr(app.state, "list_customer_orders_use_case", use_case)
    return use_case


def test_use_case_delegates_defaults_and_returns_repository_list_unchanged():
    orders = [make_order(), make_order("other", "customer-b"), make_order("legacy", None)]
    repository = SimpleNamespace(find_by_customer_id=AsyncMock(return_value=orders))
    result = asyncio.run(ListCustomerOrdersUseCase(repository).execute("customer-a"))
    assert result is orders
    repository.find_by_customer_id.assert_awaited_once_with("customer-a", limit=20, offset=0)


@pytest.mark.parametrize(("limit", "offset"), [(1, 0), (100, 0), (3, 7), (20, 10**9)])
def test_use_case_delegates_valid_pagination(limit, offset):
    repository = SimpleNamespace(find_by_customer_id=AsyncMock(return_value=[]))
    assert asyncio.run(
        ListCustomerOrdersUseCase(repository).execute("customer-a", limit, offset),
    ) == []
    repository.find_by_customer_id.assert_awaited_once_with(
        "customer-a", limit=limit, offset=offset,
    )


@pytest.mark.parametrize("customer_id", [None, "", " ", "\t\n", 42, True, [], {}])
def test_use_case_rejects_invalid_identity_without_selecting_legacy_orders(customer_id):
    repository = SimpleNamespace(find_by_customer_id=AsyncMock())
    with pytest.raises(ValueError):
        asyncio.run(ListCustomerOrdersUseCase(repository).execute(customer_id))
    repository.find_by_customer_id.assert_not_awaited()


@pytest.mark.parametrize("limit", [0, -1, 101, "20", 1.0, True, False, None])
def test_use_case_rejects_invalid_limit_without_database_access(limit):
    repository = SimpleNamespace(find_by_customer_id=AsyncMock())
    with pytest.raises(ValueError):
        asyncio.run(ListCustomerOrdersUseCase(repository).execute("customer-a", limit=limit))
    repository.find_by_customer_id.assert_not_awaited()


@pytest.mark.parametrize("offset", [-1, "0", 0.0, True, False, None])
def test_use_case_rejects_invalid_offset_without_database_access(offset):
    repository = SimpleNamespace(find_by_customer_id=AsyncMock())
    with pytest.raises(ValueError):
        asyncio.run(ListCustomerOrdersUseCase(repository).execute("customer-a", offset=offset))
    repository.find_by_customer_id.assert_not_awaited()


def test_use_case_propagates_repository_error():
    error = RuntimeError("database unavailable")
    repository = SimpleNamespace(find_by_customer_id=AsyncMock(side_effect=error))
    with pytest.raises(RuntimeError) as caught:
        asyncio.run(ListCustomerOrdersUseCase(repository).execute("customer-a", 4, 2))
    assert caught.value is error
    repository.find_by_customer_id.assert_awaited_once_with("customer-a", limit=4, offset=2)


@pytest.mark.parametrize(("limit", "offset", "expected"), [
    (20, 0, ["new", "tie-z", "tie-a", "old"]),
    (2, 1, ["tie-z", "tie-a"]),
    (1, 2, ["tie-a"]),
    (100, 0, ["new", "tie-z", "tie-a", "old"]),
    (20, 4, []),
    (20, 10**9, []),
])
def test_repository_filters_sorts_and_paginates_in_database(monkeypatch, limit, offset, expected):
    rows = [
        document("tie-a"), document("foreign", "customer-b"),
        document("old", created_at="2026-10-09T00:00:00+00:00"),
        document("legacy", None), document("tie-z"),
        document("new", created_at="2026-10-11T00:00:00+00:00"),
        {**document("null-owner"), "customer_id": None},
    ]
    repository, collection = repository_for(rows)
    mapper = Mock(wraps=repository._to_order)
    monkeypatch.setattr(repository, "_to_order", mapper)
    orders = asyncio.run(repository.find_by_customer_id("customer-a", limit, offset))
    assert [order.order_id for order in orders] == expected
    assert all(order.customer_id == "customer-a" for order in orders)
    assert collection.calls == [
        ("find", {"customer_id": "customer-a"}),
        ("sort", [("created_at", -1), ("order_id", -1)]),
        ("skip", offset), ("limit", limit), ("iterate",),
    ]
    assert mapper.call_count == len(expected)
    assert [call.args[0]["order_id"] for call in mapper.call_args_list] == expected
    if orders:
        assert orders[0].created_at.tzinfo == timezone.utc
        assert orders[0].operation_id == f"operation-{expected[0]}"
        assert orders[0].status == OrderStatus.CREATED


def test_repository_empty_collection():
    repository, collection = repository_for([])
    assert asyncio.run(repository.find_by_customer_id("customer-a", 20, 0)) == []
    assert collection.calls[-1] == ("iterate",)


def test_repository_maps_only_page_documents_not_invalid_unselected_rows():
    rows = [
        {"order_id": "unselected", "customer_id": "customer-a", "created_at": "2020"},
        document("selected"),
        {"customer_id": "customer-b"},
        {},
    ]
    repository, _ = repository_for(rows)
    orders = asyncio.run(repository.find_by_customer_id("customer-a", 1, 0))
    assert [order.order_id for order in orders] == ["selected"]


def test_repository_does_not_apply_python_customer_filter():
    row = document("database-result", "customer-b")
    cursor = FakeCursor([row], [("find", {"customer_id": "customer-a"})])
    collection = SimpleNamespace(find=Mock(return_value=cursor))
    repository = MongoOrderRepository({"orders": collection})
    orders = asyncio.run(repository.find_by_customer_id("customer-a", 20, 0))
    assert [order.order_id for order in orders] == ["database-result"]
    collection.find.assert_called_once_with({"customer_id": "customer-a"})


def test_api_defaults_response_metadata_and_no_private_fields(monkeypatch):
    use_case = install_api_use_case(monkeypatch, [make_order()])
    response = TestClient(app).get("/orders", headers=customer_headers())
    assert response.status_code == 200
    assert response.json() == {
        "items": [{
            "order_id": "order-a", "product_name": "Bosch X100", "quantity": 2,
            "status": "CREATED", "created_at": "2026-10-10T00:00:00Z",
            "events": [],
        }],
        "limit": 20, "offset": 0, "count": 1,
    }
    assert "customer-a" not in response.text
    assert "operation-order-a" not in response.text
    use_case.execute.assert_awaited_once_with("customer-a", limit=20, offset=0)


@pytest.mark.parametrize(("limit", "offset"), [(1, 0), (100, 0), (7, 3), (20, 10**9)])
def test_api_accepts_pagination_boundaries_and_empty_page(monkeypatch, limit, offset):
    use_case = install_api_use_case(monkeypatch)
    response = TestClient(app).get(
        "/orders", headers=customer_headers(), params={"limit": limit, "offset": offset},
    )
    assert response.status_code == 200
    assert response.json() == {"items": [], "limit": limit, "offset": offset, "count": 0}
    use_case.execute.assert_awaited_once_with("customer-a", limit=limit, offset=offset)


@pytest.mark.parametrize("customer_id", ["customer-b", "", "legacy"])
def test_api_uses_jwt_not_header_or_query_identity(monkeypatch, customer_id):
    use_case = install_api_use_case(monkeypatch)
    response = TestClient(app).get(
        "/orders", headers=customer_headers(**{"X-Customer-Id": customer_id}),
        params={"customer_id": customer_id, "limit": 2, "offset": 1},
    )
    assert response.status_code == 200
    use_case.execute.assert_awaited_once_with("customer-a", limit=2, offset=1)


def test_api_actual_use_case_repository_isolates_owner_before_pagination(monkeypatch):
    install_api_use_case(monkeypatch)
    repository, collection = repository_for([
        document("foreign-new", "customer-b", "2026-10-12T00:00:00+00:00"),
        document("legacy-new", None, "2026-10-12T00:00:00+00:00"),
        document("a-old", created_at="2026-10-09T00:00:00+00:00"),
        document("a-new"), document("a-tie-z"),
    ])
    monkeypatch.setattr(
        app.state, "list_customer_orders_use_case", ListCustomerOrdersUseCase(repository),
    )
    response = TestClient(app).get(
        "/orders?limit=1&offset=1&customer_id=customer-b",
        headers=customer_headers(**{"X-Customer-Id": "customer-b"}),
    )
    assert response.status_code == 200
    payload = response.json()
    assert [item["order_id"] for item in payload["items"]] == ["a-new"]
    assert payload["count"] == 1
    assert payload["limit"] == 1
    assert payload["offset"] == 1
    assert set(payload["items"][0]) == {
        "order_id", "product_name", "quantity", "status", "created_at", "events",
    }
    assert payload["items"][0]["events"] == []
    assert collection.calls[0] == ("find", {"customer_id": "customer-a"})


@pytest.mark.parametrize("headers", [
    {}, {"X-Customer-Id": "customer-a"}, {"Authorization": "Basic token"},
    {"Authorization": "Bearer"}, {"Authorization": "Bearer malformed"},
    {"Authorization": "Bearer malformed extra"},
])
def test_api_missing_or_malformed_credentials_never_invoke_use_case(monkeypatch, headers):
    use_case = install_api_use_case(monkeypatch)
    response = TestClient(app).get("/orders?customer_id=customer-a", headers=headers)
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid customer credentials."}
    assert response.headers["WWW-Authenticate"] == "Bearer"
    use_case.execute.assert_not_awaited()


@pytest.mark.parametrize("token", [
    customer_token(secret="wrong-signing-secret-at-least-32-bytes"),
    customer_token(exp=1),
    customer_token(customer_id=" "),
    customer_token(customer_id=None),
])
def test_api_invalid_jwt_never_invoke_use_case(monkeypatch, token):
    use_case = install_api_use_case(monkeypatch)
    response = TestClient(app).get("/orders", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid customer credentials."}
    assert response.headers["WWW-Authenticate"] == "Bearer"
    use_case.execute.assert_not_awaited()


@pytest.mark.parametrize("secret", [None, "", " ", "short"])
def test_api_missing_auth_configuration_fails_closed(monkeypatch, secret):
    use_case = install_api_use_case(monkeypatch)
    monkeypatch.setattr(
        app.state, "settings", Settings(customer_jwt_secret=secret, _env_file=None),
    )
    response = TestClient(app).get("/orders", headers=customer_headers())
    assert response.status_code == 503
    assert response.json() == {"detail": "Customer authentication is not configured."}
    use_case.execute.assert_not_awaited()


@pytest.mark.parametrize("params", [
    {"limit": "0"}, {"limit": "-1"}, {"limit": "101"}, {"limit": "letters"},
    {"limit": "1.5"}, {"offset": "-1"}, {"offset": "letters"}, {"offset": "1.5"},
])
def test_api_invalid_pagination_is_422_before_use_case(monkeypatch, params):
    use_case = install_api_use_case(monkeypatch)
    response = TestClient(app).get("/orders", headers=customer_headers(), params=params)
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"][:1] == ["query"]
    use_case.execute.assert_not_awaited()


def test_api_repository_error_is_sanitized_and_logged(monkeypatch, caplog):
    use_case = install_api_use_case(monkeypatch, error=RuntimeError("private database detail"))
    response = TestClient(app).get("/orders", headers=customer_headers())
    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error"}
    assert response.headers["X-Request-ID"]
    assert "private database detail" not in response.text
    assert any(record.getMessage() == "http_request_failed" for record in caplog.records)
    use_case.execute.assert_awaited_once_with("customer-a", limit=20, offset=0)


@pytest.mark.parametrize(("method", "path", "payload", "state_name"), [
    ("PATCH", "/orders/order-a/status", {"status": "PROCESSING"}, "update_order_status_use_case"),
    ("POST", "/orders/order-a/cancel", None, "cancel_order_use_case"),
])
def test_customer_jwt_cannot_authorize_backoffice_mutations(
    monkeypatch, method, path, payload, state_name,
):
    install_api_use_case(monkeypatch)
    monkeypatch.setattr(app.state, "settings", Settings(
        customer_jwt_secret=CUSTOMER_JWT_SECRET,
        backoffice_api_token="separate-backoffice-token", _env_file=None,
    ))
    mutation = SimpleNamespace(execute=AsyncMock())
    monkeypatch.setattr(app.state, state_name, mutation)
    response = TestClient(app).request(method, path, headers=customer_headers(), json=payload)
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid back-office credentials."}
    mutation.execute.assert_not_awaited()


def test_backoffice_token_cannot_authorize_customer_history(monkeypatch):
    use_case = install_api_use_case(monkeypatch)
    monkeypatch.setattr(app.state, "settings", Settings(
        customer_jwt_secret=CUSTOMER_JWT_SECRET,
        backoffice_api_token="separate-backoffice-token", _env_file=None,
    ))
    response = TestClient(app).get(
        "/orders", headers={"Authorization": "Bearer separate-backoffice-token"},
    )
    assert response.status_code == 401
    use_case.execute.assert_not_awaited()


def test_openapi_history_uses_customer_bearer_and_documents_pagination():
    schema = app.openapi()
    operation = schema["paths"]["/orders"]["get"]
    assert operation["security"] == [{"CustomerBearer": []}]
    assert operation["security"] == schema["paths"]["/assistant"]["post"]["security"]
    assert schema["paths"]["/orders/{order_id}/status"]["patch"]["security"] == [
        {"BackofficeBearer": []},
    ]
    assert schema["paths"]["/orders/{order_id}/cancel"]["post"]["security"] == [
        {"BackofficeBearer": []},
    ]
    parameters = {parameter["name"]: parameter for parameter in operation["parameters"]}
    assert set(parameters) == {"limit", "offset"}
    assert parameters["limit"]["schema"]["default"] == 20
    assert parameters["limit"]["schema"]["minimum"] == 1
    assert parameters["limit"]["schema"]["maximum"] == 100
    assert parameters["offset"]["schema"]["default"] == 0
    assert parameters["offset"]["schema"]["minimum"] == 0
    response_ref = operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    response = schema["components"]["schemas"][response_ref.rsplit("/", 1)[-1]]
    assert set(response["properties"]) == {"items", "limit", "offset", "count"}


def test_composition_root_wires_existing_order_repository():
    assert isinstance(app.state.list_customer_orders_use_case, ListCustomerOrdersUseCase)
    assert app.state.list_customer_orders_use_case.order_repository is order_repository
