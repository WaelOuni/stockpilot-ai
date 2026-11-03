"""Contact contracts/permissions; no provider, server or database."""
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from pydantic import ValidationError
from stockpilot.domain.entities.tenant_contact import CreateContact, ReplyContact, SetContactStatus, ContactQuery
from stockpilot.domain.enums.permission import Permission as P
from stockpilot.domain.enums.role import Role
from stockpilot.domain.services.authorization import ROLE_PERMISSIONS, OWN_PERMISSIONS
from stockpilot.application.services.tenant_contact import TenantContact
from stockpilot.application.services.identity_authorization import AuthorizationDenied
from stockpilot.application.ports.tenant_contact import ContactRateLimited
from stockpilot.presentation.api.b2b_boundary import failure
from stockpilot.presentation.api.routes.b2b_contact import CreateIn, ReplyIn, StatusIn, Filters


@pytest.mark.parametrize("role", list(Role))
def test_dedicated_permissions_do_not_make_manager_a_contact_administrator(role):
    actual = {p for p in ROLE_PERMISSIONS[role] if str(p).startswith("contact:")}
    own = {P.CONTACT_CREATE, P.CONTACT_READ_OWN}
    admin = {P.CONTACT_READ_ORGANIZATION, P.CONTACT_REPLY_ORGANIZATION, P.CONTACT_MANAGE_ORGANIZATION}
    assert actual == (own | admin if role == Role.ORGANIZATION_ADMIN else own)
    assert P.CONTACT_READ_OWN in OWN_PERMISSIONS


@pytest.mark.parametrize("changes", [
    {"subject": ""}, {"subject": " "}, {"subject": "x" * 121},
    {"description": "x" * 4001}, {"description": ""}, {"subject": "a\nb"},
    {"description": "\x00secret"}, {"category": "admin"}, {"operation_id": ""},
])
def test_invalid_contact_inputs(changes):
    with pytest.raises(ValueError):
        CreateContact(**{"operation_id": "op", "subject": "Question", "category": "other", "description": "Description", **changes})


@pytest.mark.parametrize("model,body", [
    (CreateIn, {"operation_id": "op", "subject": "Hello", "category": "other", "description": "Body"}),
    (ReplyIn, {"operation_id": "op", "body": "Body"}),
    (StatusIn, {"operation_id": "op", "status": "resolved"}),
    (Filters, {}),
])
@pytest.mark.parametrize("field", ["organization_id", "created_by", "user_id", "permissions", "attachment"])
def test_no_identity_authority_or_attachment_from_body(model, body, field):
    with pytest.raises(ValidationError):
        model(**body, **{field: "forged"})


@pytest.mark.parametrize("method,args,permission", [
    ("create", (CreateContact("op", "Question", "other", "Body"),), P.CONTACT_CREATE),
    ("list", (ContactQuery(scope="organization"),), P.CONTACT_READ_ORGANIZATION),
    ("reply", (ReplyContact("op", "request", "Body"),), P.CONTACT_REPLY_ORGANIZATION),
    ("set_status", (SetContactStatus("op", "request", "resolved"),), P.CONTACT_MANAGE_ORGANIZATION),
])
def test_use_case_denies_before_persistence(method, args, permission):
    ctx = SimpleNamespace(principal=SimpleNamespace(permissions=frozenset()))
    repo, auth = Mock(), SimpleNamespace(resolve_context=lambda request: ctx)
    service = TenantContact(repo, auth)
    with pytest.raises(AuthorizationDenied):
        getattr(service, method)(object(), ctx, *args)
    assert repo.mock_calls == []
    ctx.principal.permissions = frozenset({permission})
    getattr(service, method)(object(), ctx, *args)
    getattr(repo, method).assert_called_once()


@pytest.mark.parametrize("query", [
    {"limit": 101}, {"offset": -1}, {"offset": True}, {"scope": "global"}, {"status": "deleted"},
])
def test_query_is_bounded(query):
    with pytest.raises(ValueError):
        ContactQuery(**query)


def test_rate_error_contains_no_payload_and_has_retry_after():
    response = failure(ContactRateLimited())
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "600"
    assert response.body == b'{"code":"CONTACT_RATE_LIMITED"}'
