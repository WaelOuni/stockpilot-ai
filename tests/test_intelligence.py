from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock
import pytest

from stockpilot.application.services.identity_authorization import AuthorizationDenied
from stockpilot.application.services.intelligence import Intelligence
from stockpilot.domain.entities.intelligence import IntelligenceQuery
from stockpilot.domain.entities.principal import Principal
from stockpilot.domain.entities.tenant_authorization import TenantContext
from stockpilot.domain.enums.permission import Permission
from stockpilot.domain.enums.role import Role
from stockpilot.domain.services.authorization import ROLE_PERMISSIONS
from test_demand_forecast import END, series


def setup(role=Role.MANAGER, days=()):
    principal = Principal("user", "https://synthetic.invalid", "subject", "org-a", "membership", role, ROLE_PERMISSIONS[role])
    context = TenantContext(principal, "session")
    repository = Mock()
    product = {"product_id": "a" * 24, "sku": "DEMO-1", "name": "DEMO product", "category": "demo", "price_minor": "1999", "currency": "EUR", "status": "active", "quantity": 50, "reserved": 10, "available": 40}
    repository.snapshot.return_value = {"products": [product], "days": {product["product_id"]: days}, "profiles": {}, "summary": {}, "total": 1}
    authorization = SimpleNamespace(resolve_context=Mock(return_value=context))
    service = Intelligence(repository, authorization, clock=lambda: datetime.combine(END, datetime.min.time(), UTC))
    return service, repository, context, authorization


@pytest.mark.parametrize("role", list(Role))
def test_only_manager_and_organization_admin_read(role):
    service, repository, context, _ = setup(role)
    if role in (Role.MANAGER, Role.ORGANIZATION_ADMIN):
        result = service.dashboard(object(), context, IntelligenceQuery())
        assert result["automatic_actions"] is False
        assert result["items"][0]["forecast"]["status"] == "no_commercial_history"
        assert result["items"][0]["period_sales_units"] is None
    else:
        with pytest.raises(AuthorizationDenied): service.dashboard(object(), context, IntelligenceQuery())
        repository.snapshot.assert_not_called()


def test_injected_permission_does_not_promote_user_role():
    service, repository, context, authorization = setup(Role.USER)
    context = replace(context, principal=replace(context.principal, permissions=context.principal.permissions | {Permission.ANALYTICS_READ}))
    authorization.resolve_context.return_value = context
    with pytest.raises(AuthorizationDenied): service.dashboard(object(), context, IntelligenceQuery())
    repository.snapshot.assert_not_called()


def test_revocation_while_reading_discards_result():
    service, _, context, authorization = setup()
    authorization.resolve_context.side_effect = [context, replace(context, authorization_context_id="rotated")]
    with pytest.raises(AuthorizationDenied): service.dashboard(object(), context, IntelligenceQuery())


def test_sales_cost_supply_freshness_and_page_scope():
    service, repository, context, _ = setup(days=series([2] * 365))
    profile = {"currency": "EUR", "unit_cost_minor": 1800, "supplier": "DEMO supplier", "lead_time_days": 10, "minimum_order_quantity": 50, "safety_stock": 5, "incoming_confirmed": 0, "incoming_as_of": END.isoformat(), "source_references": ["DEMO-profile"]}
    repository.snapshot.return_value["profiles"]["a" * 24] = profile
    response = service.dashboard(object(), context, IntelligenceQuery())
    item = response["items"][0]
    assert item["period_sales_units"] == 180 and item["mean_daily_sales"] == 2
    assert item["stock_cost_minor"] == "90000" and item["replenishment"]["suggested_quantity"] == 50
    assert item["promotion_scenarios"][2]["unit_margin_minor"] == "-101"
    assert response["page_summary"]["products"] == 1 and len(response["page_series"]) == 90
    assert item["dataset"] == "DEMO_SYNTHETIC" and item["available"] == "40"
    profile["incoming_as_of"] = (END - timedelta(days=1)).isoformat()
    assert service.dashboard(object(), context, IntelligenceQuery())["items"][0]["replenishment"]["suggested_quantity"] is None
    profile["currency"] = "USD"
    assert service.dashboard(object(), context, IntelligenceQuery())["items"][0]["stock_cost_minor"] is None


def test_incomplete_or_censored_history_cannot_claim_dormancy():
    for days in (series([0] * 30), tuple(replace(day, stockout=True) for day in series([0] * 90)), tuple(replace(day, complete=False) for day in series([0] * 90))):
        service, _, context, _ = setup(days=days)
        assert service.dashboard(object(), context, IntelligenceQuery())["items"][0]["dormant"] is False
    service, _, context, _ = setup(days=series([0] * 90))
    assert service.dashboard(object(), context, IntelligenceQuery())["items"][0]["dormant"] is True


def test_calendar_gaps_keep_missing_coverage_instead_of_filling_sales():
    days = series([2] * 90)
    service, _, context, _ = setup(days=days[:30] + days[31:])
    result = service.dashboard(object(), context, IntelligenceQuery())
    assert len(result["page_series"]) == 90
    assert result["page_series"][30]["datasets"][0]["complete_products"] == 0
    assert result["page_series"][30]["datasets"][0]["sold"] is None
    assert result["items"][0]["period_sales_units"] is None


@pytest.mark.parametrize("filters", [{"limit": 21}, {"horizon_days": 31}, {"offset": True}, {"discount_bps": -1}, {"category": {"$ne": ""}}, {"period_days": 31}])
def test_query_rejects_unbounded_or_ambiguous_filters(filters):
    with pytest.raises((ValueError, TypeError)): IntelligenceQuery(**filters)
