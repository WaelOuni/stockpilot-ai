"""Browser return contract, only for opt-in business mode; no network/database."""

import pytest
from test_b2b_http_contracts import auth_client, configuration
from test_bff_auth import LOGIN_HEADERS


def authenticate(client):
    result = client.post(
        "/b2b/auth/login",
        json={"organization_id": "o1"},
        headers=LOGIN_HEADERS,
    )
    assert result.status_code == 200
    state = client.app.state.bff_service.provider.state
    return client.get("/b2b/auth/callback", params={"state": state, "code": "one-code"})


def test_business_callback_returns_only_to_fixed_vue_path():
    client = auth_client(configuration())
    result = authenticate(client)
    assert result.status_code == 303
    assert result.headers["location"] == "/b2b-app/"
    assert "__Host-stockpilot_bff=" in result.headers["set-cookie"]
    assert "HttpOnly" in result.headers["set-cookie"]


def test_session_display_organization_comes_from_repository_not_browser():
    client = auth_client(configuration())
    authenticate(client)
    result = client.get("/b2b/auth/session")
    assert result.status_code == 200
    body = result.json()
    assert body["organization_name"] == "Fixture"
    assert body["idle_expires_at"] <= body["expires_at"]
    assert not {"access_token", "id_token", "refresh_token"} & set(body)


def test_callback_does_not_accept_return_url_or_user_supplied_redirect():
    client = auth_client(configuration())
    result = client.get(
        "/b2b/auth/callback",
        params={
            "state": "x" * 43,
            "code": "one-code",
            "return_url": "https://evil.test",
        },
    )
    assert result.status_code == 401
    assert "location" not in result.headers


def test_provider_logout_return_is_fixed_for_vue():
    client = auth_client(configuration())
    result = client.get("/b2b/auth/logged-out")
    assert result.status_code == 303
    assert result.headers["location"] == "/b2b-app/"


@pytest.mark.parametrize(
    "proof,expected", [(None, 404), ("wrong-session", 403), ("current", 404)]
)
def test_optional_read_proof_refuses_a_cookie_from_another_ui_session(proof, expected):
    client = auth_client(configuration())
    authenticate(client)
    client.app.state.bff_business = (
        object()
    )  # only auth boundary, no business route invoked
    csrf = client.get("/b2b/auth/session").json()["csrf_token"]
    headers = (
        {} if proof is None else {"X-BFF-CSRF": csrf if proof == "current" else proof}
    )
    assert (
        client.get("/b2b/api/not-a-business-route", headers=headers).status_code
        == expected
    )
