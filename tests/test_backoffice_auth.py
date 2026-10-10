from contextlib import asynccontextmanager
from datetime import datetime, timezone
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from stockpilot.application.dto.intent_result import IntentResult
from stockpilot.domain.entities.order import Order
from stockpilot.domain.enums.intent import Intent
from stockpilot.domain.enums.order_status import OrderStatus
from stockpilot.infrastructure.config.settings import Settings
import stockpilot.main as main
from stockpilot.main import app
from stockpilot.presentation.api.routes.assistant import customer_thread_key
from stockpilot.observability import JsonFormatter
from customer_auth import CUSTOMER_JWT_SECRET, customer_headers


TOKEN = "test-only-token"
ENDPOINTS = [
    ("PATCH", "/orders/auth-order/status", {"status": "PROCESSING"},
     "update_order_status_use_case"),
    ("POST", "/orders/auth-order/cancel", None, "cancel_order_use_case"),
]


@pytest.fixture
def mutation_use_cases(monkeypatch):
    use_cases = {}
    for _, _, _, name in ENDPOINTS:
        use_case = SimpleNamespace(execute=AsyncMock(
            side_effect=AssertionError("Authentication must precede the mutation"),
        ))
        monkeypatch.setattr(app.state, name, use_case)
        use_cases[name] = use_case
    return use_cases


def configure_token(monkeypatch, token):
    monkeypatch.setattr(
        app.state, "settings", Settings(
            backoffice_api_token=token,
            customer_jwt_secret=CUSTOMER_JWT_SECRET,
            _env_file=None,
        ),
    )


def request(client, endpoint, authorization=None):
    method, path, payload, _ = endpoint
    headers = {} if authorization is None else {"Authorization": authorization}
    return client.request(method, path, json=payload, headers=headers)


def assert_no_mutation(use_cases):
    for use_case in use_cases.values():
        use_case.execute.assert_not_awaited()


@pytest.mark.parametrize("endpoint", ENDPOINTS)
@pytest.mark.parametrize("token", [None, "", " ", "\t\n"])
@pytest.mark.parametrize("authorization", [None, f"Bearer {TOKEN}"])
def test_missing_configuration_fails_closed(
    monkeypatch, mutation_use_cases, endpoint, token, authorization,
):
    configure_token(monkeypatch, token)
    response = request(TestClient(app), endpoint, authorization)
    assert response.status_code == 503
    assert response.json() == {
        "detail": "Back-office authentication is not configured.",
    }
    assert TOKEN not in response.text
    assert_no_mutation(mutation_use_cases)


@pytest.mark.parametrize("endpoint", ENDPOINTS)
@pytest.mark.parametrize("authorization", [
    None, "Bearer wrong-token", f"Basic {TOKEN}", f"Token {TOKEN}",
    "Bearer", "Bearer ", "Bearer   ", f"Bearer  {TOKEN}",
    f"Bearer\t{TOKEN}", f"Bearer \t{TOKEN}", f"Bearer {TOKEN} ",
    f"Bearer {TOKEN}\t", f" Bearer {TOKEN}", f"Bearer {TOKEN} extra",
], ids=[
    "absent", "wrong-token", "basic-scheme", "token-scheme",
    "scheme-only", "empty-token", "whitespace-only", "double-space",
    "tab-separator", "space-tab-prefix", "trailing-space", "trailing-tab",
    "leading-space", "extra-word",
])
def test_invalid_credentials_have_generic_challenge_and_never_mutate(
    monkeypatch, mutation_use_cases, endpoint, authorization,
):
    configure_token(monkeypatch, TOKEN)
    response = request(TestClient(app), endpoint, authorization)
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid back-office credentials."}
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert TOKEN not in response.text
    assert_no_mutation(mutation_use_cases)


@pytest.mark.parametrize("endpoint", ENDPOINTS)
@pytest.mark.parametrize(("configured", "authorization"), [
    (TOKEN, b"Bearer mauvais-\xc3\xa9"),
    ("test-only-\u00e9", b"Bearer wrong-token"),
])
def test_non_ascii_wrong_credentials_return_401_not_500(
    monkeypatch, mutation_use_cases, endpoint, configured, authorization,
):
    configure_token(monkeypatch, configured)
    response = request(TestClient(app), endpoint, authorization)
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid back-office credentials."}
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert_no_mutation(mutation_use_cases)


