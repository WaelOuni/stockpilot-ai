"""Pure in-memory contract tests: no app import, DB, network or HTTP client."""

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta

import pytest

from stockpilot.application.ports.identity_errors import IdentityRepositoryError
from stockpilot.application.services.identity_authorization import (
    AuthorizationDenied,
    IdentityAuthorizationService,
    IdentityResolutionError,
)
from stockpilot.domain.entities.identity import ExternalIdentity, User
from stockpilot.domain.entities.membership import Membership
from stockpilot.domain.entities.organization import Organization
from stockpilot.domain.entities.principal import ResourceScope
from stockpilot.domain.enums.identity_status import IdentityStatus
from stockpilot.domain.enums.membership_status import MembershipStatus
from stockpilot.domain.enums.organization_status import OrganizationStatus
from stockpilot.domain.enums.permission import Permission as P
from stockpilot.domain.enums.role import Role
from stockpilot.domain.services.authorization import ROLE_PERMISSIONS

NOW = datetime(2026, 10, 20, tzinfo=UTC)
SESSION = object()  # Only this opaque evidence is accepted by the trusted test adapter.


class Authentication:
    def __init__(self, identity):
        self.identity = identity

    def authenticate(self, request_context):
        return self.identity if request_context is SESSION else None


class Identities:
    def __init__(self, users):
        self.users = tuple(users)

    def find_by_external_identity(self, identity):
        return tuple(u for u in self.users if u.external_identity == identity)


class Organizations:
    def __init__(self, organizations):
        self.organizations = {o.organization_id: o for o in organizations}

    def get(self, organization_id):
        return self.organizations.get(organization_id)


class Memberships:
    def __init__(self, memberships):
        self.memberships = tuple(memberships)

    def find_for_user(self, user_id):
        return tuple(m for m in self.memberships if m.user_id == user_id)


@pytest.fixture
def service():
    external = ExternalIdentity("https://id.example/realms/pilot", "subject-1")
    user = User("u1", external, IdentityStatus.ACTIVE, NOW, NOW)
    organization = Organization("o1", "Acme", OrganizationStatus.ACTIVE, NOW, NOW)
    membership = Membership(
        "m1", "u1", "o1", Role.USER, MembershipStatus.ACTIVE, NOW, NOW
    )
    return IdentityAuthorizationService(
        Authentication(external),
        Identities([user]),
        Organizations([organization]),
        Memberships([membership]),
    )


def principal(service):
    return service.resolve(SESSION, "o1")


def test_resolves_only_provisioned_active_human(service):
    value = principal(service)
    assert (value.user_id, value.organization_id, value.membership_id) == (
        "u1",
        "o1",
        "m1",
    )
    assert value.permissions == ROLE_PERMISSIONS[Role.USER]
    assert value.subject == "subject-1"
    assert not hasattr(value, "customer_id")
    assert not hasattr(value, "email")
    assert not hasattr(value, "password")
    with pytest.raises(FrozenInstanceError):
        value.role = Role.ORGANIZATION_ADMIN


@pytest.mark.parametrize("field", ["issuer", "subject"])
@pytest.mark.parametrize("bad", ["", " ", None, 42, " leading", "trailing "])
def test_identity_requires_exact_nonblank_key(field, bad):
    args = {"issuer": "https://id.example", "subject": "subject"}
    args[field] = bad
    with pytest.raises(ValueError):
        ExternalIdentity(**args)


@pytest.mark.parametrize(
    "evidence",
    [
        None,
        {},
        {"verified": True, "role": "ORGANIZATION_ADMIN", "organization_id": "o1"},
        ExternalIdentity("https://id.example/realms/pilot", "subject-1"),
        "subject-1",
    ],
)
def test_payload_or_domain_key_is_not_authentication_evidence(service, evidence):
    with pytest.raises(IdentityResolutionError):
        service.resolve(evidence, "o1")


def test_unknown_identity_is_not_provisioned(service):
    service.authentication.identity = ExternalIdentity("https://unknown", "missing")
    before = service.identities.users, service.memberships.memberships
    with pytest.raises(IdentityResolutionError):
        principal(service)
    assert before == (service.identities.users, service.memberships.memberships)


def test_same_subject_under_different_issuers_is_distinct(service):
    first = service.identities.users[0]
    other = replace(
        first,
        user_id="u2",
        external_identity=ExternalIdentity(
            "https://other", first.external_identity.subject
        ),
    )
    service.identities.users += (other,)
    service.memberships.memberships += (
        replace(service.memberships.memberships[0], membership_id="m2", user_id="u2"),
    )
    assert principal(service).user_id == "u1"
    service.authentication.identity = other.external_identity
    assert principal(service).user_id == "u2"


