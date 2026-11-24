"""Regression oracles for the independent Intelligence review."""
from dataclasses import replace
from fractions import Fraction

import pytest

from stockpilot.domain.entities.intelligence import IntelligenceQuery
from stockpilot.domain.services import demand_forecast as demand
from test_demand_forecast import END, series
from test_intelligence import setup


PROFILE = {"lead_time_days": 7, "safety_stock": 0,
           "minimum_order_quantity": 20, "incoming_confirmed": 0}


@pytest.mark.parametrize("quantity,available,incoming,minimum,expected", [
    (Fraction(29), 29, 0, 20, 0),
    (Fraction(29, 1) + Fraction(1, 10**18), 29, 0, 20, 20),
    (Fraction(29, 1) - Fraction(1, 10**18), 29, 0, 20, 0),
    (Fraction(1, 10**18), 0, 0, 20, 20),
    (Fraction(29), 1, 0, 20, 28),
    (Fraction(29), 1, 28, 20, 0),
    (Fraction(7_000_000_000), 0, 0, 20, 7_000_000_000),
    (Fraction(7_000_000_000), 2**63 - 1, 0, 20, 0),
])
def test_exact_need_boundaries(quantity, available, incoming, minimum, expected):
    prediction = {"status": "estimated", "horizon_days": 7, "quantity": quantity}
    actual = demand.replenish(available, prediction, {
        **PROFILE, "incoming_confirmed": incoming, "minimum_order_quantity": minimum})
    assert actual["suggested_quantity"] == expected


def test_presentation_rounding_must_not_erase_a_small_positive_forecast(monkeypatch):
    # Isolate the calculation/presentation boundary independently of model selection.
    monkeypatch.setattr(demand, "predict", lambda values, horizon, method: [Fraction(1, 10**9)] * horizon)
    prediction = demand.forecast(series([1] * 120), as_of=END, horizon=7)
    assert prediction["quantity"] == Fraction(7, 10**9)
    assert demand.replenish(0, prediction, PROFILE)["suggested_quantity"] == 20


@pytest.mark.parametrize("values,method,expected", [
    ([1, 2], "mean28", Fraction(3, 2)),
    ([0, 1], "weighted28", Fraction(2, 3)),
    ([1, 0, 0], "ses02", Fraction(16, 25)),
    ([0, 4], "croston_sba", Fraction(19, 10)),
])
def test_predictors_retain_exact_rational_demand(values, method, expected):
    assert demand.predict(values, 7, method) == [expected] * 7


def test_lead_safety_and_confirmed_receipts_are_preserved():
    prediction = {"status": "estimated", "horizon_days": 7, "quantity": Fraction(29)}
    actual = demand.replenish(29, prediction, {**PROFILE, "lead_time_days": 14,
                                             "safety_stock": 3, "incoming_confirmed": 5})
    assert actual["reorder_point"] == 61
    assert actual["suggested_quantity"] == 27


@pytest.mark.parametrize("horizon", [7, 30, 60, 90])
@pytest.mark.parametrize("lead", [0, 90])
def test_supply_horizons_and_maximum_daily_demand(horizon, lead):
    prediction = demand.forecast(series([10**9] * 365), as_of=END, horizon=horizon)
    actual = demand.replenish(0, prediction, {**PROFILE, "lead_time_days": lead,
                                            "safety_stock": 10**9, "incoming_confirmed": 10**9})
    assert actual["reorder_point"] == (lead + 1) * 10**9
    assert actual["suggested_quantity"] == max(lead, horizon) * 10**9


@pytest.mark.parametrize("field,value", [("lead_time_days", 91), ("safety_stock", -1),
    ("minimum_order_quantity", 0), ("minimum_order_quantity", 10**9 + 1),
    ("incoming_confirmed", 10**9 + 1), ("incoming_confirmed", True)])
def test_supply_parameter_bounds_remain_strict(field, value):
    with pytest.raises(ValueError):
        demand.replenish(0, {"status": "estimated", "horizon_days": 7, "quantity": Fraction(29)}, {**PROFILE, field: value})


