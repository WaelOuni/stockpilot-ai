from unittest.mock import MagicMock

from fastapi.testclient import TestClient
from pymongo.errors import AutoReconnect, NetworkTimeout
import pytest

from scripts.verify_demo_dataset import ORIGIN, _plan_summary, compose_read_app, main
from stockpilot.infrastructure.config.settings import Settings
from stockpilot.presentation.api.admin_auth import password_verifier


def test_read_composition_blocks_every_persistent_mutation_without_repository_calls():
    database = MagicMock()
    app = compose_read_app(database, Settings())
    with TestClient(app, base_url=ORIGIN) as client:
        for method, path in [
            ("POST", "/backoffice/products/DEMO-1/stock-adjustments"),
            ("POST", "/customer/orders/demo/cancel"),
            ("POST", "/orders/demo/cancel"),
            ("PATCH", "/orders/demo/status"),
            ("PATCH", "/backoffice/orders/demo/status"),
            ("POST", "/backoffice/orders/demo/cancel"),
            ("POST", "/backoffice/products"),
        ]:
            assert client.request(method, path, json={}).status_code == 405
    for collection in ("products", "orders", "stock_movements"):
        database[collection].find.assert_not_called()
        database[collection].insert_one.assert_not_called()
        database[collection].update_one.assert_not_called()
        database[collection].create_index.assert_not_called()


def test_real_session_expiry_and_recomposition_are_read_only():
    database = MagicMock()
    password = "fictional-test-password-for-demo-only"
    settings = Settings(
        admin_username="demo-admin", admin_password_verifier=password_verifier(password),
        admin_trusted_origin=ORIGIN, backoffice_api_token=None, customer_jwt_secret=None,
    )
    app = compose_read_app(database, settings)
    with TestClient(app, base_url=ORIGIN) as client:
        response = client.post(
            "/backoffice/session/login",
            json={"username": "demo-admin", "password": password},
            headers={"origin": ORIGIN, "x-admin-csrf": "login"},
        )
        assert response.status_code == 200
        assert client.get("/backoffice/session/me").status_code == 200
        old_cookies = dict(client.cookies)
        app.state.admin_sessions.clock = lambda: float("inf")
        assert client.get("/backoffice/session/me").status_code == 401
    with TestClient(compose_read_app(database, settings), base_url=ORIGIN) as restarted:
        restarted.cookies.update(old_cookies)
        assert restarted.get("/backoffice/session/me").status_code == 401
    database["products"].find.assert_not_called()
    database["orders"].find.assert_not_called()


@pytest.mark.parametrize("error", [NetworkTimeout, AutoReconnect])
def test_simulated_read_network_errors_fail_closed_without_mutation(error):
    database = MagicMock()
    database["products"].find.side_effect = error("withheld simulated driver details")
    app = compose_read_app(database, Settings())
    with TestClient(app, base_url=ORIGIN, raise_server_exceptions=False) as client:
        response = client.get("/products/DEMO-1")
        assert response.status_code == 500
        assert response.text == "Internal Server Error"
    for collection in ("products", "orders", "stock_movements"):
        database[collection].insert_one.assert_not_called()
        database[collection].update_one.assert_not_called()
        database[collection].delete_one.assert_not_called()


def test_non_demo_read_cli_refuses_before_connection(monkeypatch, capsys):
    monkeypatch.setattr("scripts.verify_demo_dataset.Settings", lambda: Settings())
    client = MagicMock()
    monkeypatch.setattr("scripts.verify_demo_dataset.MongoClient", client)
    assert main(["--database", "stockpilot"]) == 1
    client.assert_not_called()
    assert "only permitted demo target" in capsys.readouterr().out


def test_explain_summary_uses_only_winning_plan_and_execution_statistics():
    summary = _plan_summary({
        "queryPlanner": {
            "winningPlan": {"stage": "FETCH", "inputStage": {"stage": "IXSCAN", "indexName": "approved"}},
            "rejectedPlans": [{"stage": "COLLSCAN"}],
        },
        "executionStats": {
            "nReturned": 6, "totalDocsExamined": 6, "totalKeysExamined": 6, "executionTimeMillis": 1,
        },
    })
    assert summary == {
        "stages": ["FETCH", "IXSCAN"], "indexes": ["approved"], "returned": 6,
        "docs_examined": 6, "keys_examined": 6, "server_execution_ms": 1,
    }