def test_duplicate_external_identity_is_denied(service):
    service.identities.users += (
        replace(service.identities.users[0], user_id="duplicate"),
    )
    with pytest.raises(IdentityResolutionError):
        principal(service)


def test_inconsistent_identity_repository_is_denied(service):
    foreign = replace(
        service.identities.users[0],
        external_identity=ExternalIdentity("https://foreign", "foreign"),
    )
    service.identities.find_by_external_identity = lambda _: (foreign,)
    with pytest.raises(IdentityResolutionError):
        principal(service)


def test_disabled_user_is_denied(service):
    service.identities.users = (
        replace(service.identities.users[0], status=IdentityStatus.DISABLED),
    )
    with pytest.raises(IdentityResolutionError):
        principal(service)


@pytest.mark.parametrize(
    "status", [OrganizationStatus.SUSPENDED, OrganizationStatus.DISABLED]
)
def test_inactive_organization_is_denied(service, status):
    service.organizations.organizations["o1"] = replace(
        service.organizations.get("o1"), status=status
    )
    with pytest.raises(IdentityResolutionError):
        principal(service)


def test_missing_organization_is_denied(service):
    service.organizations.organizations.clear()
    with pytest.raises(IdentityResolutionError):
        principal(service)


def test_inconsistent_organization_repository_is_denied(service):
    foreign = replace(service.organizations.get("o1"), organization_id="foreign")
    service.organizations.get = lambda _: foreign
    with pytest.raises(IdentityResolutionError):
        principal(service)


@pytest.mark.parametrize(
    "status", [MembershipStatus.INVITED, MembershipStatus.SUSPENDED]
)
def test_nonactive_membership_is_denied(service, status):
    service.memberships.memberships = (
        replace(service.memberships.memberships[0], status=status),
    )
    with pytest.raises(IdentityResolutionError):
        principal(service)


def test_missing_membership_and_foreign_selection_are_denied(service):
    with pytest.raises(IdentityResolutionError):
        service.resolve(SESSION, "foreign")
    service.memberships.memberships = ()
    with pytest.raises(IdentityResolutionError):
        principal(service)


def test_duplicate_active_memberships_are_denied(service):
    service.memberships.memberships += (
        replace(service.memberships.memberships[0], membership_id="m2"),
    )
    with pytest.raises(IdentityResolutionError):
        principal(service)


def test_duplicate_membership_id_is_denied_even_when_other_record_inactive(service):
    service.memberships.memberships += (
        replace(service.memberships.memberships[0], status=MembershipStatus.SUSPENDED),
    )
    with pytest.raises(IdentityResolutionError):
        principal(service)


def test_inconsistent_membership_user_is_denied(service):
    foreign = replace(service.memberships.memberships[0], user_id="foreign")
    service.memberships.find_for_user = lambda _: (foreign,)
    with pytest.raises(IdentityResolutionError):
        principal(service)


def test_two_organizations_require_explicit_authorized_selection(service):
    service.organizations.organizations["o2"] = replace(
        service.organizations.get("o1"), organization_id="o2"
    )
    service.memberships.memberships += (
        replace(
            service.memberships.memberships[0],
            membership_id="m2",
            organization_id="o2",
            role=Role.READ_ONLY,
        ),
    )
    assert principal(service).organization_id == "o1"
    other = service.resolve(SESSION, "o2")
    assert other.organization_id == "o2"
    assert other.role is Role.READ_ONLY
    for missing in (None, "", " "):
        with pytest.raises(IdentityResolutionError):
            service.resolve(SESSION, missing)


@pytest.mark.parametrize(
    "role", ["UNKNOWN", "PLATFORM_ADMIN", "ORGANIZATION_ADMIN", None]
)
def test_untyped_or_reserved_role_cannot_enter_membership(service, role):
    with pytest.raises(TypeError):
        replace(service.memberships.memberships[0], role=role)


@pytest.mark.parametrize("role", list(Role))
def test_each_role_has_explicit_permissions_and_no_foreign_scope(service, role):
    service.memberships.memberships = (
        replace(service.memberships.memberships[0], role=role),
    )
    value = principal(service)
    for permission in P:
        if permission in ROLE_PERMISSIONS[role]:
            service.require(
                SESSION, value, permission, resource=ResourceScope("o1", "u1")
            )
        else:
            with pytest.raises(AuthorizationDenied):
                service.require(
                    SESSION, value, permission, resource=ResourceScope("o1", "u1")
                )
        with pytest.raises(AuthorizationDenied):
            service.require(
                SESSION, value, permission, resource=ResourceScope("foreign", "u1")
            )