def test_serialized_rounded_forecast_cannot_be_reused_as_a_calculation_input():
    with pytest.raises(ValueError, match="Unrounded exact forecast"):
        demand.replenish(29, {"status": "estimated", "horizon_days": 7, "quantity": 29.0}, PROFILE)


@pytest.mark.parametrize("values", [[], [2] * 14])
def test_unavailable_demand_does_not_invent_replenishment(values):
    prediction = demand.forecast(series(values), as_of=END, horizon=7)
    assert demand.replenish(0, prediction, PROFILE)["suggested_quantity"] is None


def test_backfilled_history_is_explicitly_not_a_vintage_backtest():
    original = series([2] * 365)
    corrected = list(original)
    corrected[-91] = replace(corrected[-91], sold=900_000_000)
    before = demand.forecast(original, as_of=END, horizon=30)
    after = demand.forecast(tuple(corrected), as_of=END, horizon=30)
    assert before["backtest"]["mae_units"] != after["backtest"]["mae_units"]
    for result in (before, after):
        assert result["backtest"]["basis"] == "retrospective_final_snapshot"
        assert result["backtest"]["availability_verified"] is False


def test_mixed_product_history_keeps_daily_provenance_and_calendar_gaps():
    days = list(series([2] * 90))
    days[20] = replace(days[20], dataset="observed", sold=7)
    days.pop(30)
    service, _, context, _ = setup(days=tuple(days))
    response = service.dashboard(object(), context, IntelligenceQuery())
    item = response["items"][0]
    assert item["dataset"] == "mixed"
    assert item["forecast"]["status"] == "mixed_dataset"
    assert item["period_sales_units"] is None
    rows = response["page_series"]
    actual = {row["dataset"]: row for row in rows[20]["datasets"]}
    assert actual["observed"]["sold"] == 7
    assert actual["DEMO_SYNTHETIC"]["sold"] is None
    assert all(row["sold"] is None for row in rows[30]["datasets"])


def test_multiple_products_never_combine_observed_and_synthetic_sales():
    service, repository, context, _ = setup(days=series([2] * 90))
    raw = repository.snapshot.return_value
    second = {**raw["products"][0], "product_id": "b" * 24}
    third = {**raw["products"][0], "product_id": "c" * 24}
    raw["products"].extend([second, third])
    raw["days"][second["product_id"]] = tuple(replace(day, dataset="observed", sold=3, reserved=10, adjusted=-5) for day in series([3] * 90))
    raw["days"][third["product_id"]] = tuple(replace(day, dataset="observed", sold=4) for day in series([4] * 90))
    response = service.dashboard(object(), context, IntelligenceQuery())
    for day in response["page_series"]:
        buckets = {row["dataset"]: row for row in day["datasets"]}
        assert buckets["observed"]["sold"] == 7
        assert buckets["observed"]["reserved"] == 10
        assert buckets["observed"]["adjusted"] == -5
        assert buckets["observed"]["complete_products"] == 2
        assert buckets["observed"]["expected_products"] == 2
        assert buckets["DEMO_SYNTHETIC"]["sold"] == 2


@pytest.mark.parametrize("dataset", ["observed", "DEMO_SYNTHETIC"])
def test_single_provenance_and_incomplete_days(dataset):
    days = tuple(replace(day, dataset=dataset, complete=i != 0) for i, day in enumerate(series([0] * 90)))
    service, _, context, _ = setup(days=days)
    rows = service.dashboard(object(), context, IntelligenceQuery())["page_series"]
    assert rows[0]["datasets"][0]["sold"] is None
    assert rows[0]["datasets"][0]["complete_products"] == 0
    assert rows[1]["datasets"][0]["sold"] == 0
    assert all(len(row["datasets"]) == 1 and row["datasets"][0]["dataset"] == dataset for row in rows)


def test_no_history_has_no_synthetic_zero_series():
    service, _, context, _ = setup()
    assert service.dashboard(object(), context, IntelligenceQuery())["page_series"] == []
