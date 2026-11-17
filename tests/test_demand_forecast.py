from dataclasses import replace
from datetime import date, timedelta
import pytest
from stockpilot.domain.services.demand_forecast import DemandDay, forecast, replenish, promotion

END = date(2026, 11, 17)


def series(values):
    return tuple(DemandDay(END - timedelta(days=len(values) - i), value, source_references=(f"DEMO-{i}",), dataset="DEMO_SYNTHETIC") for i, value in enumerate(values))


@pytest.mark.parametrize("horizon", [7, 30, 60, 90])
def test_constant_demand_exact_forecast_and_separate_holdout(horizon):
    result = forecast(series([3] * 365), as_of=END, horizon=horizon)
    assert result["status"] == "estimated" and result["quantity"] == 3 * horizon
    assert result["backtest"]["mae_units"] == result["backtest"]["naive_mae_units"] == 0
    assert result["backtest"]["assessment_windows"] >= 1
    assert result["backtest"]["assessment_end"] < END.isoformat()


@pytest.mark.parametrize("kind", ["gap", "incomplete", "stockout", "duplicate", "mixed", "stale"])
def test_unsafe_history_never_produces_a_numeric_forecast(kind):
    days = list(series([4] * 120))
    if kind == "gap": days.pop(30)
    if kind == "incomplete": days[30] = replace(days[30], complete=False)
    if kind == "stockout": days[30] = replace(days[30], stockout=True)
    if kind == "duplicate": days[31] = days[30]
    if kind == "mixed": days[30] = replace(days[30], dataset="observed")
    if kind == "stale": days.pop()
    result = forecast(tuple(days), as_of=END, horizon=30)
    assert result["quantity"] is None and result["daily"] == []


def test_cancelled_returned_reserved_receipts_and_adjustments_do_not_become_sales():
    days = tuple(replace(day, sold=0, reserved=100, cancelled=30, returned=20, received=200, adjusted=-50) for day in series([0] * 120))
    assert forecast(days, as_of=END, horizon=30)["quantity"] == 0


def test_future_observation_is_excluded_without_leakage():
    days = series([2] * 120)
    future = DemandDay(END + timedelta(days=1), 1_000_000, source_references=("DEMO-future",))
    assert forecast(days + (future,), as_of=END, horizon=30) == forecast(days, as_of=END, horizon=30)


@pytest.mark.parametrize("values", [[0] * 15, [4] * 60, []])
def test_insufficient_history_no_claimed_precision(values):
    result = forecast(series(values), as_of=END, horizon=30)
    assert result["quantity"] is None and result["backtest"] is None


@pytest.mark.parametrize("values", [list(range(365)), [8 if i % 10 == 0 else 0 for i in range(365)], [1, 1, 2, 2, 4, 20, 30] * 52])
def test_trend_intermittent_and_weekly_demand_have_measured_temporal_errors(values):
    result = forecast(series(values), as_of=END, horizon=7)
    assert result["status"] == "estimated"
    assert result["quantity"] >= 0
    assert result["backtest"]["selection_windows"] > 0
    assert result["backtest"]["assessment_windows"] >= 8
    assert result["uncertainty"]["probability"] is None


def test_replenishment_requires_known_incoming_and_respects_supplier_minimum():
    result = forecast(series([2] * 120), as_of=END, horizon=30)
    assert replenish(40, result, None)["suggested_quantity"] is None
    profile = {"lead_time_days": 10, "safety_stock": 5, "minimum_order_quantity": 50, "incoming_confirmed": 0}
    assert replenish(40, result, profile) == {"status": "recommendation", "suggested_quantity": 50, "coverage_days": 20.0, "reorder_point": 25, "lead_time_days": 10, "safety_stock": 5, "minimum_order_quantity": 50, "incoming_quantity": 0}
    assert replenish(40, result, {**profile, "incoming_confirmed": 100})["suggested_quantity"] == 0


def test_promotions_are_exact_scenarios_without_demand_elasticity():
    result = promotion(1999, 1800, 1500)
    assert result["price_minor"] == "1699" and result["unit_margin_minor"] == "-101"
    assert result["demand_uplift"] is None and result["profitability"] == "not_estimated"
    assert promotion(1999, None, 500)["unit_margin_minor"] is None
    with pytest.raises(ValueError): promotion(1999, 1200, True)