@pytest.mark.parametrize("endpoint", ENDPOINTS)
@pytest.mark.parametrize("scheme", ["Bearer", "bearer", "BEARER"])
def test_correct_credentials_delegate_without_passing_token_to_use_case(
    monkeypatch, mutation_use_cases, endpoint, scheme,
):
    configure_token(monkeypatch, TOKEN)
    status = OrderStatus.PROCESSING if endpoint[0] == "PATCH" else OrderStatus.CANCELLED
    order = Order(
        order_id="auth-order", operation_id="auth-operation",
        product_name="Bosch X100", quantity=2, status=status,
        created_at=datetime(2026, 10, 10, tzinfo=timezone.utc),
    )
    use_case = mutation_use_cases[endpoint[3]]
    use_case.execute.side_effect = None
    use_case.execute.return_value = order
    response = request(TestClient(app), endpoint, f"{scheme} {TOKEN}")
    assert response.status_code == 200
    assert response.json()["status"] == status.value
    if endpoint[0] == "PATCH":
        use_case.execute.assert_awaited_once_with("auth-order", OrderStatus.PROCESSING)
    else:
        use_case.execute.assert_awaited_once_with("auth-order")
    for name, other in mutation_use_cases.items():
        if name != endpoint[3]:
            other.execute.assert_not_awaited()


@pytest.mark.parametrize("endpoint", ENDPOINTS)
@pytest.mark.parametrize("outcome", ["success", "unauthorized", "unconfigured", "failure"])
def test_token_never_appears_in_response_or_structured_logs(
    monkeypatch, mutation_use_cases, endpoint, outcome, caplog,
):
    configure_token(monkeypatch, None if outcome == "unconfigured" else TOKEN)
    use_case = mutation_use_cases[endpoint[3]]
    use_case.execute.side_effect = None
    use_case.execute.return_value = Order(
        order_id="auth-order", operation_id="auth-operation", product_name="Bosch X100",
        quantity=2, status=OrderStatus.CANCELLED,
        created_at=datetime(2026, 10, 10, tzinfo=timezone.utc),
    )
    if outcome == "failure":
        use_case.execute.side_effect = RuntimeError(f"private failure with {TOKEN}")
    authorization = "Bearer rejected-test-token" if outcome == "unauthorized" else f"Bearer {TOKEN}"
    with caplog.at_level(logging.INFO, logger="stockpilot"):
        response = request(TestClient(app), endpoint, authorization)
    assert response.status_code == {
        "success": 200, "unauthorized": 401, "unconfigured": 503, "failure": 500,
    }[outcome]
    assert TOKEN not in response.text
    assert TOKEN not in str(dict(response.headers))
    records = [record for record in caplog.records if record.name.startswith("stockpilot")]
    assert records
    for record in records:
        assert TOKEN not in record.getMessage()
        assert TOKEN not in repr(getattr(record, "fields", {}))
        assert TOKEN not in JsonFormatter().format(record)
    if outcome == "failure":
        assert response.json() == {"detail": "Internal server error"}
        assert any(record.getMessage() == "http_request_failed" for record in records)


@pytest.mark.parametrize("token", [None, TOKEN])
@pytest.mark.parametrize("authorization", [None, "Bearer wrong-token"])
def test_public_routes_remain_accessible_without_backoffice_auth(
    monkeypatch, mutation_use_cases, token, authorization,
):
    configure_token(monkeypatch, token)
    generate = SimpleNamespace(execute=AsyncMock(return_value="mock advice"))
    classify = SimpleNamespace(execute=AsyncMock(
        return_value=IntentResult(intent=Intent.PRODUCT_ADVICE),
    ))
    workflow = SimpleNamespace(ainvoke=AsyncMock(return_value={"response": "mock workflow"}))
    monkeypatch.setattr(app.state, "generate_response_use_case", generate)
    monkeypatch.setattr(app.state, "classify_intent_use_case", classify)
    monkeypatch.setattr(app.state, "stockpilot_workflow", workflow)
    client = TestClient(app, headers={} if authorization is None else {
        "Authorization": authorization,
    })
    assert client.get("/health").json() == {"status": "UP"}
    assert client.post("/chat", json={"message": "advice"}).json() == {
        "response": "mock advice",
    }
    response = client.post("/classify", json={"message": "classify"})
    assert response.status_code == 200
    assert response.json()["intent"] == "PRODUCT_ADVICE"
    response = client.post("/assistant", headers=customer_headers("customer-a"),
                           json={"message": "hello", "thread_id": "auth-test"})
    assert response.status_code == 200
    assert response.json() == {"response": "mock workflow"}
    generate.execute.assert_awaited_once_with("advice")
    classify.execute.assert_awaited_once_with("classify")
    workflow.ainvoke.assert_awaited_once_with(
        {"message": "hello", "customer_id": "customer-a"},
        config={"configurable": {"thread_id": customer_thread_key("customer-a", "auth-test")}},
    )
    assert_no_mutation(mutation_use_cases)


