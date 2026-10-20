"""BFF bridge contracts against the real BFF service and existing pure fixtures."""

from dataclasses import replace

import pytest
from test_bff_auth import begin, complete, signed_in
from test_bff_auth import env as env  # noqa: PLC0414 - explicit pytest fixture re-export
from test_tenant_authorization import CATALOGUE, ORDER, Buyers, Ownership

from stockpilot.application.ports.bff_security import BffUnavailable
from stockpilot.application.ports.tenant_authorization import TenantContextUnavailable
from stockpilot.application.services.tenant_authorization import (
    TenantAuthorizationService,
)
from stockpilot.domain.entities.tenant_authorization import (
    BuyerAssociation,
    OrderResource,
    OrganizationResource,
)
from stockpilot.domain.enums.identity_status import IdentityStatus
from stockpilot.domain.enums.membership_status import MembershipStatus
from stockpilot.domain.enums.organization_status import OrganizationStatus
from stockpilot.domain.enums.role import Role
from stockpilot.domain.enums.tenant_authorization import AuthorizationDecision as D
from stockpilot.domain.enums.tenant_authorization import BuyerAssociationStatus
from stockpilot.domain.enums.tenant_authorization import TenantOperation as O
from stockpilot.infrastructure.identity.bff_tenant_context import (
    BffTenantContextSource,
    BffTenantRequest,
)
from stockpilot.presentation.api.routes.bff_auth import SESSION_COOKIE


def setup_bridge(env):
    signed_in(env)
    source = BffTenantContextSource(env[1])
    request_context = BffTenantRequest(env[0].cookies.get(SESSION_COOKIE))
    return source, request_context


def test_valid_bff_context_allows_its_explicitly_owned_resources(env):
    source, request_context = setup_bridge(env)
    ownership, buyers = Ownership(), Buyers()
    ownership.records[("o1", CATALOGUE)] = (
        OrganizationResource(CATALOGUE, "o1", "v1"),
    )
    ownership.records[("o1", ORDER)] = (
        OrderResource(ORDER, "o1", "v1", "u1", "explicit-buyer"),
    )
    buyers.records[("o1", "u1", "explicit-buyer")] = (
        BuyerAssociation(
            "grant-u1",
            "o1",
            "u1",
            "explicit-buyer",
            BuyerAssociationStatus.ACTIVE,
            "v1",
        ),
    )
    service = TenantAuthorizationService(source, ownership, buyers)
    context = service.resolve_context(request_context)
    assert (
        service.decide(request_context, context, O.CATALOGUE_READ, CATALOGUE) is D.ALLOW
    )
    assert service.decide(request_context, context, O.ORDER_READ_OWN, ORDER) is D.ALLOW
    assert buyers.calls == [("o1", "u1", "explicit-buyer")]


def test_bridge_uses_validated_session_principal_and_private_context_id(env):
    source, request_context = setup_bridge(env)
    context = source.current(request_context)
    assert context.organization_id == "o1" and context.principal.user_id == "u1"
    assert context.principal.membership_id == "m1"
    assert request_context.session_cookie not in repr(context)
    assert request_context.session_cookie not in repr(request_context)


@pytest.mark.parametrize(
    "untrusted",
    [
        None,
        {},
        {"organization_id": "o1", "role": "ORGANIZATION_ADMIN"},
        "cookie",
        "verified",
    ],
)
def test_headers_json_and_raw_values_cannot_be_context(env, untrusted):
    source, _ = setup_bridge(env)
    assert source.current(untrusted) is None


@pytest.mark.parametrize("cookie", [None, "", "invalid", "a" * 43])
def test_unvalidated_cookie_never_constructs_tenant_context(env, cookie):
    source, _ = setup_bridge(env)
    assert source.current(BffTenantRequest(cookie)) is None


@pytest.mark.parametrize(
    "cause",
    ["expired", "user_disabled", "membership_suspended", "org_suspended", "logout"],
)
def test_bridge_refuses_revoked_or_expired_access(env, cause):
    info = signed_in(env)
    source = BffTenantContextSource(env[1])
    request_context = BffTenantRequest(env[0].cookies.get(SESSION_COOKIE))
    if cause == "expired":
        env[2][0] = env[2][0].replace(minute=2)
    elif cause == "user_disabled":
        env[3].user = replace(env[3].user, status=IdentityStatus.DISABLED)
    elif cause == "membership_suspended":
        env[3].membership = replace(
            env[3].membership, status=MembershipStatus.SUSPENDED
        )
    elif cause == "org_suspended":
        env[3].organization = replace(
            env[3].organization, status=OrganizationStatus.SUSPENDED
        )
    elif cause == "logout":
        assert (
            env[0]
            .post(
                "/b2b/auth/logout",
                headers={
                    "Origin": "https://bff.example.test",
                    "X-BFF-CSRF": info["csrf_token"],
                },
            )
            .status_code
            == 200
        )
    assert source.current(request_context) is None


def test_storage_outage_is_not_an_unknown_identity(env):
    source, request_context = setup_bridge(env)
    current = source.current(request_context)

    def failure(*_args):
        raise BffUnavailable("private storage credentials")

    env[1].store.get_session = failure
    with pytest.raises(TenantContextUnavailable) as caught:
        source.current(request_context)
    assert "credentials" not in str(caught.value)
    service = TenantAuthorizationService(source, Ownership(), Buyers())
    assert service.decide(request_context, None, O.CATALOGUE_READ, CATALOGUE) is D.DENY
    assert (
        service.decide(request_context, current, O.CATALOGUE_READ, CATALOGUE)
        is D.INDETERMINATE
    )


def test_missing_bff_service_is_technical_failure():
    source = BffTenantContextSource(None)
    with pytest.raises(TenantContextUnavailable):
        source.current(BffTenantRequest("a" * 43))


def test_revoked_permissions_make_old_bff_tenant_context_obsolete(env):
    source, request_context = setup_bridge(env)
    service = TenantAuthorizationService(source, Ownership(), Buyers())
    old_context = service.resolve_context(request_context)
    env[3].membership = replace(env[3].membership, role=Role.READ_ONLY)
    new_context = service.resolve_context(request_context)
    assert old_context != new_context
    assert new_context.principal.role is Role.READ_ONLY
    assert (
        service.decide(request_context, old_context, O.CATALOGUE_READ, CATALOGUE)
        is D.DENY
    )


def test_rotated_session_cannot_reuse_previous_authorization_context(env):
    source, old_request = setup_bridge(env)
    old_context = source.current(old_request)
    begin(env)
    assert complete(env).status_code == 303
    new_request = BffTenantRequest(env[0].cookies.get(SESSION_COOKIE))
    new_context = source.current(new_request)
    assert source.current(old_request) is None
    assert new_context.authorization_context_id != old_context.authorization_context_id
    service = TenantAuthorizationService(source, Ownership(), Buyers())
    assert (
        service.decide(new_request, old_context, O.CATALOGUE_READ, CATALOGUE) is D.DENY
    )


def test_bridge_adds_no_http_route_or_business_proxy(env):
    signed_in(env)
    for path in (
        "/b2b/tenant",
        "/b2b/authorize",
        "/b2b/proxy/products",
        "/b2b/orders",
        "/products",
        "/assistant",
    ):
        assert env[0].get(path).status_code == 404
        assert env[0].post(path, json={"organization_id": "o1"}).status_code == 404
