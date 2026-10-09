import json
import logging
import sys
from pathlib import Path
from logging.handlers import RotatingFileHandler
import traceback
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from time import perf_counter
from functools import wraps
from stockpilot.telemetry import trace_fields, span, enrich_span
from stockpilot.metrics import record


_context: ContextVar[dict] = ContextVar("log_context", default={})


def bind_context(**fields) -> None:
    _context.set({**_context.get(), **fields})


@contextmanager
def log_context(**fields):
    token = _context.set({**_context.get(), **fields})
    try:
        yield
    finally:
        _context.reset(token)


def event(logger, name, *, level=logging.INFO, exc_info=False, **fields):
    logger.log(
        level, fields.get("message", name),
        extra={"event": name, "fields": {**_context.get(), **fields, **trace_fields()}},
        exc_info=exc_info,
    )


class Timer:
    def __init__(self):
        self.started = perf_counter()

    @property
    def duration_ms(self):
        return round((perf_counter() - self.started) * 1000, 3)


@contextmanager
def timed_event(logger, started, completed, failed, **fields):
    timer = Timer()
    event(logger, started, **fields)
    try:
        yield
    except Exception as error:
        event(logger, failed, level=logging.ERROR,
              duration_ms=timer.duration_ms, error_type=type(error).__name__, **fields)
        raise
    else:
        event(logger, completed, duration_ms=timer.duration_ms, **fields)


def observe_llm(kind):
    def decorate(function):
        @wraps(function)
        async def call(self, *args, **kwargs):
            with span(
                "stockpilot.llm.classify" if kind == "classification"
                else "stockpilot.llm.generate",
                **{"gen_ai.system": "ollama", "gen_ai.request.model": self._settings.ollama_model},
            ):
                return await observed(self, *args, **kwargs)

        async def observed(self, *args, **kwargs):
            logger = logging.getLogger(function.__module__)
            timer = Timer()
            fields = {"model": self._settings.ollama_model, "call_type": kind}
            event(logger, f"llm_{kind}_started", **fields)
            try:
                result = await function(self, *args, **kwargs)
            except Exception as error:
                attributes = {"model": self._settings.ollama_model,
                              "operation": "classify" if kind == "classification" else "generate",
                              "outcome": "error"}
                record("llm.calls", **attributes)
                record("llm.errors", **attributes)
                record("llm.duration", timer.duration_ms / 1000, **attributes)
                event(logger, "llm_call_failed", level=logging.ERROR,
                      duration_ms=timer.duration_ms, error_type=type(error).__name__, **fields)
                raise
            if kind == "classification":
                fields["intent"] = result.intent.value
                enrich_span(**{"stockpilot.intent": result.intent.value})
            attributes = {"model": self._settings.ollama_model,
                          "operation": "classify" if kind == "classification" else "generate",
                          "outcome": "success"}
            record("llm.calls", **attributes)
            record("llm.duration", timer.duration_ms / 1000, **attributes)
            event(logger, f"llm_{kind}_completed", duration_ms=timer.duration_ms, **fields)
            return result
        return call
    return decorate


class JsonFormatter(logging.Formatter):
    def format(self, record):
        data = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": getattr(record, "event", "application_log"),
            **getattr(record, "fields", {}),
        }
        if not hasattr(record, "event"):
            data["message"] = record.getMessage()
        if record.exc_info:
            error_type, _, tb = record.exc_info
            data["error_type"] = error_type.__name__
            # Exception text and source lines can contain prompts or credentials.
            data["stacktrace"] = [
                {"file": frame.filename, "line": frame.lineno, "function": frame.name}
                for frame in traceback.extract_tb(tb)
            ]
        return json.dumps(data, ensure_ascii=True, default=str)


def configure_logging(level: str, log_file: str | None = None):
    logger = logging.getLogger("stockpilot")
    logger.setLevel(level)
    if not any(getattr(handler, "_stockpilot_json", False) for handler in logger.handlers):
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        handler._stockpilot_json = True
        logger.addHandler(handler)
    desired = str(Path(log_file).resolve()) if log_file else None
    for handler in list(logger.handlers):
        if getattr(handler, "_stockpilot_file", False):
            if handler.baseFilename == desired:
                desired = None
            else:
                logger.removeHandler(handler)
                handler.close()
    if desired:
        Path(desired).parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            desired, maxBytes=10 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        handler.setFormatter(JsonFormatter())
        handler._stockpilot_file = True
        logger.addHandler(handler)
