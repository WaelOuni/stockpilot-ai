"""Adversarial policy contracts only; no claim of real database isolation."""

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime

import pytest

from stockpilot.application.ports.identity_errors import IdentityRepositoryError
from stockpilot.application.ports.tenant_authorization import (
    BuyerAssociationUnavailable,
    ResourceOwnershipUnavailable,
    TenantContextUnavailable,
)
from stockpilot.application.services.identity_authorization import (
    AuthorizationDenied,
    IdentityAuthorizationService,
)
from stockpilot.application.services.tenant_authorization import (
    TenantAuthorizationService,
)
from stockpilot.domain.entities.identity import ExternalIdentity, User
from stockpilot.domain.entities.membership import Membership
from stockpilot.domain.entities.organization import Organization
from stockpilot.domain.entities.tenant_authorization import (
    BuyerAssociation,
    ConversationResource,
    OperationResource,
    OrderResource,
    OrganizationResource,
    ResourceOwnership,
    ResourceReference,
    TenantContext,
    TenantOperationKey,
)
from stockpilot.domain.enums.identity_status import IdentityStatus
from stockpilot.domain.enums.membership_status import MembershipStatus
from stockpilot.domain.enums.organization_status import OrganizationStatus
from stockpilot.domain.enums.permission import Permission
from stockpilot.domain.enums.role import Role
from stockpilot.domain.enums.tenant_authorization import (
    AuthorizationDecision as D,
)
from stockpilot.domain.enums.tenant_authorization import (
    BuyerAssociationStatus as BS,
)
from stockpilot.domain.enums.tenant_authorization import (
    ResourceKind as K,
)
from stockpilot.domain.enums.tenant_authorization import (
    TenantOperation as O,
)
from stockpilot.domain.services.authorization import ROLE_PERMISSIONS

NOW = datetime(2026, 10, 20, tzinfo=UTC)


@pytest.mark.parametrize("decision", list(D))
def test_decisions_cannot_be_used_as_truthy_authorization_flags(decision):
    with pytest.raises(TypeError, match="explicitly"):
        bool(decision)


IDENTITY = ExternalIdentity("https://fixture.example/realm", "subject")
REQUEST = object()
CATALOGUE = ResourceReference(K.CATALOGUE_ITEM, "SKU-0476")
ORDER = ResourceReference(K.ORDER, "ORDER-0001")


class IdentityStores:
    def __init__(self):
        self.user = User("human-a", IDENTITY, IdentityStatus.ACTIVE, NOW, NOW)
        self.organizations = {
            o: Organization(o, o, OrganizationStatus.ACTIVE, NOW, NOW)
            for o in ("org-a", "org-b")
        }
        self.memberships = [
            Membership(
                "m-" + o, "human-a", o, Role.USER, MembershipStatus.ACTIVE, NOW, NOW
            )
            for o in self.organizations
        ]
        self.failure = False

    def find_by_external_identity(self, identity):
        if self.failure:
            raise IdentityRepositoryError("private connection")
        return (
            (self.user,)
            if self.user and self.user.external_identity == identity
            else ()
        )

    def get(self, organization_id):
        return self.organizations.get(organization_id)

    def find_for_user(self, user_id):
        return tuple(m for m in self.memberships if m.user_id == user_id)


class Authentication:
    def __init__(self):
        self.active = True

    def authenticate(self, request):
        return IDENTITY if request is REQUEST and self.active else None


class FreshSource:
    """Test source reusing the actual V13.7.1 resolver on each call."""

    def __init__(self, stores):
        self.auth = Authentication()
        self.resolver = IdentityAuthorizationService(self.auth, stores, stores, stores)
        self.organization_id = "org-a"
        self.context_id = "server-session-a"
        self.calls = 0

    def current(self, request):
        self.calls += 1
        try:
            return TenantContext(
                self.resolver.resolve(request, self.organization_id), self.context_id
            )
        except AuthorizationDenied:
            return None
        except IdentityRepositoryError:
            raise TenantContextUnavailable("Fixture identity unavailable") from None


class Ownership:
    def __init__(self):
        self.records = {
            ("org-a", CATALOGUE): (OrganizationResource(CATALOGUE, "org-a", "v1"),),
            ("org-a", ORDER): (
                OrderResource(ORDER, "org-a", "v1", "human-a", "buyer-legacy-9"),
            ),
        }
        self.calls = []
        self.failure = False

    def find(self, organization_id, reference):
        self.calls.append((organization_id, reference))
        if self.failure:
            raise ResourceOwnershipUnavailable("private resource storage")
        return self.records.get((organization_id, reference), ())


