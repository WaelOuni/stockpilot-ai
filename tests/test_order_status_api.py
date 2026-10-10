from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from stockpilot.application.use_cases.update_order_status import (
    OrderStatusConflictError,
    UpdateOrderStatusUseCase,
)
from stockpilot.domain.entities.order import Order
from stockpilot.domain.enums.order_status import OrderStatus
from stockpilot.domain.services.order_lifecycle import InvalidOrderStatusTransitionError
from stockpilot.infrastructure.config.settings import Settings
from stockpilot.main import app, order_repository


def authorized_client(monkeypatch):
    monkeypatch.setattr(app.state, "settings", Settings(backoffice_api_token="test-only-token"))
    return TestClient(app, headers={"Authorization": "Bearer test-only-token"})


def make_order(status):
    return Order(
        order_id="order-123", operation_id="op-123", product_name="Bosch X100",
        quantity=2, status=status, created_at=datetime(2026, 10, 10, tzinfo=timezone.utc),
    )


def install_use_case(monkeypatch, current, updated=None):
    repo = SimpleNamespace(
        find_by_order_id=AsyncMock(return_value=make_order(current)),
        update_status=AsyncMock(return_value=make_order(updated or current)),
    )
    use_case = UpdateOrderStatusUseCase(repo)
    monkeypatch.setattr(app.state, "update_order_status_use_case", use_case)
    return repo


@pytest.mark.parametrize(("current", "target"), [
    (OrderStatus.CREATED, OrderStatus.PROCESSING),
    (OrderStatus.PROCESSING, OrderStatus.SHIPPED),
    (OrderStatus.SHIPPED, OrderStatus.DELIVERED),
])
def test_allowed_progression_uses_business_use_case(monkeypatch, current, target):
    repo = install_use_case(monkeypatch, current, target)
    response = authorized_client(monkeypatch).patch("/orders/order-123/status", json={"status": target.value})
    assert response.status_code == 200
    assert response.json() == {
        "order_id": "order-123", "product_name": "Bosch X100",
        "quantity": 2, "status": target.value, "created_at": "2026-10-10T00:00:00Z",
        "events": [],
    }
    repo.find_by_order_id.assert_awaited_once_with("order-123")
    repo.update_status.assert_awaited_once_with(
        "order-123", expected_status=current, status=target,
    )


@pytest.mark.parametrize("status", list(OrderStatus))
def test_identical_status_success_without_write(monkeypatch, status):
    repo = install_use_case(monkeypatch, status)
    response = authorized_client(monkeypatch).patch("/orders/order-123/status", json={"status": status.value})
    assert response.status_code == 200
    assert response.json()["status"] == status.value
    repo.update_status.assert_not_awaited()


def test_invalid_business_transition_is_conflict(monkeypatch):
    repo = install_use_case(monkeypatch, OrderStatus.SHIPPED)
    response = authorized_client(monkeypatch).patch("/orders/order-123/status", json={"status": "CREATED"})
    assert response.status_code == 409
    assert "SHIPPED -> CREATED" in response.json()["detail"]
    repo.update_status.assert_not_awaited()


def test_unknown_order_is_not_found(monkeypatch):
    repo = install_use_case(monkeypatch, OrderStatus.CREATED)
    repo.find_by_order_id.return_value = None
    response = authorized_client(monkeypatch).patch("/orders/unknown/status", json={"status": "PROCESSING"})
    assert response.status_code == 404
    assert response.json() == {"detail": "Commande introuvable (NOT_FOUND)."}
    repo.update_status.assert_not_awaited()


@pytest.mark.parametrize("payload", [
    {"status": "unknown"}, {"status": "processing"},
    {"status": None}, {"status": 1}, {"status": []}, {}, ["PROCESSING"],
])
def test_bad_status_rejected_before_use_case(monkeypatch, payload):
    use_case = SimpleNamespace(execute=AsyncMock())
    monkeypatch.setattr(app.state, "update_order_status_use_case", use_case)
    response = authorized_client(monkeypatch).patch("/orders/order-123/status", json=payload)
    assert response.status_code == 422
    use_case.execute.assert_not_awaited()


