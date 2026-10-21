"""Pure B4 input and permission contracts; no storage or application startup."""

from dataclasses import FrozenInstanceError, replace

import pytest

from stockpilot.domain.entities.tenant_knowledge import (
    IngestTenantDocument,
    vector,
)
from stockpilot.domain.enums.permission import Permission as P
from stockpilot.domain.enums.role import Role
from stockpilot.domain.services.authorization import ROLE_PERMISSIONS


def valid():
    return IngestTenantDocument("op", "doc", "title", "content", "model", "v1", 0)


@pytest.mark.parametrize(
    "field", ["operation_id", "document_id", "embedding_model", "embedding_version"]
)
@pytest.mark.parametrize("bad", ["", " trailing", "x" * 129, "null\x00"])
def test_identifiers_bounded(field, bad):
    with pytest.raises(ValueError):
        replace(valid(), **{field: bad})


@pytest.mark.parametrize("field", ["title", "content"])
@pytest.mark.parametrize("bad", ["", " ", "bad\x00", "\ud800"])
def test_text_inputs(field, bad):
    with pytest.raises((ValueError, UnicodeError)):
        replace(valid(), **{field: bad})


@pytest.mark.parametrize("bad", [True, -1, 1.5, "1", None])
def test_revision_is_strict(bad):
    with pytest.raises(ValueError):
        replace(valid(), expected_revision=bad)


@pytest.mark.parametrize(
    "bad",
    [
        [],
        [1] * 767,
        [0] * 768,
        [float("nan")] * 768,
        [float("inf")] * 768,
        [True] * 768,
        ["1"] * 768,
        [10**1000] * 768,
        [1e-200] * 768,
    ],
)
def test_embedding_validation(bad):
    with pytest.raises(ValueError):
        vector(bad)


def test_immutable_contract_and_embedding_copy():
    with pytest.raises(FrozenInstanceError):
        valid().title = "x"
    values = [1.0] + [0.0] * 767
    frozen = vector(values)
    values[0] = 99
    assert frozen[0] == 1


def test_explicit_b4_permissions_no_implicit_management_or_sharing():
    for role, permissions in ROLE_PERMISSIONS.items():
        assert {P.RAG_READ, P.CONVERSATION_OWN} <= permissions
        assert (P.RAG_MANAGE in permissions) == (role is Role.ORGANIZATION_ADMIN)
    assert not any(
        p.value in {"conversation:share", "conversation:read_organization"} for p in P
    )