class Buyers:
    def __init__(self):
        self.records = {
            ("org-a", "human-a", "buyer-legacy-9"): (
                BuyerAssociation(
                    "grant-a", "org-a", "human-a", "buyer-legacy-9", BS.ACTIVE, "g1"
                ),
            )
        }
        self.calls = []
        self.failure = False

    def find(self, organization_id, user_id, customer_id):
        self.calls.append((organization_id, user_id, customer_id))
        if self.failure:
            raise BuyerAssociationUnavailable("private mapping storage")
        return self.records.get((organization_id, user_id, customer_id), ())


@pytest.fixture
def env():
    stores = IdentityStores()
    source = FreshSource(stores)
    ownership = Ownership()
    buyers = Buyers()
    service = TenantAuthorizationService(source, ownership, buyers)
    context = service.resolve_context(REQUEST)
    return service, context, stores, source, ownership, buyers


def decide(env, operation=O.CATALOGUE_READ, reference=CATALOGUE, context=None):
    return env[0].decide(
        REQUEST, env[1] if context is None else context, operation, reference
    )


def set_role(env, role):
    env[2].memberships = [
        replace(m, role=role) if m.organization_id == "org-a" else m
        for m in env[2].memberships
    ]
    return env[0].resolve_context(REQUEST)


def test_valid_scope_and_fresh_resolution(env):
    before = env[3].calls
    assert decide(env) is D.ALLOW
    assert env[3].calls == before + 1
    assert env[4].calls == [("org-a", CATALOGUE)]
    assert env[1].principal.user_id != "org-a" and env[1].organization_id == "org-a"


@pytest.mark.parametrize(
    "context", [None, {}, {"organization_id": "org-a", "verified": True}, "raw-cookie"]
)
def test_no_typed_context_denied_without_lookup(env, context):
    assert env[0].decide(REQUEST, context, O.CATALOGUE_READ, CATALOGUE) is D.DENY
    assert not env[4].calls


@pytest.mark.parametrize(
    "untrusted_request",
    [
        {"headers": {"X-Organization-ID": "org-a"}},
        {"organization_id": "org-a", "role": "ORGANIZATION_ADMIN"},
        {"user_id": "human-a", "verified": True},
        IDENTITY,
        None,
    ],
)
def test_browser_identity_or_org_never_authenticates(env, untrusted_request):
    assert (
        env[0].decide(untrusted_request, env[1], O.CATALOGUE_READ, CATALOGUE) is D.DENY
    )


@pytest.mark.parametrize(
    "cause",
    [
        "user_disabled",
        "user_unknown",
        "membership_suspended",
        "membership_absent",
        "org_disabled",
        "org_suspended",
        "org_unknown",
        "auth_revoked",
        "organization_changed",
        "session_changed",
        "role_changed",
        "membership_changed",
    ],
)
def test_obsolete_context_refused(env, cause):
    assert decide(env) is D.ALLOW
    stores, source = env[2], env[3]
    if cause == "user_disabled":
        stores.user = replace(stores.user, status=IdentityStatus.DISABLED)
    elif cause == "user_unknown":
        stores.user = None
    elif cause == "membership_suspended":
        stores.memberships[0] = replace(
            stores.memberships[0], status=MembershipStatus.SUSPENDED
        )
    elif cause == "membership_absent":
        stores.memberships = []
    elif cause == "org_disabled":
        stores.organizations["org-a"] = replace(
            stores.organizations["org-a"], status=OrganizationStatus.DISABLED
        )
    elif cause == "org_suspended":
        stores.organizations["org-a"] = replace(
            stores.organizations["org-a"], status=OrganizationStatus.SUSPENDED
        )
    elif cause == "org_unknown":
        stores.organizations.pop("org-a")
    elif cause == "auth_revoked":
        source.auth.active = False
    elif cause == "organization_changed":
        source.organization_id = "org-b"
    elif cause == "session_changed":
        source.context_id = "server-session-b"
    elif cause == "role_changed":
        set_role(env, Role.READ_ONLY)
    elif cause == "membership_changed":
        stores.memberships[0] = replace(
            stores.memberships[0], membership_id="new-membership"
        )
    assert decide(env) is D.DENY


def test_multiple_organizations_have_no_default_fallback(env):
    env[3].organization_id = "org-b"
    other = env[0].resolve_context(REQUEST)
    assert other.organization_id == "org-b"
    assert env[0].decide(REQUEST, other, O.CATALOGUE_READ, CATALOGUE) is D.NOT_FOUND
    env[3].organization_id = "foreign-org"
    with pytest.raises(AuthorizationDenied):
        env[0].resolve_context(REQUEST)


