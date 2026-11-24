"""Unfixed business regression exposed by independent review. Expected to fail."""
from datetime import date,timedelta
from stockpilot.domain.services.demand_forecast import DemandDay,forecast,replenish

def test_exact_horizon_stock_must_not_trigger_a_supplier_minimum():
    today=date(2026,11,17)
    days=tuple(DemandDay(today-timedelta(days=365-index),sold=(4,4,4,4,4,4,5)[index%7],source_references=('synthetic-review',)) for index in range(365))
    prediction=forecast(days,as_of=today,horizon=7)
    assert prediction['quantity']==29
    actual=replenish(29,prediction,{'lead_time_days':7,'safety_stock':0,'minimum_order_quantity':20,'incoming_confirmed':0})
    assert actual['suggested_quantity']==0, '29 units available cover exactly 29 forecast units; no supplier order is needed.'
