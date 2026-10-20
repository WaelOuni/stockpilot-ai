from datetime import datetime, timezone
import pytest

from stockpilot.application.services.identity_authorization import (
    AuthorizationDenied, IdentityAuthorizationService, ROLE_PERMISSIONS,
)
from stockpilot.domain.entities.identity import ExternalIdentity, User
from stockpilot.domain.entities.membership import Membership
from stockpilot.domain.entities.organization import Organization
from stockpilot.domain.enums.identity_status import IdentityStatus
from stockpilot.domain.enums.membership_status import MembershipStatus
from stockpilot.domain.enums.organization_status import OrganizationStatus
from stockpilot.domain.enums.permission import Permission
from stockpilot.domain.enums.role import Role

NOW = datetime.now(timezone.utc)

class Identities:
    def __init__(self, user): self.user = user
    def find_by_external_identity(self, identity): return self.user if identity == self.user.external_identity else None

class Organizations:
    def __init__(self, org): self.org = org
    def get(self, organization_id): return self.org if self.org and self.org.organization_id == organization_id else None

class Memberships:
    def __init__(self, membership): self.membership = membership
    def find_active_for_user(self, user_id): return [self.membership] if self.membership and self.membership.user_id == user_id else []
    def find_for_user_and_organization(self, user_id, organization_id):
        return self.membership if self.membership and self.membership.user_id == user_id and self.membership.organization_id == organization_id else None

def service(role=Role.USER, user_status=IdentityStatus.ACTIVE, org_status=OrganizationStatus.ACTIVE, membership_status=MembershipStatus.ACTIVE):
    external = ExternalIdentity("https://id.example", "subject-1")
    user = User("user-1", external, user_status, NOW, NOW)
    org = Organization("org-1", "Acme", org_status, NOW, NOW)
    membership = Membership("membership-1", "user-1", "org-1", role, membership_status, NOW, NOW)
    return IdentityAuthorizationService(Identities(user), Organizations(org), Memberships(membership)), external

def test_resolves_active_identity_and_membership():
    resolver, external = service(Role.MANAGER)
    principal = resolver.resolve(external, "org-1")
    assert principal.role is Role.MANAGER
    assert principal.can(Permission.STOCK_ADJUST)
    assert principal.issuer == external.issuer

@pytest.mark.parametrize("status", [IdentityStatus.DISABLED])
def test_disabled_identity_is_denied(status):
    resolver, external = service(user_status=status)
    with pytest.raises(Exception, match="unknown or disabled"):
        resolver.resolve(external, "org-1")

@pytest.mark.parametrize("status", [OrganizationStatus.SUSPENDED, OrganizationStatus.DISABLED])
def test_inactive_organization_is_denied(status):
    resolver, external = service(org_status=status)
    with pytest.raises(Exception, match="unknown or inactive"):
        resolver.resolve(external, "org-1")

def test_inactive_membership_is_denied():
    resolver, external = service(membership_status=MembershipStatus.SUSPENDED)
    with pytest.raises(Exception, match="active organization membership"):
        resolver.resolve(external, "org-1")

def test_foreign_organization_cannot_be_selected():
    resolver, external = service()
    with pytest.raises(Exception, match="active organization membership"):
        resolver.resolve(external, "other-org")

def test_permission_and_resource_scope_are_both_required():
    resolver, external = service(Role.USER)
    principal = resolver.resolve(external, "org-1")
    resolver.require(principal, Permission.ORDER_CREATE, resource_organization_id="org-1")
    with pytest.raises(AuthorizationDenied):
        resolver.require(principal, Permission.ORDER_READ_ORGANIZATION, resource_organization_id="org-1")
    with pytest.raises(AuthorizationDenied):
        resolver.require(principal, Permission.ORDER_CREATE, resource_organization_id="other-org")
    with pytest.raises(AuthorizationDenied):
        resolver.require(None, Permission.ORDER_CREATE, resource_organization_id="org-1")

def test_email_is_not_an_identity_field():
    resolver, external = service()
    assert resolver.resolve(external, "org-1").user_id == "user-1"

def test_role_permission_map_has_no_platform_admin():
    assert Role.ORGANIZATION_ADMIN in ROLE_PERMISSIONS
    assert "PLATFORM_ADMIN" not in {role.value for role in ROLE_PERMISSIONS}
