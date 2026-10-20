from dataclasses import dataclass
from stockpilot.domain.enums.permission import Permission
from stockpilot.domain.enums.role import Role

@dataclass(frozen=True)
class Principal:
    user_id: str
    issuer: str
    subject: str
    organization_id: str
    membership_id: str
    role: Role
    permissions: frozenset[Permission]
    def __post_init__(self):
        if not all(value.strip() for value in (self.user_id,self.issuer,self.subject,self.organization_id,self.membership_id)): raise ValueError("principal context is required")
    def can(self, permission: Permission) -> bool: return permission in self.permissions
    def owns(self, organization_id: str) -> bool: return self.organization_id == organization_id
