"""Small, immutable role matrix; no platform-wide administrator role."""

from types import MappingProxyType

from stockpilot.domain.enums.permission import Permission as P
from stockpilot.domain.enums.role import Role

_READ = frozenset(
    {P.CATALOGUE_READ, P.STOCK_READ, P.ORDER_READ_OWN, P.RAG_READ, P.CONVERSATION_OWN}
)
_USER = _READ | {P.ORDER_CREATE, P.ORDER_CANCEL_OWN}
_MANAGER = _USER | {
    P.STOCK_ADJUST,
    P.ORDER_READ_ORGANIZATION,
    P.ORDER_CANCEL_ORGANIZATION,
}
ROLE_PERMISSIONS = MappingProxyType(
    {
        Role.READ_ONLY: _READ,
        Role.USER: _USER,
        Role.MANAGER: _MANAGER,
        Role.ORGANIZATION_ADMIN: _MANAGER
        | {P.IMPORT_EXECUTE, P.ORGANIZATION_MANAGE_MEMBERS, P.RAG_MANAGE},
    }
)
OWN_PERMISSIONS = frozenset({P.ORDER_READ_OWN, P.ORDER_CANCEL_OWN})
