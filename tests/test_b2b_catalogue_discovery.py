import pytest
from stockpilot.application.services.tenant_catalogue_discovery import (
    is_low_stock_request,
)
from stockpilot.domain.entities.tenant_product import TenantProductQuery


@pytest.mark.parametrize(
    "amount", ["moins de 150 euros", "moins de 150 €", "moins de 150", "maximum 150"]
)
def test_catalogue_availability_does_not_turn_a_budget_into_stock(amount):
    assert not is_low_stock_request("je cherche 2 perceuses disponibles " + amount)


@pytest.mark.parametrize(
    "values",
    [
        {"maximum_available": True},
        {"maximum_available": -1},
        {"maximum_available": "5"},
        {"maximum_available": 2**63},
        {"sort_by_available": 1},
        {"categories": ["perceuses"]},
        {"categories": ({"$ne": None},)},
    ],
)
def test_internal_stock_query_rejects_coercion_and_injected_filters(values):
    with pytest.raises(ValueError):
        TenantProductQuery(**values)