@pytest.mark.parametrize("current", [
    OrderStatus.CREATED, OrderStatus.PROCESSING,
    OrderStatus.SHIPPED, OrderStatus.DELIVERED,
])
def test_generic_patch_cannot_bypass_cancellation(monkeypatch, current):
    repo = install_use_case(monkeypatch, current)
    cancellation = SimpleNamespace(execute=AsyncMock())
    monkeypatch.setattr(app.state, "cancel_order_use_case", cancellation)
    response = authorized_client(monkeypatch).patch("/orders/order-123/status", json={"status": "CANCELLED"})
    assert response.status_code == 409
    assert f"{current.value} -> CANCELLED" in response.json()["detail"]
    repo.find_by_order_id.assert_awaited_once_with("order-123")
    repo.update_status.assert_not_awaited()
    cancellation.execute.assert_not_awaited()


def test_malformed_json_is_validation_error(monkeypatch):
    use_case = SimpleNamespace(execute=AsyncMock())
    monkeypatch.setattr(app.state, "update_order_status_use_case", use_case)
    response = authorized_client(monkeypatch).patch(
        "/orders/order-123/status", content="{", headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422
    use_case.execute.assert_not_awaited()


def test_concurrent_update_is_conflict(monkeypatch):
    repo = install_use_case(monkeypatch, OrderStatus.CREATED)
    repo.update_status.return_value = None
    response = authorized_client(monkeypatch).patch("/orders/order-123/status", json={"status": "PROCESSING"})
    assert response.status_code == 409
    assert "mise à jour" in response.json()["detail"]


@pytest.mark.parametrize("error", [
    InvalidOrderStatusTransitionError("business conflict"),
    OrderStatusConflictError("concurrent conflict"),
])
def test_route_maps_use_case_conflicts_without_revalidating(monkeypatch, error):
    use_case = SimpleNamespace(execute=AsyncMock(side_effect=error))
    monkeypatch.setattr(app.state, "update_order_status_use_case", use_case)
    response = authorized_client(monkeypatch).patch("/orders/order-123/status", json={"status": "PROCESSING"})
    assert response.status_code == 409
    assert response.json()["detail"] == str(error)
    use_case.execute.assert_awaited_once_with("order-123", OrderStatus.PROCESSING)


def test_route_returns_use_case_result_without_own_transition_logic(monkeypatch):
    use_case = SimpleNamespace(execute=AsyncMock(return_value=make_order(OrderStatus.DELIVERED)))
    monkeypatch.setattr(app.state, "update_order_status_use_case", use_case)
    response = authorized_client(monkeypatch).patch("/orders/order-123/status", json={"status": "PROCESSING"})
    assert response.status_code == 200
    assert response.json()["status"] == "DELIVERED"
    use_case.execute.assert_awaited_once_with("order-123", OrderStatus.PROCESSING)


def test_unexpected_error_uses_existing_sanitized_500_and_logging(monkeypatch, caplog):
    use_case = SimpleNamespace(execute=AsyncMock(side_effect=RuntimeError("private database detail")))
    monkeypatch.setattr(app.state, "update_order_status_use_case", use_case)
    response = authorized_client(monkeypatch).patch("/orders/order-123/status", json={"status": "PROCESSING"})
    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error"}
    assert response.headers["X-Request-ID"]
    assert any(record.getMessage() == "http_request_failed" for record in caplog.records)
    assert "private database detail" not in response.text


def test_composition_root_wires_existing_repository():
    assert isinstance(app.state.update_order_status_use_case, UpdateOrderStatusUseCase)
    assert app.state.update_order_status_use_case.order_repository is order_repository
