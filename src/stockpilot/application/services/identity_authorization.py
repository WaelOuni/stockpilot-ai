from dataclasses import dataclass

from stockpilot.application.ports.identity_repository import IdentityRepository
from stockpilot.application.ports.membership_repository import MembershipRepository
from stockpilot.application.ports.organization_repository import OrganizationRepository
from stockpilot.domain.entities.identity import ExternalIdentity
from stockpilot.domain.entities.principal import Principal
from stockpilot.domain.enums.identity_status import IdentityStatus
from stockpilot.domain.enums.membership_status import MembershipStatus
from stockpilot.domain.enums.organization_status import OrganizationStatus
from stockpilot.domain.enums.permission import Permission
from stockpilot.domain.enums.role import Role

ROLE_PERMISSIONS = {
    Role.READ_ONLY: frozenset({Permission.CATALOGUE_READ, Permission.STOCK_READ, Permission.ORDER_READ_OWN}),
    Role.USER: frozenset({Permission.CATALOGUE_READ, Permission.STOCK_READ, Permission.ORDER_CREATE, Permission.ORDER_READ_OWN, Permission.ORDER_CANCEL_OWN}),
    Role.MANAGER: frozenset({Permission.CATALOGUE_READ, Permission.STOCK_READ, Permission.STOCK_ADJUST, Permission.ORDER_CREATE, Permission.ORDER_READ_OWN, Permission.ORDER_READ_ORGANIZATION, Permission.ORDER_CANCEL_OWN, Permission.ORDER_CANCEL_ORGANIZATION, Permission.IMPORT_EXECUTE}),
    Role.ORGANIZATION_ADMIN: frozenset({Permission.CATALOGUE_READ, Permission.STOCK_READ, Permission.STOCK_ADJUST, Permission.ORDER_CREATE, Permission.ORDER_READ_OWN, Permission.ORDER_READ_ORGANIZATION, Permission.ORDER_CANCEL_OWN, Permission.ORDER_CANCEL_ORGANIZATION, Permission.IMPORT_EXECUTE, Permission.ORGANIZATION_MANAGE_MEMBERS}),
}

class IdentityResolutionError(Exception):
    pass

class AuthorizationDenied(Exception):
    pass

@dataclass(frozen=True)
class IdentityAuthorizationService:
    identities: IdentityRepository
    organizations: OrganizationRepository
    memberships: MembershipRepository

    def resolve(self, external: ExternalIdentity, organization_id: str) -> Principal:
        user = self.identities.find_by_external_identity(external)
        if user is None or user.status is not IdentityStatus.ACTIVE:
            raise IdentityResolutionError("identity is unknown or disabled")
        membership = self.memberships.find_for_user_and_organization(user.user_id, organization_id)
        if membership is None or membership.status is not MembershipStatus.ACTIVE:
            raise IdentityResolutionError("active organization membership is required")
        if not isinstance(membership.role, Role) or membership.role not in ROLE_PERMISSIONS:
            raise IdentityResolutionError("membership role is not supported")
        organization = self.organizations.get(organization_id)
        if organization is None or organization.status is not OrganizationStatus.ACTIVE:
            raise IdentityResolutionError("organization is unknown or inactive")
        return Principal(user.user_id, external.issuer, external.subject, organization_id, membership.membership_id, membership.role, ROLE_PERMISSIONS[membership.role])

    def require(self, principal: Principal | None, permission: Permission, *, resource_organization_id: str | None = None) -> None:
        if principal is None or not isinstance(permission, Permission) or not principal.can(permission):
            raise AuthorizationDenied("permission denied")
        if resource_organization_id is None or not principal.owns(resource_organization_id):
            raise AuthorizationDenied("resource is outside the active organization")
