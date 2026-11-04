"""Contact use cases use only the dedicated repository and current identity."""
from stockpilot.application.ports.tenant_contact import TenantContactPort
from stockpilot.application.services.identity_authorization import AuthorizationDenied
from stockpilot.domain.enums.permission import Permission as P


class TenantContact:
    def __init__(self, repository: TenantContactPort, authorization):
        self.repository, self.authorization = repository, authorization

    def _call(self, request, context, permissions, method, *args):
        def check():
            current = self.authorization.resolve_context(request)
            if current != context or not any(p in current.principal.permissions for p in permissions):
                raise AuthorizationDenied("Current contact permission required.")
        check()
        result = getattr(self.repository, method)(request, context, *args)
        check()
        return result

    def create(self, request, context, command):
        return self._call(request, context, (P.CONTACT_CREATE,), "create", command)

    def list(self, request, context, query):
        permission = P.CONTACT_READ_ORGANIZATION if query.scope == "organization" else P.CONTACT_READ_OWN
        return self._call(request, context, (permission,), "list", query)

    def get(self, request, context, request_id):
        return self._call(request, context, (P.CONTACT_READ_OWN, P.CONTACT_READ_ORGANIZATION), "get", request_id)

    def replies(self, request, context, request_id, limit, offset):
        return self._call(request, context, (P.CONTACT_READ_OWN, P.CONTACT_READ_ORGANIZATION), "replies", request_id, limit, offset)

    def reply(self, request, context, command):
        return self._call(request, context, (P.CONTACT_REPLY_ORGANIZATION,), "reply", command)

    def set_status(self, request, context, command):
        return self._call(request, context, (P.CONTACT_MANAGE_ORGANIZATION,), "set_status", command)
