"""Bounded, content-free assistant observations and cooperative read-only budgets.

Request context is copied into the BFF worker and checkpoint threads. The shared
event crosses the ASGI/worker boundary without cancelling an ORDER transaction.
"""
import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, UTC
from functools import wraps
import inspect
import json
import logging
import threading
from time import monotonic
from uuid import uuid4

from stockpilot.application.ports.tenant_knowledge import TenantDataUnavailable

PHASES = frozenset({"request", "admission", "execution_lock", "begin", "classification",
    "embeddings", "retrieval", "generation", "validation", "checkpoint", "checkpoint_writes",
    "finalization", "failure_persistence", "provider", "provider_connect", "provider_headers",
    "provider_body", "model_load", "provider_prompt_evaluation", "provider_generation"})
CODES = frozenset({"provider_http_500", "provider_http_503", "provider_http_error",
    "connect_timeout", "read_timeout", "write_timeout", "pool_timeout", "transport_timeout", "transport_error",
    "response_decode_error", "response_validation_error", "input_limit", "client_cancelled",
    "server_cancelled", "global_budget_exceeded", "dependency_unavailable", "outcome_unknown",
    "authorization_denied", "conflict", "internal_error", "busy"})
_current = ContextVar("assistant_runtime", default=None)
_phase = ContextVar("assistant_phase", default="request")
logger = logging.getLogger("stockpilot.assistant.runtime")


class ReadOnlyInterrupted(TenantDataUnavailable):
    def __init__(self, code):
        self.code = code if code in CODES else "internal_error"
        super().__init__("Consultation interrupted; inspect the original turn.")


@dataclass
class Observation:
    correlation: str = field(default_factory=lambda: uuid4().hex)
    started: float = field(default_factory=monotonic)
    cancelled: threading.Event = field(default_factory=threading.Event)
    cancellation: str = "server_cancelled"
    deadline: float | None = None
    failure: tuple[str, str] | None = None
    enforcing: bool = True
    active_phase: str = "request"

    def cancel(self, code):
        self.cancellation = code if code in {"client_cancelled", "server_cancelled", "global_budget_exceeded"} else "server_cancelled"
        self.cancelled.set()


def current():
    return _current.get()


def error_code(error):
    from stockpilot.application.ports.tenant_knowledge import TenantDataConflict, TenantDataOutcomeUnknown
    from stockpilot.application.services.identity_authorization import AuthorizationDenied
    code = getattr(error, "code", None)
    if isinstance(code, str) and code in CODES:
        return code
    if isinstance(error, asyncio.CancelledError):
        obs = current()
        return obs.cancellation if obs and obs.cancelled.is_set() else "server_cancelled"
    if isinstance(error, TenantDataOutcomeUnknown):
        return "outcome_unknown"
    if isinstance(error, AuthorizationDenied):
        return "authorization_denied"
    if isinstance(error, TenantDataConflict):
        return "conflict"
    if isinstance(error, TenantDataUnavailable):
        return "dependency_unavailable"
    if isinstance(error, (ValueError, TypeError, KeyError)):
        return "response_validation_error"
    return "internal_error"


def emit(phase, outcome, duration_ms=0.0, *, code=None, http_status=None, measured_ms=None):
    obs = current()
    if obs is None or phase not in PHASES or outcome not in {"success", "error", "rejected", "observed"}:
        return
    record = {"event": "assistant_phase", "correlation_id": obs.correlation,
              "at": datetime.now(UTC).isoformat(), "request_elapsed_ms": round((monotonic() - obs.started) * 1000, 3),
              "operation": obs.active_phase, "phase": phase, "outcome": outcome, "duration_ms": round(max(0, duration_ms), 3)}
    if code in CODES:
        record["error_code"] = code
    if type(http_status) is int and 100 <= http_status <= 599:
        record["http_status"] = http_status
    if isinstance(measured_ms, (float, int)) and 0 <= measured_ms <= 86400000:
        record["provider_duration_ms"] = round(measured_ms, 3)
    # Only explicit scalar fields above; never str(exception), URLs or payloads.
    logger.info(json.dumps(record, separators=(",", ":"), allow_nan=False))


def check_budget():
    obs = current()
    if obs is None or obs.deadline is None or not obs.enforcing:
        return
    if obs.cancelled.is_set():
        raise ReadOnlyInterrupted(obs.cancellation)
    if monotonic() >= obs.deadline:
        raise ReadOnlyInterrupted("global_budget_exceeded")


@contextmanager
def observation(value=None):
    token = _current.set(value or Observation())
    try:
        yield current()
    finally:
        _current.reset(token)


@contextmanager
def phase(name, *, budget=True):
    assert name in PHASES
    token = _phase.set(name)
    started = monotonic()
    obs = current()
    if obs is not None and name in {"classification", "embeddings", "retrieval", "generation", "validation"}:
        obs.active_phase = name
    try:
        if budget:
            check_budget()
        yield
    except BaseException as error:
        code = error_code(error)
        obs = current()
        if obs is not None and obs.failure is None:
            obs.failure = name, code
        emit(name, "error", (monotonic() - started) * 1000, code=code)
        raise
    else:
        emit(name, "success", (monotonic() - started) * 1000)
    finally:
        _phase.reset(token)


def observed(name):
    def decorate(function):
        if inspect.iscoroutinefunction(function):
            @wraps(function)
            async def asynchronous(*args, **kwargs):
                with phase(name, budget=False):
                    return await function(*args, **kwargs)
            return asynchronous
        @wraps(function)
        def synchronous(*args, **kwargs):
            with phase(name, budget=False):
                return function(*args, **kwargs)
        return synchronous
    return decorate


async def bounded_read(operation):
    """Cancel only this read-only coroutine; drain it before releasing admission.

Synchronous database operations keep their own bounded SQL/connect timeouts.
No hard real-time promise is made for kernel stalls or process termination.
"""
    obs = current()
    assert obs is not None and obs.deadline is not None
    async def watch():
        while True:
            check_budget()
            await asyncio.sleep(min(0.05, max(0.001, obs.deadline - monotonic())))
    work, watcher = asyncio.create_task(operation), asyncio.create_task(watch())
    try:
        done, _ = await asyncio.wait({work, watcher}, return_when=asyncio.FIRST_COMPLETED)
        if watcher in done:
            error = watcher.exception()
            failed_phase = obs.active_phase
            obs.cancel(error.code)
            work.cancel()
            await asyncio.gather(work, return_exceptions=True)
            # A cooperative deadline/cancellation has known provenance, unlike a
            # bare CancelledError from an unrelated server task.
            obs.failure = (failed_phase, error.code)
            raise error
        return await work
    finally:
        if not work.done():
            if not obs.cancelled.is_set():
                obs.cancel("server_cancelled")
            work.cancel()
        watcher.cancel()
        await asyncio.gather(work, watcher, return_exceptions=True)
