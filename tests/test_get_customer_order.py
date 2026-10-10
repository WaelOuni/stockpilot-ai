import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from stockpilot.application.use_cases.get_customer_order import GetCustomerOrderUseCase
from stockpilot.main import app
from customer_auth import configure_customer_auth, customer_headers
from test_create_order import make_order, make_repository, order_document


@pytest.mark.parametrize("stored_customer", ["customer-a", "customer-b", None, ""])
def test_customer_detail_owned_missing_foreign_legacy_are_scoped(monkeypatch, stored_customer):
    configure_customer_auth(monkeypatch, app)
    repository, collection = make_repository()
    order = replace(make_order(), customer_id=stored_customer)
    collection.find_one.side_effect = lambda query: (
        {**order_document(order), "customer_id": stored_customer}
        if query == {"order_id": order.order_id, "customer_id": stored_customer} else None
    )
    monkeypatch.setattr(app.state, "get_customer_order_use_case", GetCustomerOrderUseCase(repository))
    client = TestClient(app)
    response = client.get(f"/orders/{order.order_id}", headers=customer_headers())
    collection.find_one.assert_called_once_with({"order_id": order.order_id, "customer_id": "customer-a"})
    assert response.status_code == (200 if stored_customer == "customer-a" else 404)
    if response.status_code == 200:
        assert set(response.json()) == {"order_id", "product_name", "quantity", "status", "created_at"}
        assert response.json()["status"] == "CREATED"
    else:
        assert response.json() == {"detail": "Commande introuvable (NOT_FOUND)."}
    assert client.get("/orders/missing", headers=customer_headers()).status_code == 404
    collection.insert_one.assert_not_called()


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer invalid"}, {"X-Customer-Id": "customer-a"}])
def test_detail_rejects_missing_or_invalid_jwt(monkeypatch, headers):
    configure_customer_auth(monkeypatch, app)
    use_case = SimpleNamespace(execute=AsyncMock())
    monkeypatch.setattr(app.state, "get_customer_order_use_case", use_case)
    response = TestClient(app).get("/orders/order-123", headers=headers)
    assert response.status_code == 401
    use_case.execute.assert_not_awaited()


def test_detail_missing_jwt_configuration_returns_503(monkeypatch):
    configure_customer_auth(monkeypatch, app)
    monkeypatch.setattr(app.state, "settings", app.state.settings.model_copy(update={"customer_jwt_secret": None}))
    assert TestClient(app).get("/orders/order-123", headers=customer_headers()).status_code == 503


def test_use_case_delegates_only_scoped_lookup():
    repository = SimpleNamespace(find_by_order_id_and_customer_id=AsyncMock(return_value=None))
    assert asyncio.run(GetCustomerOrderUseCase(repository).execute("missing", "customer-a")) is None
    repository.find_by_order_id_and_customer_id.assert_awaited_once_with("missing", "customer-a")


@pytest.mark.parametrize("customer", [None, "", " "])
def test_use_case_rejects_unowned_identity(customer):
    repository = SimpleNamespace(find_by_order_id_and_customer_id=AsyncMock())
    with pytest.raises(ValueError):
        asyncio.run(GetCustomerOrderUseCase(repository).execute("id", customer))
    repository.find_by_order_id_and_customer_id.assert_not_awaited()


def test_detail_technical_errors_are_not_masked(monkeypatch):
    configure_customer_auth(monkeypatch, app)
    monkeypatch.setattr(app.state, "get_customer_order_use_case", SimpleNamespace(
        execute=AsyncMock(side_effect=RuntimeError("private database error")),
    ))
    response = TestClient(app).get("/orders/order-123", headers=customer_headers())
    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error"}
