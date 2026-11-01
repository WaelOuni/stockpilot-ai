from collections import Counter
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from scripts.b2b_demo_stock_examples import build_plan, generated_rows
from stockpilot.domain.entities.tenant_product import (
    TenantProduct,
    TenantStock,
    TenantProductStock,
    TenantProductPage,
    TenantStockPage,
)


@pytest.mark.parametrize(
    "profile,total,zero,low", [("northline", 1250, 25, 20), ("atelierpro", 250, 10, 10)]
)
def test_reproducible_new_installation_examples(profile, total, zero, low):
    rows = list(generated_rows(profile))
    assert rows == list(generated_rows(profile)) and len(rows) == total
    assert len({r["sku"] for r in rows}) == total
    counts = Counter(
        "zero" if r["quantity"] == 0 else "low" if r["quantity"] < 5 else "normal"
        for r in rows
    )
    assert counts["zero"] == zero and counts["low"] == low and counts["normal"] > 0


def test_plan_is_read_only_and_never_selects_reserved_stock():
    items = tuple(
        TenantProductStock(
            TenantProduct(
                str(i),
                "org-a",
                f"SKU-{i:03}",
                "DEMO",
                "",
                "perceuses",
                15500,
                "EUR",
                "active",
                (),
                1,
            ),
            TenantStock(str(i), "org-a", 30, 5 if i < 3 else 0, "active", 1),
        )
        for i in range(50)
    )
    catalogue = Mock()
    catalogue.search.return_value = TenantProductPage((), 1250, True)
    catalogue.search_stock.return_value = TenantStockPage(items, 50, False)
    plan = build_plan(
        catalogue, object(), SimpleNamespace(organization_id="org-a"), "northline"
    )
    assert len(plan["adjustments"]) == 45
    assert all(row["reserved_before"] == 0 for row in plan["adjustments"])
    assert sum(row["command"]["quantity"] == 0 for row in plan["adjustments"]) == 25
    assert {call[0] for call in catalogue.mock_calls} == {"search", "search_stock"}
    assert len({row["command"]["operation_id"] for row in plan["adjustments"]}) == 45
