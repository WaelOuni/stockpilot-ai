import json
import logging
import sys
from logging.handlers import RotatingFileHandler

import pytest

from stockpilot.observability import configure_logging, event
from stockpilot import telemetry
from opentelemetry.sdk.trace import TracerProvider


@pytest.fixture
def logger(monkeypatch):
    logger = logging.getLogger("stockpilot")
    previous_handlers = logger.handlers[:]
    previous_level = logger.level
    logger.handlers = []
    yield logger
    for handler in logger.handlers:
        handler.close()
    logger.handlers = previous_handlers
    logger.setLevel(previous_level)


def test_file_is_jsonl_rotating_and_stdout_remains(logger, tmp_path, capsys):
    path = tmp_path / "logs" / "stockpilot.jsonl"
    configure_logging("INFO", str(path))
    configure_logging("INFO", str(path))
    files = [h for h in logger.handlers if isinstance(h, RotatingFileHandler)]
    assert len(files) == 1
    assert files[0].maxBytes == 10 * 1024 * 1024
    assert files[0].backupCount == 3
    assert any(h.stream is sys.stdout for h in logger.handlers)
    event(logger, "synthetic", operation_id="op-file")
    event(logger, "synthetic_second")
    lines = path.read_text().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["operation_id"] == "op-file"
    assert json.loads(capsys.readouterr().out.splitlines()[0])["event"] == "synthetic"


def test_file_contains_trace_ids_and_redacted_exception(logger, tmp_path, monkeypatch):
    path = tmp_path / "stockpilot.jsonl"
    configure_logging("INFO", str(path))
    provider = TracerProvider()
    monkeypatch.setattr(telemetry, "_provider", provider)
    with telemetry.span("synthetic") as span:
        try:
            raise RuntimeError("private-user-content")
        except RuntimeError:
            event(logger, "failure", exc_info=True)
        expected = f"{span.get_span_context().trace_id:032x}"
    provider.shutdown()
    raw = path.read_text()
    assert "private-user-content" not in raw
    assert json.loads(raw)["trace_id"] == expected


def test_file_disabled_leaves_console_only(logger, tmp_path):
    path = tmp_path / "stockpilot.jsonl"
    configure_logging("INFO", str(path))
    configure_logging("INFO", None)
    assert not any(isinstance(h, RotatingFileHandler) for h in logger.handlers)
    assert any(h.stream is sys.stdout for h in logger.handlers)


def test_actual_rotation_keeps_valid_lines(logger, tmp_path):
    path = tmp_path / "stockpilot.jsonl"
    configure_logging("INFO", str(path))
    handler = next(h for h in logger.handlers if isinstance(h, RotatingFileHandler))
    handler.maxBytes = 200
    for index in range(8):
        event(logger, "rotation", sequence=index)
    assert (tmp_path / "stockpilot.jsonl.1").exists()
    for file in tmp_path.iterdir():
        for line in file.read_text().splitlines():
            assert json.loads(line)["event"] == "rotation"
