"""Explainable analytics; the LLM never supplies or recalculates business facts."""
from datetime import UTC, datetime, timedelta
from collections import defaultdict
from statistics import mean

from stockpilot.application.ports.intelligence import IntelligencePort
from stockpilot.application.services.identity_authorization import AuthorizationDenied
from stockpilot.domain.entities.intelligence import IntelligenceQuery
from stockpilot.domain.entities.tenant_authorization import TenantContext
from stockpilot.domain.enums.permission import Permission
from stockpilot.domain.enums.role import Role
from stockpilot.domain.services.demand_forecast import forecast, replenish, promotion


MOVEMENTS = ("sold", "returned", "received", "adjusted", "reserved", "cancelled", "transferred")


def _forecast_response(prediction):
    """Round for display only, after replenishment has used the exact expectation."""
    return {**prediction,
            "quantity": float(round(prediction["quantity"], 3)) if prediction["quantity"] is not None else None,
            "daily": [float(round(value, 3)) for value in prediction["daily"]]}


def _page_series(series, cohorts, start, period_days):
    """Separate provenance cohorts; incomplete cohort/day aggregates stay unknown."""
    if not cohorts:
        return []
    rows = []
    for offset in range(period_days):
        day = (start + timedelta(days=offset)).isoformat()
        datasets = []
        for dataset, products in sorted(cohorts.items()):
            facts = series[(day, dataset)]
            complete = len(facts) == len(products)
            datasets.append({"dataset": dataset, "expected_products": len(products),
                "complete_products": len(facts),
                **{field: sum(getattr(fact, field) for fact in facts.values()) if complete else None for field in MOVEMENTS}})
        rows.append({"date": day, "datasets": datasets})
    return rows


class Intelligence:
    def __init__(self, repository: IntelligencePort, authorization, *, clock=None):
        self.repository, self.authorization = repository, authorization
        self.clock = clock or (lambda: datetime.now(UTC))

    def _check(self, request, context):
        current = self.authorization.resolve_context(request)
        if not isinstance(context, TenantContext) or current != context or current.principal.role not in (Role.MANAGER, Role.ORGANIZATION_ADMIN) or Permission.ANALYTICS_READ not in current.principal.permissions:
            raise AuthorizationDenied("Current analytics permission required.")

    def dashboard(self, request, context, query: IntelligenceQuery):
        if not isinstance(query, IntelligenceQuery):
            raise TypeError("Typed analytics filters required.")
        self._check(request, context)
        now = self.clock().astimezone(UTC)
        as_of = now.date()
        start = as_of - timedelta(days=query.period_days)
        raw = self.repository.snapshot(request, context, query, as_of)
        items, series, cohorts = [], defaultdict(dict), defaultdict(set)
        for product in raw["products"]:
            pid = product["product_id"]
            days = raw["days"].get(pid, ())
            profile = raw["profiles"].get(pid)
            selected = tuple(day for day in days if start <= day.day < as_of)
            full_period = ({day.day for day in selected} == {start + timedelta(days=i) for i in range(query.period_days)}
                and len(selected) == query.period_days and all(day.complete for day in selected)
                and len({day.dataset for day in selected}) == 1)
            prediction = forecast(days, as_of=as_of, horizon=query.horizon_days)
            replenishment = replenish(product["available"], prediction, profile if profile and profile.get("incoming_as_of") == as_of.isoformat() else None)
            cost = profile.get("unit_cost_minor") if profile and profile["currency"] == product["currency"] else None
            sold = sum(day.sold for day in selected) if full_period else None
            daily_mean = sold / query.period_days if sold is not None else None
            dormant = full_period and sold == 0 and not any(day.stockout for day in selected) and product["quantity"] > 0
            reasons = []
            if product["status"] == "active":
                if product["available"] == 0: reasons.append("out_of_stock")
                elif product["available"] <= query.low_stock_threshold: reasons.append("low_stock_threshold")
            if product["status"] != "active":
                replenishment = {**replenishment, "status": "inactive_product", "suggested_quantity": None}
            if replenishment["suggested_quantity"]: reasons.append("replenishment_estimate")
            if dormant: reasons.append("no_observed_sales_in_period")
            if any(day.adjusted for day in selected): reasons.append("administrative_movements_to_review")
            if prediction["status"] != "estimated": reasons.append(prediction["status"])
            turnover_proxy = (sold / product["quantity"]) if sold is not None and product["quantity"] > 0 else None
            for day in selected:
                cohorts[day.dataset].add(pid)
                if day.complete:
                    series[(day.day.isoformat(), day.dataset)][pid] = day
            items.append({**product, **{key: str(product[key]) for key in ("quantity", "reserved", "available")}, "period_sales_units": sold,
                "period_returns_units": sum(day.returned for day in selected) if full_period else None,
                "mean_daily_sales": round(daily_mean, 3) if daily_mean is not None else None,
                "sales_to_current_stock_ratio": round(turnover_proxy, 3) if turnover_proxy is not None else None,
                "dormant": dormant, "period_complete": full_period,
                "history_days": len(days), "source_references": list(dict.fromkeys(ref for day in selected for ref in day.source_references))[:20],
                "source_reference_count": len({ref for day in selected for ref in day.source_references}),
                "dataset": next(iter({day.dataset for day in days}), "unavailable") if len({day.dataset for day in days}) <= 1 else "mixed",
                "forecast": _forecast_response(prediction), "replenishment": replenishment,
                "unit_cost_minor": str(cost) if cost is not None else None,
                "supplier": profile.get("supplier") if profile else None,
                "supply_parameters": {key: profile.get(key) for key in ("lead_time_days", "safety_stock", "minimum_order_quantity", "incoming_confirmed", "incoming_as_of", "source_references")} if profile else None,
                "stock_cost_minor": str(cost * product["quantity"]) if cost is not None else None,
                "promotion_scenarios": [promotion(int(product["price_minor"]), cost, bps) for bps in dict.fromkeys((500, 1000, 1500, query.discount_bps))],
                "reasons": reasons})
        coverage = [item["replenishment"]["coverage_days"] for item in items if item["replenishment"]["coverage_days"] is not None]
        self._check(request, context)
        return {"organization_id": context.organization_id, "refreshed_at": now.isoformat(),
            "period_start": start.isoformat(), "period_end_exclusive": as_of.isoformat(),
            "horizon_days": query.horizon_days, "low_stock_threshold": query.low_stock_threshold,
            "summary": raw["summary"], "items": items, "total": raw["total"],
            "limit": query.limit, "offset": query.offset, "has_more": query.offset + query.limit < raw["total"],
            "page_summary": {"products": len(items), "dormant_products": sum(item["dormant"] for item in items),
                "forecastable_products": sum(item["forecast"]["status"] == "estimated" for item in items),
                "mean_coverage_days": round(mean(coverage), 2) if coverage else None},
            "page_series": _page_series(series, cohorts, start, query.period_days),
            "limitations": ["reservations_are_not_sales", "movement_sign_is_not_commercial_semantics",
                "no_average_stock_history_for_turnover", "no_lot_age_or_annual_seasonality",
                "promotion_demand_and_profitability_not_estimated", "page_series_not_organization_total",
                "series_separated_by_dataset", "historical_availability_not_recorded"],
            "explanation_mode": "deterministic", "automatic_actions": False}
