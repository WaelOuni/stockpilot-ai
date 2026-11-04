import pytest
from pydantic import ValidationError
from stockpilot.domain.entities.tenant_order_archive import OrderListQuery, SetOrderArchive
from stockpilot.presentation.api.routes.b2b_business import ArchiveOrderIn, OrderFilters


@pytest.mark.parametrize("kwargs", [
    {"limit": 0}, {"limit": 101}, {"limit": True}, {"offset": -1}, {"offset": 10001},
    {"offset": "2"}, {"status": "deleted"}, {"status": {"$ne": None}}, {"archived": 1},
])
def test_strict_internal_filters(kwargs):
    with pytest.raises(ValueError):
        OrderListQuery(**kwargs)


@pytest.mark.parametrize("confirmed", [False, None, 1, "true"])
def test_archive_requires_explicit_confirmation(confirmed):
    with pytest.raises(ValueError):
        SetOrderArchive("op", "order", True, confirmed)
    with pytest.raises(ValidationError):
        ArchiveOrderIn(operation_id="op", confirmed=confirmed)


@pytest.mark.parametrize("extra", [{"organization_id": "other"}, {"user_id": "other"}, {"created_by": "other"}])
def test_cannot_choose_archive_owner(extra):
    with pytest.raises(ValidationError):
        OrderFilters(**extra)
    with pytest.raises(ValidationError):
        ArchiveOrderIn(operation_id="op", confirmed=True, **extra)
