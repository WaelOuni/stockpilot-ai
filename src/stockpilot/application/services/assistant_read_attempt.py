"""Versioned consultation outcome inside the existing bounded reply JSON.

SQL pending continues to block new keys, including under older binaries. This is
not a success reply; only finish_read may move the journal to completed.
"""
import re
from stockpilot.application.assistant_runtime import CODES, PHASES

KEY = "_read_attempt"


def attempt(reply):
    value = reply.get(KEY) if isinstance(reply, dict) else None
    if not isinstance(value, dict) or set(value) != {"version", "id", "state", "code", "phase", "retryable"}:
        return None
    if (type(value["version"]) is not int or value["version"] != 1 or not isinstance(value["id"], str)
        or not re.fullmatch("[0-9a-f]{32}", value["id"])
        or not isinstance(value["state"], str) or value["state"] not in {"running", "failed"}
        or not (value["code"] is None or isinstance(value["code"], str))
        or not isinstance(value["phase"], str)
        or value["code"] not in CODES | {None} or value["phase"] not in PHASES
        or type(value["retryable"]) is not bool):
        return None
    if (value["state"] == "running") != (value["code"] is None):
        return None
    if value["retryable"] != (value["code"] not in {"input_limit", "response_validation_error"}):
        return None
    return dict(value)


def envelope(identity, *, code=None, phase="request"):
    value = {"version": 1, "id": identity, "state": "running" if code is None else "failed",
             "code": code, "phase": phase, "retryable": code not in {"input_limit", "response_validation_error"}}
    reply = {"intent": "ASSISTANT_UNAVAILABLE", "text": "Consultation non aboutie. Vérifiez son état avant une reprise explicite.",
             "products": [], "order": None, "can_confirm": False, KEY: value}
    if attempt(reply) is None:
        raise ValueError("Invalid consultation outcome.")
    return reply
