"""A verified identity refusal is recoverable without granting tenant access."""

import pytest

from stockpilot.application.ports.bff_security import BffUnauthenticated
from stockpilot.presentation.api.routes.bff_auth import SESSION_COOKIE, TRANSACTION_COOKIE
from test_bff_auth import LOGIN_HEADERS, begin, complete, env, settings, signed_in


@pytest.mark.parametrize("organization", ["foreign-org", "o1"])
def test_business_membership_denial_returns_safe_selector_and_revokes_previous(env, organization):
    client, service, _, repos = env
    signed_in(env)
    old_cookie = client.cookies.get(SESSION_COOKIE)
    client.app.state.bff_settings = settings(mode="b2b_business_test")
    if organization == "o1":
        repos.membership = None
    assert client.post("/b2b/auth/login", json={"organization_id": organization}, headers=LOGIN_HEADERS).status_code == 200
    state, browser = service.provider.state, client.cookies.get(TRANSACTION_COOKIE)
    response = complete(env)
    assert response.status_code == 303
    assert response.headers["location"] == "/b2b-app/?login=denied"
    assert "foreign-org" not in str(response.headers) and "Fixture" not in response.text
    assert not client.cookies.get(SESSION_COOKIE)
    assert not client.cookies.get(TRANSACTION_COOKIE)
    assert not service.store.sessions
    assert "no-store" in response.headers["cache-control"]
    with pytest.raises(BffUnauthenticated):
        service.current(old_cookie)
    client.cookies.set(TRANSACTION_COOKIE, browser, domain="bff.example.test", path="/")
    assert complete(env, state).status_code == 401


def test_denial_does_not_change_auth_only_http_contract(env):
    begin(env, "foreign-org")
    assert complete(env).status_code == 403


def test_storage_failure_is_not_presented_as_membership_denial(env):
    from stockpilot.application.ports.identity_errors import IdentityRepositoryError

    def unavailable(_):
        raise IdentityRepositoryError("private-storage-detail")

    client, _, _, repos = env
    begin(env)
    client.app.state.bff_settings = settings(mode="b2b_business_test")
    repos.find_by_external_identity = unavailable
    result = complete(env)
    assert result.status_code == 503
    assert "location" not in result.headers
    assert "private-storage-detail" not in result.text