@pytest.mark.parametrize("permission", [P.ORDER_READ_OWN, P.ORDER_CANCEL_OWN])
@pytest.mark.parametrize("owner", [None, "someone-else"])
def test_own_permission_requires_exact_owner(service, permission, owner):
    with pytest.raises(AuthorizationDenied):
        service.require(
            SESSION, principal(service), permission, resource=ResourceScope("o1", owner)
        )


@pytest.mark.parametrize(
    "changes",
    [
        {
            "role": Role.ORGANIZATION_ADMIN,
            "permissions": ROLE_PERMISSIONS[Role.ORGANIZATION_ADMIN],
        },
        {"permissions": frozenset(P)},
        {"user_id": "u2"},
        {"subject": "foreign"},
        {"issuer": "https://foreign"},
        {"membership_id": "foreign"},
    ],
)
def test_forged_principal_is_denied(service, changes):
    forged = replace(principal(service), **changes)
    with pytest.raises(AuthorizationDenied):
        service.require(
            SESSION, forged, P.ORDER_CREATE, resource=ResourceScope("o1", "u1")
        )


def test_changed_browser_session_is_denied(service):
    with pytest.raises(AuthorizationDenied):
        service.require(
            object(),
            principal(service),
            P.ORDER_CREATE,
            resource=ResourceScope("o1", "u1"),
        )


def test_stale_principal_is_denied_after_membership_suspension(service):
    value = principal(service)
    service.memberships.memberships = (
        replace(service.memberships.memberships[0], status=MembershipStatus.SUSPENDED),
    )
    with pytest.raises(AuthorizationDenied):
        service.require(
            SESSION, value, P.ORDER_CREATE, resource=ResourceScope("o1", "u1")
        )


@pytest.mark.parametrize(
    "field", ["authentication", "identities", "organizations", "memberships"]
)
def test_incomplete_configuration_is_denied(service, field):
    broken = replace(service, **{field: None})
    with pytest.raises(IdentityResolutionError):
        principal(broken)


@pytest.mark.parametrize(
    "value,permission,scope",
    [
        (None, P.CATALOGUE_READ, ResourceScope("o1")),
        ({}, P.CATALOGUE_READ, ResourceScope("o1")),
        ("valid", "catalogue:read", ResourceScope("o1")),
        ("valid", None, ResourceScope("o1")),
        ("valid", P.CATALOGUE_READ, None),
        ("valid", P.CATALOGUE_READ, {"organization_id": "o1"}),
    ],
)
def test_incomplete_decision_denies_by_default(service, value, permission, scope):
    value = principal(service) if value == "valid" else value
    with pytest.raises(AuthorizationDenied):
        service.require(SESSION, value, permission, resource=scope)


@pytest.mark.parametrize(
    "field", ["user_id", "issuer", "subject", "organization_id", "membership_id"]
)
def test_incomplete_principal_is_rejected(service, field):
    with pytest.raises(ValueError):
        replace(principal(service), **{field: ""})


def test_mutable_permissions_are_rejected(service):
    with pytest.raises(ValueError):
        replace(principal(service), permissions=set(P))


def test_repository_technical_errors_remain_distinct(service):
    def unavailable(_):
        raise IdentityRepositoryError("Unavailable")

    service.identities.find_by_external_identity = unavailable
    with pytest.raises(IdentityRepositoryError):
        principal(service)


def test_email_metadata_change_does_not_change_identity(service):
    service.authentication.email = "before@example.test"
    before = principal(service)
    service.authentication.email = "after@example.test"
    assert principal(service) == before


def test_dates_are_aware_and_monotonic(service):
    user = service.identities.users[0]
    with pytest.raises(ValueError):
        replace(user, created_at=NOW.replace(tzinfo=None))
    with pytest.raises(ValueError):
        replace(user, updated_at=NOW - timedelta(seconds=1))


def test_permission_matrix_is_immutable():
    with pytest.raises(TypeError):
        ROLE_PERMISSIONS[Role.USER] = frozenset(P)
    assert Role.__members__.get("PLATFORM_ADMIN") is None
    assert P.IMPORT_EXECUTE not in ROLE_PERMISSIONS[Role.MANAGER]