def test_settings_token_is_optional_secret_and_excluded_from_repr(monkeypatch):
    monkeypatch.delenv("BACKOFFICE_API_TOKEN", raising=False)
    assert Settings(_env_file=None).backoffice_api_token is None
    field = Settings.model_fields["backoffice_api_token"]
    assert field.default is None
    assert field.repr is False
    settings = Settings(backoffice_api_token=TOKEN, _env_file=None)
    assert isinstance(settings.backoffice_api_token, SecretStr)
    assert settings.backoffice_api_token.get_secret_value() == TOKEN
    assert TOKEN not in repr(settings)
    assert "backoffice_api_token" not in repr(settings)
    assert TOKEN not in str(settings.backoffice_api_token)
    assert TOKEN not in settings.model_dump_json()


def test_settings_support_environment_and_explicit_override(monkeypatch):
    monkeypatch.setenv("BACKOFFICE_API_TOKEN", "test-only-environment-token")
    settings = Settings(_env_file=None)
    assert settings.backoffice_api_token.get_secret_value() == "test-only-environment-token"
    overridden = Settings(backoffice_api_token=TOKEN, _env_file=None)
    assert overridden.backoffice_api_token.get_secret_value() == TOKEN
    assert Settings(backoffice_api_token=None, _env_file=None).backoffice_api_token is None


def test_composition_root_exposes_existing_settings():
    assert app.state.settings is main.settings


def test_missing_token_does_not_prevent_startup(monkeypatch):
    settings = Settings(
        backoffice_api_token=None, otel_enabled=False, log_file=None, _env_file=None,
    )
    monkeypatch.setattr(main, "settings", settings)
    monkeypatch.setattr(app.state, "settings", settings)
    initialize = Mock()
    monkeypatch.setattr(main.order_repository, "initialize", initialize)
    monkeypatch.setattr(main, "configure_logging", Mock())
    monkeypatch.setattr(main, "telemetry_provider", None)
    workflow = SimpleNamespace(ainvoke=AsyncMock())
    monkeypatch.setattr(main, "build_graph", Mock(return_value=workflow))

    @asynccontextmanager
    async def checkpointer(_):
        yield SimpleNamespace()

    monkeypatch.setattr(main, "open_checkpointer", checkpointer)
    monkeypatch.setattr(app.state, "stockpilot_workflow", None)
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        response = client.post("/orders/auth-order/cancel")
        assert response.status_code == 503
        assert app.state.stockpilot_workflow is workflow
    initialize.assert_called_once_with()
    workflow.ainvoke.assert_not_awaited()


def test_openapi_separates_customer_technical_and_admin_session_security():
    schema = app.openapi()
    schemes = schema["components"]["securitySchemes"]
    bearer_names = {
        name for name, value in schemes.items()
        if value.get("type") == "http" and value.get("scheme") == "bearer"
    }
    assert bearer_names
    protected = {
        ("get", "/backoffice/orders"),
        ("get", "/backoffice/orders/{order_id}"),
        ("get", "/orders"),
        ("get", "/orders/{order_id}"),
        ("patch", "/orders/{order_id}/status"),
        ("post", "/orders/{order_id}/cancel"),
        ("post", "/customer/orders/{order_id}/cancel"),
        ("post", "/assistant"),
        ("get", "/backoffice/session/me"),
        ("post", "/backoffice/session/logout"),
        ("patch", "/backoffice/orders/{order_id}/status"),
        ("post", "/backoffice/orders/{order_id}/cancel"),
        ("get", "/backoffice/products"),
        ("get", "/backoffice/products/{sku}"),
        ("post", "/backoffice/products"),
        ("patch", "/backoffice/products/{sku}"),
    }
    actual = set()
    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            if method not in {"get", "post", "patch", "put", "delete", "head", "options"}:
                continue
            security = operation.get("security", schema.get("security", []))
            if security:
                actual.add((method, path))
                assert any(set(requirement) & (bearer_names | {"AdminSessionCookie"}) for requirement in security)
                assert {} not in security
    assert actual == protected
    assert schema["paths"]["/assistant"]["post"]["security"] == [
        {"CustomerBearer": []},
    ]
    assert schema["paths"]["/orders/{order_id}/cancel"]["post"]["security"] == [
        {"BackofficeBearer": []},
    ]
    assert schema["paths"]["/backoffice/orders"]["get"]["security"] == [
        {"BackofficeBearer": []}, {"AdminSessionCookie": []},
    ]
    assert schema["paths"]["/backoffice/session/me"]["get"]["security"] == [{"AdminSessionCookie": []}]
    for path, method in (("/backoffice/orders/{order_id}/status", "patch"),
                         ("/backoffice/orders/{order_id}/cancel", "post"),
                         ("/backoffice/products", "post"),
                         ("/backoffice/products/{sku}", "patch")):
        assert schema["paths"][path][method]["security"] == [{"AdminSessionCookie": []}]
    assert "security" not in schema["paths"]["/backoffice/session/login"]["post"]
