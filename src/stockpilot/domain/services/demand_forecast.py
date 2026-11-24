"""Bounded deterministic daily-demand forecasts; never infer sales from reservations."""

from dataclasses import dataclass
from datetime import date, timedelta
from fractions import Fraction
from math import ceil, sqrt
from statistics import mean


@dataclass(frozen=True)
class DemandDay:
    day: date
    sold: int
    returned: int = 0
    cancelled: int = 0
    reserved: int = 0
    received: int = 0
    adjusted: int = 0
    transferred: int = 0
    complete: bool = True
    stockout: bool = False
    source_references: tuple[str, ...] = ()
    dataset: str = "observed"

    def __post_init__(self):
        if type(self.day) is not date or type(self.complete) is not bool or type(self.stockout) is not bool:
            raise ValueError("Explicit date and quality flags required.")
        for value in (self.sold, self.returned, self.cancelled, self.reserved, self.received, self.transferred):
            if type(value) is not int or not 0 <= value <= 1_000_000_000:
                raise ValueError("Bounded nonnegative observed quantities required.")
        if type(self.adjusted) is not int or not -1_000_000_000 <= self.adjusted <= 1_000_000_000:
            raise ValueError("Bounded administrative adjustment required.")
        if self.dataset not in {"observed", "DEMO_SYNTHETIC"}:
            raise ValueError("Explicit dataset origin required.")
        if not isinstance(self.source_references, tuple) or not 1 <= len(self.source_references) <= 20 or any(
            type(value) is not str or not 1 <= len(value) <= 128 for value in self.source_references
        ):
            raise ValueError("Bounded source references required for every observation.")


def predict(values: list[int], horizon: int, method: str) -> list[Fraction]:
    """Exact demand expectations. Presentation rounding belongs at the API boundary."""
    if not values:
        raise ValueError("Training history required.")
    if method == "naive":
        rate = values[-1]
    elif method == "mean28":
        rate = Fraction(sum(values[-28:]), len(values[-28:]))
    elif method == "weighted28":
        recent = values[-28:]
        rate = Fraction(sum((i + 1) * value for i, value in enumerate(recent)), sum(range(1, len(recent) + 1)))
    elif method == "ses02":
        rate = values[0]
        for value in values[1:]:
            rate = Fraction(value, 5) + Fraction(4, 5) * rate
    elif method == "croston_sba":
        first = next((i for i, value in enumerate(values) if value), None)
        if first is None:
            rate = 0
        else:
            size, interval, elapsed = Fraction(values[first]), Fraction(first + 1), 1
            for value in values[first + 1:]:
                if value:
                    size = Fraction(value, 10) + Fraction(9, 10) * size
                    interval = Fraction(elapsed, 10) + Fraction(9, 10) * interval
                    elapsed = 1
                else:
                    elapsed += 1
            rate = Fraction(19, 20) * size / interval
    elif method == "seasonal_naive7":
        if len(values) < 84:
            raise ValueError("Twelve weekly cycles required.")
        return [Fraction(values[-7 + i % 7]) for i in range(horizon)]
    else:
        raise ValueError("Unknown forecast method.")
    return [Fraction(rate)] * horizon