@pytest.mark.parametrize(
    "change",
    [
        {"user_id": "other"},
        {"issuer": "https://foreign.example"},
        {"subject": "other"},
        {"organization_id": "org-b"},
        {"membership_id": "other"},
        {
            "role": Role.ORGANIZATION_ADMIN,
            "permissions": ROLE_PERMISSIONS[Role.ORGANIZATION_ADMIN],
        },
        {"permissions": frozenset({Permission.STOCK_ADJUST})},
    ],
)
def test_forged_principal_cannot_elevate(env, change):
    forged = replace(env[1], principal=replace(env[1].principal, **change))
    assert decide(env, context=forged) is D.DENY


def test_inconsistent_permissions_from_trusted_source_fail_closed(env):
    altered = replace(
        env[1], principal=replace(env[1].principal, permissions=frozenset())
    )

    class BadSource:
        def current(self, request):
            return altered

    service = TenantAuthorizationService(BadSource(), env[4], env[5])
    assert service.decide(REQUEST, altered, O.CATALOGUE_READ, CATALOGUE) is D.DENY


@pytest.mark.parametrize(
    "role,operation,kind,expected",
    [
        (Role.READ_ONLY, O.CATALOGUE_READ, K.CATALOGUE_ITEM, D.ALLOW),
        (Role.READ_ONLY, O.STOCK_READ, K.STOCK_ITEM, D.ALLOW),
        (Role.READ_ONLY, O.STOCK_ADJUST, K.STOCK_ITEM, D.DENY),
        (Role.USER, O.STOCK_ADJUST, K.STOCK_ITEM, D.DENY),
        (Role.MANAGER, O.STOCK_ADJUST, K.STOCK_ITEM, D.ALLOW),
        (Role.MANAGER, O.IMPORT_EXECUTE, K.IMPORT_JOB, D.DENY),
        (Role.ORGANIZATION_ADMIN, O.IMPORT_EXECUTE, K.IMPORT_JOB, D.ALLOW),
        (Role.USER, O.MANAGE_MEMBERS, K.ORGANIZATION_MEMBERSHIP, D.DENY),
        (Role.ORGANIZATION_ADMIN, O.MANAGE_MEMBERS, K.ORGANIZATION_MEMBERSHIP, D.ALLOW),
        (Role.USER, O.ORDER_READ_OWN, K.ORDER, D.ALLOW),
        (Role.READ_ONLY, O.ORDER_CANCEL_OWN, K.ORDER, D.DENY),
        (Role.USER, O.ORDER_CANCEL_OWN, K.ORDER, D.ALLOW),
        (Role.USER, O.ORDER_READ_ORGANIZATION, K.ORDER, D.DENY),
        (Role.MANAGER, O.ORDER_READ_ORGANIZATION, K.ORDER, D.ALLOW),
        (Role.MANAGER, O.ORDER_CANCEL_ORGANIZATION, K.ORDER, D.ALLOW),
    ],
)
def test_minimal_business_permission_matrix(env, role, operation, kind, expected):
    context = set_role(env, role)
    ref = ORDER if kind is K.ORDER else ResourceReference(kind, "resource")
    if kind is not K.ORDER:
        env[4].records[("org-a", ref)] = (OrganizationResource(ref, "org-a", "v1"),)
    assert decide(env, operation, ref, context) is expected


@pytest.mark.parametrize("role", list(Role))
@pytest.mark.parametrize(
    "operation",
    [
        O.ORDER_CREATE,
        O.RAG_READ,
        O.CONVERSATION_READ,
        O.CHECKPOINT_READ,
        O.IDEMPOTENCY_REPLAY,
    ],
)
def test_reserved_operations_never_granted_by_any_role(env, role, operation):
    context = set_role(env, role)
    assert decide(env, operation, ORDER, context) is D.DENY
    assert not env[4].calls


@pytest.mark.parametrize("kind", [k for k in K if k is not K.CATALOGUE_ITEM])
def test_catalogue_permission_never_becomes_other_resource_permission(env, kind):
    assert (
        decide(env, O.CATALOGUE_READ, ResourceReference(kind, "predictable")) is D.DENY
    )


@pytest.mark.parametrize("operation", [None, "catalogue_read", "platform_admin", "*"])
def test_unknown_untyped_operation_denied(env, operation):
    assert decide(env, operation) is D.DENY


