from time import perf_counter
from functools import wraps
from contextvars import ContextVar


_instruments = {}
_allowed = {"model", "operation", "outcome", "reason",
            "http.request.method", "http.route", "http.response.status_code"}
_counters = [
    "http.requests", "llm.calls", "llm.errors", "rag.searches", "rag.empty_results",
    "stock.checks", "orders.prepared", "orders.finalized", "orders.failed",
    "mongo.order_transactions",
]
_durations = ["http.request.duration", "llm.duration", "rag.duration",
              "mongo.order_transaction.duration"]


def initialize_metrics(provider):
    global _instruments
    meter = provider.get_meter("stockpilot")
    _instruments = {
        name: meter.create_counter("stockpilot." + name)
        for name in _counters
    }
    _instruments.update({
        name: meter.create_histogram("stockpilot." + name, unit="s")
        for name in _durations
    })


def record(name, value=1, **attributes):
    if set(attributes) - _allowed:
        raise ValueError("Unsupported metric attributes")
    instrument = _instruments.get(name)
    if instrument is not None:
        if name in _durations:
            instrument.record(value, attributes)
        else:
            instrument.add(value, attributes)


def observe_transaction(function):
    @wraps(function)
    async def execute(*args, **kwargs):
        state = {"outcome": "error"}
        token = transaction_outcome.set(state)
        try:
            return await function(*args, **kwargs)
        finally:
            transaction_outcome.reset(token)
            # A precheck replay never opens a transaction.
            if state.get("started"):
                record("mongo.order_transactions", outcome=state["outcome"])
                record("mongo.order_transaction.duration", perf_counter() - state["started"],
                       outcome=state["outcome"])
    return execute


transaction_outcome = ContextVar("transaction_metric_outcome", default=None)


def mark_transaction(outcome=None, started=False):
    state = transaction_outcome.get()
    if state is not None:
        if outcome:
            state["outcome"] = outcome
        if started:
            state["started"] = perf_counter()


order_replay = ContextVar("order_metric_replay", default=False)
