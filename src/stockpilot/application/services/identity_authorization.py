"""Inactive B2B services: no HTTP dependency, provisioning or startup hook."""

from dataclasses import dataclass

from stockpilot.application.ports.human_authentication import HumanAuthentication
from stockpilot.application.ports.identity_repository import IdentityRepository
from stockpilot.application.ports.membership_repository import MembershipRepository
from stockpilot.application.ports.organization_repository import OrganizationRepository
from stockpilot.domain.entities.identity import ExternalIdentity
from stockpilot.domain.entities.principal import Principal, ResourceScope
from stockpilot.domain.enums.identity_status import IdentityStatus
from stockpilot.domain.enums.membership_status import MembershipStatus
from stockpilot.domain.enums.organization_status import OrganizationStatus
from stockpilot.domain.enums.permission import Permission
from stockpilot.domain.enums.role import Role
from stockpilot.domain.identity_validation import require_identifier
from stockpilot.domain.services.authorization import OWN_PERMISSIONS, ROLE_PERMISSIONS


class AuthorizationDenied(Exception):
    """Expected business denial; contains no credentials or private records."""


class IdentityResolutionError(AuthorizationDenied):
    """Unknown, inactive, ambiguous or inconsistent identity context."""


@dataclass(frozen=True)
class IdentityAuthorizationService:
    authentication: HumanAuthentication
    identities: IdentityRepository
    organizations: OrganizationRepository
    memberships: MembershipRepository

    def resolve(self, request_context: object, organization_id: str) -> Principal:
        # Only a trusted injected adapter supplies authentication evidence.
        if self.authentication is None:
            raise IdentityResolutionError("Human authentication is not configured.")
        external = self.authentication.authenticate(request_context)
        return self._resolve_key(external, organization_id)

    def _resolve_key(
        self, external: ExternalIdentity | None, organization_id: str
    ) -> Principal:
        if not isinstance(external, ExternalIdentity):
            raise IdentityResolutionError("Authenticated human identity required.")
        try:
            require_identifier(organization_id)
        except ValueError:
            raise IdentityResolutionError("Explicit organization required.") from None
        if any(
            repo is None
            for repo in (self.identities, self.organizations, self.memberships)
        ):
            raise IdentityResolutionError("Identity repositories are not configured.")
        users = self.identities.find_by_external_identity(external)
        if len(users) != 1:
            raise IdentityResolutionError("Identity unknown or ambiguous.")
        user = users[0]
        if (
            user.external_identity != external
            or user.status is not IdentityStatus.ACTIVE
        ):
            raise IdentityResolutionError("Identity inconsistent or disabled.")
        memberships = self.memberships.find_for_user(user.user_id)
        active = {}
        ids = set()
        for membership in memberships:
            if membership.user_id != user.user_id or membership.membership_id in ids:
                raise IdentityResolutionError("Inconsistent membership records.")
            ids.add(membership.membership_id)
            if membership.status is MembershipStatus.ACTIVE:
                if membership.organization_id in active:
                    raise IdentityResolutionError("Duplicate active memberships.")
                active[membership.organization_id] = membership
        membership = active.get(organization_id)
        if (
            membership is None
            or not isinstance(membership.role, Role)
            or membership.role not in ROLE_PERMISSIONS
        ):
            raise IdentityResolutionError("Active supported membership required.")
        organization = self.organizations.get(organization_id)
        if (
            organization is None
            or organization.organization_id != organization_id
            or organization.status is not OrganizationStatus.ACTIVE
        ):
            raise IdentityResolutionError(
                "Organization unknown, inconsistent or inactive."
            )
        return Principal(
            user.user_id,
            external.issuer,
            external.subject,
            organization_id,
            membership.membership_id,
            membership.role,
            ROLE_PERMISSIONS[membership.role],
        )

    def require(
        self,
        request_context: object,
        principal: Principal | None,
        permission: Permission,
        *,
        resource: ResourceScope | None = None,
    ) -> None:
        if (
            not isinstance(principal, Principal)
            or not isinstance(permission, Permission)
            or not isinstance(resource, ResourceScope)
        ):
            raise AuthorizationDenied(
                "Complete human principal, permission and scope required."
            )
        # Recheck authentication, memberships and role permissions. A caller cannot
        # elevate a copied principal or keep access after membership suspension.
        current = self.resolve(request_context, principal.organization_id)
        if current != principal or permission not in current.permissions:
            raise AuthorizationDenied("Principal changed or permission denied.")
        if resource.organization_id != current.organization_id:
            raise AuthorizationDenied("Foreign organization scope.")
        if permission in OWN_PERMISSIONS and resource.owner_user_id != current.user_id:
            raise AuthorizationDenied("Resource owner required.")