@pytest.mark.parametrize(
    "row",
    [
        (),
        (OrganizationResource(CATALOGUE, "org-b", "v1"),),
    ],
)
def test_foreign_and_missing_resource_indistinguishable(env, row):
    env[4].records[("org-a", CATALOGUE)] = row
    assert decide(env) is D.NOT_FOUND


@pytest.mark.parametrize(
    "cause",
    ["duplicate", "wrong_id", "wrong_kind", "generic_base", "malformed", "not_tuple"],
)
def test_ambiguous_or_inconsistent_store_never_allows(env, cause):
    original = env[4].records[("org-a", CATALOGUE)][0]
    rows = {
        "duplicate": (original, original),
        "wrong_id": (
            replace(original, reference=ResourceReference(K.CATALOGUE_ITEM, "other")),
        ),
        "wrong_kind": (
            OrganizationResource(
                ResourceReference(K.STOCK_ITEM, "other"), "org-a", "v1"
            ),
        ),
        "generic_base": (ResourceOwnership(CATALOGUE, "org-a", "v1"),),
        "malformed": ({"organization_id": "org-a"},),
        "not_tuple": [original],
    }
    env[4].records[("org-a", CATALOGUE)] = rows[cause]
    assert decide(env) is D.INDETERMINATE


@pytest.mark.parametrize(
    "operation",
    [
        O.ORDER_READ_OWN,
        O.ORDER_CANCEL_OWN,
        O.ORDER_READ_ORGANIZATION,
        O.ORDER_CANCEL_ORGANIZATION,
    ],
)
def test_foreign_order_hidden_even_for_admin(env, operation):
    context = set_role(env, Role.ORGANIZATION_ADMIN)
    env[4].records[("org-a", ORDER)] = (
        OrderResource(ORDER, "org-b", "v2", "human-a", "buyer-legacy-9"),
    )
    assert decide(env, operation, ORDER, context) is D.NOT_FOUND
    assert not env[5].calls


@pytest.mark.parametrize(
    "owner,customer",
    [
        (None, "buyer-legacy-9"),
        ("human-a", None),
        (None, None),
        ("other", "buyer-legacy-9"),
    ],
)
def test_own_permission_requires_real_owner_and_customer(env, owner, customer):
    env[4].records[("org-a", ORDER)] = (
        OrderResource(ORDER, "org-a", "v2", owner, customer),
    )
    assert decide(env, O.ORDER_READ_OWN, ORDER) is D.NOT_FOUND


def test_org_action_uses_persisted_owner_mapping_not_requesting_admin(env):
    context = set_role(env, Role.ORGANIZATION_ADMIN)
    env[4].records[("org-a", ORDER)] = (
        OrderResource(ORDER, "org-a", "v2", "human-b", "buyer-b"),
    )
    env[5].records[("org-a", "human-b", "buyer-b")] = (
        BuyerAssociation("grant-b", "org-a", "human-b", "buyer-b", BS.ACTIVE, "g1"),
    )
    assert decide(env, O.ORDER_READ_ORGANIZATION, ORDER, context) is D.ALLOW
    assert env[5].calls == [("org-a", "human-b", "buyer-b")]
    assert decide(env, O.ORDER_READ_OWN, ORDER, context) is D.NOT_FOUND


@pytest.mark.parametrize(
    "cause",
    [
        "absent",
        "disabled",
        "duplicate",
        "foreign_org",
        "foreign_user",
        "foreign_buyer",
        "malformed",
    ],
)
def test_buyer_associations_are_explicit_unambiguous_and_revocable(env, cause):
    key = ("org-a", "human-a", "buyer-legacy-9")
    original = env[5].records[key][0]
    rows = {
        "absent": (),
        "disabled": (replace(original, status=BS.DISABLED),),
        "duplicate": (original, original),
        "foreign_org": (replace(original, organization_id="org-b"),),
        "foreign_user": (replace(original, user_id="human-b"),),
        "foreign_buyer": (replace(original, customer_id="org-a"),),
        "malformed": ({},),
    }
    env[5].records[key] = rows[cause]
    expected = D.NOT_FOUND if cause in {"absent", "disabled"} else D.INDETERMINATE
    assert decide(env, O.ORDER_READ_OWN, ORDER) is expected


@pytest.mark.parametrize("customer", ["human-a", "org-a"])
def test_equal_identifiers_never_create_buyer_mapping(env, customer):
    env[4].records[("org-a", ORDER)] = (
        OrderResource(ORDER, "org-a", "v2", "human-a", customer),
    )
    assert decide(env, O.ORDER_READ_OWN, ORDER) is D.NOT_FOUND