def forecast(days: tuple[DemandDay, ...], *, as_of: date, horizon: int) -> dict:
    if type(horizon) is not int or horizon not in (7, 30, 60, 90) or type(as_of) is not date or len(days) > 366:
        raise ValueError("Bounded daily forecast requested.")
    # Exclude future event dates. Availability/revision times are NOT recorded:
    # this evaluates the final snapshot, not what was knowable at each origin.
    days = tuple(sorted((day for day in days if day.day < as_of), key=lambda day: day.day))
    result = {"status": "insufficient_history", "horizon_days": horizon,
              "observations": len(days), "method": None, "quantity": None,
              "daily": [], "backtest": None, "uncertainty": None}
    if not days:
        return {**result, "status": "no_commercial_history"}
    if len({day.dataset for day in days}) != 1:
        return {**result, "status": "mixed_dataset"}
    expected = tuple(days[0].day + timedelta(days=i) for i in range(len(days)))
    if tuple(day.day for day in days) != expected or days[-1].day != as_of - timedelta(days=1) or not all(day.complete for day in days):
        return {**result, "status": "incomplete_history"}
    if any(day.stockout for day in days):
        return {**result, "status": "stockout_censored"}
    values = [day.sold for day in days]
    # Non-overlapping horizon-sized temporal holdouts; selection and assessment
    # use separate periods. Three holdouts is the minimum, never shuffled CV.
    minimum_training = 84 if len(values) >= 84 + 3 * horizon else 28
    origins = list(range(len(values) - horizon, minimum_training - 1, -horizon))[::-1]
    if len(origins) < 3:
        return result
    selection_count = max(2, len(origins) * 2 // 3)
    selection, assessment = origins[:selection_count], origins[selection_count:]
    methods = ["naive", "mean28", "weighted28", "ses02"]
    earliest = values[:selection[0]]
    if earliest.count(0) / len(earliest) >= 0.4 and sum(value > 0 for value in earliest) >= 4:
        methods.append("croston_sba")
    if selection[0] >= 84:
        methods.append("seasonal_naive7")

    def errors(method, cuts):
        return [sum(values[cut:cut + horizon]) - sum(predict(values[:cut], horizon, method)) for cut in cuts]

    scores = {method: mean(abs(e) for e in errors(method, selection)) for method in methods}
    method = min(methods, key=scores.get)
    evaluated, naive = errors(method, assessment), errors("naive", assessment)
    predicted = predict(values, horizon, method)
    total = sum(predicted)
    uncertainty = None
    if len(evaluated) >= 8:
        radius = max(abs(error) for error in evaluated)
        uncertainty = {"kind": "historical_error_range", "lower": float(round(max(0, total - radius), 3)),
                       "upper": float(round(total + radius, 3)), "assessment_windows": len(evaluated),
                       "probability": None}
    return {**result, "status": "estimated", "method": method,
            "quantity": total, "daily": predicted,
            "backtest": {"basis": "retrospective_final_snapshot", "availability_verified": False,
                         "horizon_days": horizon, "selection_windows": len(selection),
                         "assessment_windows": len(assessment),
                         "mae_units": float(round(mean(abs(e) for e in evaluated), 3)),
                         "rmse_units": round(sqrt(mean(e * e for e in evaluated)), 3),
                         "naive_mae_units": float(round(mean(abs(e) for e in naive), 3)),
                         "assessment_start": days[assessment[0]].day.isoformat(),
                         "assessment_end": days[-1].day.isoformat()},
            "uncertainty": uncertainty}


def replenish(available: int, forecast_result: dict, profile: dict | None) -> dict:
    empty = {"status": "missing_supply_parameters", "suggested_quantity": None,
             "coverage_days": None, "reorder_point": None, "lead_time_days": None,
             "safety_stock": None, "minimum_order_quantity": None, "incoming_quantity": None}
    if forecast_result["status"] != "estimated":
        return {**empty, "status": "forecast_unavailable"}
    quantity, horizon = forecast_result["quantity"], forecast_result["horizon_days"]
    if type(quantity) not in (int, Fraction) or quantity < 0 or type(horizon) is not int or horizon not in (7, 30, 60, 90):
        raise ValueError("Unrounded exact forecast required for replenishment.")
    if type(available) is not int or not 0 <= available <= 2**63 - 1:
        raise ValueError("Invalid available stock.")
    rate = Fraction(quantity) / horizon
    # Coverage is an indicative presentation metric, never an input to ordering.
    coverage = float(round(available / rate, 2)) if rate > 0 else None
    if profile is None or any(profile.get(field) is None for field in ("lead_time_days", "safety_stock", "minimum_order_quantity", "incoming_confirmed")):
        return {**empty, "coverage_days": coverage}
    lead, safety, minimum, incoming = (profile[field] for field in ("lead_time_days", "safety_stock", "minimum_order_quantity", "incoming_confirmed"))
    if any(type(value) is not int or not 0 <= value <= 10**9 for value in (lead, safety, minimum, incoming)) or lead > 90 or minimum < 1:
        raise ValueError("Invalid supply parameters.")
    # Only confirmed receipts due within the stated lead time belong in incoming.
    point = ceil(rate * lead + safety)
    need = rate * max(lead, horizon) + safety - available - incoming
    # The supplier minimum applies only to a strictly positive exact need.
    # A purchase is in whole units: ceiling is the explicit final business step.
    return {"status": "recommendation", "suggested_quantity": max(minimum, ceil(need)) if need > 0 else 0,
            "coverage_days": coverage, "reorder_point": point, "lead_time_days": lead,
            "safety_stock": safety, "minimum_order_quantity": minimum, "incoming_quantity": incoming}


def promotion(price_minor: int, cost_minor: int | None, discount_bps: int) -> dict:
    if type(discount_bps) is not int or not 0 <= discount_bps <= 9000 or type(price_minor) is not int or price_minor < 0:
        raise ValueError("Discount must be between zero and ninety percent.")
    if cost_minor is not None and (type(cost_minor) is not int or cost_minor < 0):
        raise ValueError("Nonnegative known unit cost required.")
    # Conservative half-up rounding to a whole currency minor unit.
    discounted = (price_minor * (10000 - discount_bps) + 5000) // 10000
    return {"discount_bps": discount_bps, "price_minor": str(discounted),
            "unit_margin_minor": str(discounted - cost_minor) if cost_minor is not None else None,
            "baseline_unit_margin_minor": str(price_minor - cost_minor) if cost_minor is not None else None,
            "demand_uplift": None, "profitability": "not_estimated"}