def test_owner_changes_and_mapping_revocation_visible_between_operations(env):
    assert decide(env, O.ORDER_CANCEL_OWN, ORDER) is D.ALLOW
    original = env[4].records[("org-a", ORDER)][0]
    env[4].records[("org-a", ORDER)] = (
        replace(original, owner_user_id="human-b", revision="v2"),
    )
    assert decide(env, O.ORDER_CANCEL_OWN, ORDER) is D.NOT_FOUND
    env[4].records[("org-a", ORDER)] = (original,)
    key = ("org-a", "human-a", "buyer-legacy-9")
    env[5].records[key] = (
        replace(env[5].records[key][0], status=BS.DISABLED, revision="g2"),
    )
    assert decide(env, O.ORDER_CANCEL_OWN, ORDER) is D.NOT_FOUND


@pytest.mark.parametrize(
    "component",
    [
        "identity",
        "ownership",
        "buyers",
        "missing_source",
        "missing_ownership",
        "missing_buyers",
    ],
)
def test_technical_errors_indeterminate_never_allow(env, component):
    service = env[0]
    if component == "identity":
        env[2].failure = True
    elif component == "ownership":
        env[4].failure = True
    elif component == "buyers":
        env[5].failure = True
    elif component == "missing_source":
        service = replace(service, contexts=None)
    elif component == "missing_ownership":
        service = replace(service, ownership=None)
    elif component == "missing_buyers":
        service = replace(service, buyers=None)
    assert service.decide(REQUEST, env[1], O.ORDER_READ_OWN, ORDER) is D.INDETERMINATE


def test_context_resolution_technical_failure_not_hidden_as_unknown(env):
    env[2].failure = True
    with pytest.raises(TenantContextUnavailable):
        env[0].resolve_context(REQUEST)


def test_predictable_foreign_url_identifier_uses_scoped_lookup(env):
    ref = ResourceReference(K.ORDER, "ORDER-0002")
    env[4].records[("org-b", ref)] = (
        OrderResource(ref, "org-b", "v1", "human-b", "buyer-b"),
    )
    assert decide(env, O.ORDER_READ_OWN, ref) is D.NOT_FOUND
    assert env[4].calls == [("org-a", ref)]


def test_operation_namespace_is_composite_not_concatenated_or_global():
    a = TenantOperationKey("org-a", "same-operation")
    b = TenantOperationKey("org-b", "same-operation")
    assert a != b and len({a, b}) == 2
    assert TenantOperationKey("a|b", "c") != TenantOperationKey("a", "b|c")


def test_tenant_contracts_are_immutable(env):
    with pytest.raises(FrozenInstanceError):
        env[1].authorization_context_id = "forged"
    with pytest.raises(FrozenInstanceError):
        env[1].principal.organization_id = "org-b"
    with pytest.raises(FrozenInstanceError):
        env[4].records[("org-a", ORDER)][0].organization_id = "org-b"


@pytest.mark.parametrize("kind", [K.CONVERSATION, K.CHECKPOINT])
def test_conversation_contract_keeps_thread_user_and_customer_distinct(kind):
    row = ConversationResource(
        ResourceReference(kind, "opaque-key"),
        "org-a",
        "v1",
        "human-a",
        "buyer-9",
        "client-thread",
    )
    assert row.owner_user_id != row.customer_id != row.thread_id != row.organization_id


def test_operation_ownership_preserves_exact_target():
    row = OperationResource(
        ResourceReference(K.IDEMPOTENT_OPERATION, "op-9"),
        "org-a",
        "v1",
        O.ORDER_CANCEL_OWN,
        ORDER,
        "human-a",
        "buyer-9",
    )
    assert row.target == ORDER and row.reference.resource_id == "op-9"


@pytest.mark.parametrize(
    "factory",
    [
        lambda: TenantContext(None, "session"),
        lambda: ResourceReference("order", "id"),
        lambda: OrganizationResource(CATALOGUE, None, "v1"),
        lambda: OrganizationResource(CATALOGUE, "org-a", ""),
        lambda: OrganizationResource(ORDER, "org-a", "v1"),
        lambda: OrderResource(CATALOGUE, "org-a", "v1", "u", "c"),
        lambda: ConversationResource(ORDER, "org-a", "v1", "u", "c", "t"),
        lambda: BuyerAssociation("g", "o", "u", "c", "active", "v1"),
        lambda: TenantOperationKey("", "op"),
    ],
)
def test_incomplete_or_ambiguous_contract_rejected_at_construction(factory):
    with pytest.raises((ValueError, TypeError)):
        factory()
