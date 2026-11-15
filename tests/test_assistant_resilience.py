"""Content-free observations and cooperative read-only cancellation contracts."""
import asyncio
import json
import logging
from time import monotonic

import httpx
import pytest

from stockpilot.application.assistant_runtime import (
    Observation, ReadOnlyInterrupted, bounded_read, error_code, observation, phase,
)
from stockpilot.application.services.assistant_read_attempt import attempt, envelope
from stockpilot.infrastructure.config.settings import Settings
from stockpilot.infrastructure.llm import resource_limits
from stockpilot.infrastructure.llm.ollama_adapter import OllamaAdapter
from test_model_resource_limits import transport


@pytest.mark.parametrize("failure,code", [
    (httpx.ConnectTimeout, "connect_timeout"), (httpx.ReadTimeout, "read_timeout"),
    (httpx.WriteTimeout, "write_timeout"), (httpx.PoolTimeout, "pool_timeout"),
    (httpx.ConnectError, "transport_error"), (httpx.ReadError, "transport_error"),
    (httpx.TimeoutException, "transport_timeout"),
])
def test_exact_transport_provenance_without_exception_contents(monkeypatch, caplog, failure, code):
    def handler(request):
        raise failure("SECRET_COOKIE_AND_DOCUMENT", request=request)
    transport(monkeypatch, handler)
    with caplog.at_level(logging.INFO, logger="stockpilot.assistant.runtime"), observation():
        with pytest.raises(Exception) as caught:
            asyncio.run(OllamaAdapter(Settings()).generate("PRIVATE_PROMPT"))
    assert error_code(caught.value) == code
    records = [json.loads(r.message) for r in caplog.records if r.name == "stockpilot.assistant.runtime"]
    assert records[-1]["error_code"] == code
    assert "PRIVATE_PROMPT" not in caplog.text and "SECRET_COOKIE" not in caplog.text
    assert "SECRET_COOKIE" not in str(caught.value)


@pytest.mark.parametrize("status,code", [(500, "provider_http_500"), (503, "provider_http_503"),
    (401, "provider_http_error"), (429, "provider_http_error")])
def test_provider_http_is_not_called_a_timeout(monkeypatch, caplog, status, code):
    transport(monkeypatch, lambda request: httpx.Response(status, json={"error": "SECRET_BODY"}))
    with caplog.at_level(logging.INFO, logger="stockpilot.assistant.runtime"), observation():
        with pytest.raises(Exception) as caught:
            asyncio.run(OllamaAdapter(Settings()).generate("SYNTHETIC"))
    assert error_code(caught.value) == code
    assert any(json.loads(r.message).get("http_status") == status for r in caplog.records)
    assert "SECRET_BODY" not in caplog.text


def test_decode_and_provider_load_metrics_are_separate(monkeypatch, caplog):
    transport(monkeypatch, lambda request: httpx.Response(200, json={
        "response": "synthetic", "load_duration": 39900000000, "eval_duration": 123000000,
    }))
    with caplog.at_level(logging.INFO, logger="stockpilot.assistant.runtime"), observation() as obs:
        with phase("classification"):
            assert asyncio.run(OllamaAdapter(Settings()).generate("PRIVATE_PROMPT")) == "synthetic"
    records = [json.loads(r.message) for r in caplog.records]
    load = next(r for r in records if r["phase"] == "model_load")
    assert load["provider_duration_ms"] == 39900 and load["operation"] == "classification"
    assert all(r["correlation_id"] == obs.correlation and r["at"] and r["request_elapsed_ms"] >= 0 for r in records)
    monkeypatch.undo()
    transport(monkeypatch, lambda request: httpx.Response(200, content=b"SECRET_NOT_JSON"))
    with pytest.raises(Exception) as caught:
        asyncio.run(OllamaAdapter(Settings()).generate("synthetic"))
    assert error_code(caught.value) == "response_decode_error"


@pytest.mark.parametrize("reason", ["global_budget_exceeded", "client_cancelled", "server_cancelled"])
def test_read_budget_drains_work_and_retains_actual_cancellation_reason(reason):
    drained = []
    async def run():
        with observation(Observation(deadline=monotonic() + 0.03)) as obs:
            async def work():
                with phase("classification"):
                    try:
                        await asyncio.sleep(10)
                    finally:
                        await asyncio.sleep(0.01)
                        drained.append(True)
            if reason != "global_budget_exceeded":
                obs.cancel(reason)
            with pytest.raises(ReadOnlyInterrupted) as caught:
                await bounded_read(work())
            assert caught.value.code == reason
            assert obs.failure == ("classification", reason)
            assert drained or reason != "global_budget_exceeded"
    asyncio.run(run())


def test_bad_exception_metadata_cannot_replace_the_original_error():
    error = RuntimeError("private")
    error.code = []
    assert error_code(error) == "internal_error"


@pytest.mark.parametrize("case,code", [("slow", "read_timeout"), ("disconnect", "transport_error"), ("500", "provider_http_500")])
def test_actual_http_socket_failures_are_typed(monkeypatch, caplog, case, code):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    import time
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            if case == "disconnect":
                self.close_connection = True
                return
            if case == "slow":
                time.sleep(.15)
            try:
                self.send_response(500 if case == "500" else 200)
                self.end_headers()
                self.wfile.write(b'{"error":"SYNTHETIC_PRIVATE_BODY"}')
            except (BrokenPipeError, ConnectionResetError):
                pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        settings = Settings(ollama_base_url=f'http://127.0.0.1:{server.server_port}', ollama_timeout_seconds=.05)
        with caplog.at_level(logging.INFO, logger="stockpilot.assistant.runtime"), observation():
            with pytest.raises(Exception) as caught:
                asyncio.run(OllamaAdapter(settings).generate('SYNTHETIC_PRIVATE_PROMPT'))
        assert error_code(caught.value) == code
        assert any(json.loads(r.message)['phase'] == 'provider_connect' for r in caplog.records if r.name == 'stockpilot.assistant.runtime')
        assert 'SYNTHETIC_PRIVATE' not in caplog.text
    finally:
        server.shutdown(); server.server_close(); thread.join()


def test_asgi_disconnect_reaches_the_worker_read_budget():
    from types import SimpleNamespace
    from stockpilot.presentation.api.routes.b2b_assistant import invoke
    from stockpilot.application.assistant_runtime import current
    import anyio
    async def run():
        entered = asyncio.Event()
        class Assistant:
            def current(self, *args):
                pass
            async def read(self, *args):
                obs = current()
                obs.deadline = monotonic() + 5
                with phase('classification'):
                    await bounded_read(asyncio.sleep(10))
        async def disconnected():
            return {"type": "http.disconnect"}
        request = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(bff_assistant=Assistant(),
                bff_settings=SimpleNamespace(mode='b2b_business_test'), business_limiter=anyio.CapacityLimiter(2))),
            state=SimpleNamespace(tenant_request=object(), tenant_context=object()), receive=disconnected)
        with pytest.raises(ReadOnlyInterrupted) as caught:
            await invoke(request, lambda a: a.read)
        assert caught.value.code == 'client_cancelled'
        assert len(request.state.assistant_correlation) == 32
    asyncio.run(run())


def test_disconnect_is_received_through_an_anyio_stream_after_body_parsing():
    """The cancelled polling receive used by is_disconnected misses this stream."""
    import anyio
    import threading
    from types import SimpleNamespace
    from starlette.requests import Request
    from stockpilot.presentation.api.routes.b2b_assistant import invoke
    from stockpilot.application.assistant_runtime import current
    async def run():
        entered = threading.Event()
        class Assistant:
            def current(self, *args): pass
            async def read(self, *args):
                current().deadline = monotonic() + 1
                entered.set()
                with phase('classification'):
                    await bounded_read(asyncio.sleep(10))
        sender, receiver = anyio.create_memory_object_stream(2)
        active, maximum = 0, 0
        async def receive():
            nonlocal active, maximum
            active += 1; maximum = max(maximum, active)
            try:
                await anyio.lowlevel.checkpoint()
                return await receiver.receive()
            finally:
                active -= 1
        app = SimpleNamespace(state=SimpleNamespace(bff_assistant=Assistant(),
            bff_settings=SimpleNamespace(mode='b2b_business_test'), business_limiter=anyio.CapacityLimiter(2)))
        request = Request({'type':'http', 'app':app, 'state':{'tenant_request':object(),'tenant_context':object()}}, receive)
        async def disconnect():
            assert await asyncio.to_thread(entered.wait, 2)
            await sender.send({'type':'http.request', 'body':b'', 'more_body':False})
            await sender.send({'type':'http.disconnect'})
        producer = asyncio.create_task(disconnect())
        try:
            with pytest.raises(ReadOnlyInterrupted) as caught:
                await invoke(request, lambda a: a.read)
            assert caught.value.code == 'client_cancelled'
            assert active == 0 and maximum == 1
            await producer
        finally:
            producer.cancel()
            await sender.aclose(); await receiver.aclose()
    asyncio.run(run())


def test_normal_response_joins_receive_watcher_without_claiming_client_cancellation():
    import anyio
    from types import SimpleNamespace
    from starlette.requests import Request
    from stockpilot.presentation.api.routes.b2b_assistant import invoke
    from stockpilot.application.assistant_runtime import current
    captured = []
    async def run():
        class Assistant:
            def current(self, *args): pass
            async def read(self, *args):
                captured.append(current())
                await asyncio.sleep(.02)
                return 'completed'
        sender, receiver = anyio.create_memory_object_stream(1)
        active = 0
        async def receive():
            nonlocal active
            active += 1
            try: return await receiver.receive()
            finally: active -= 1
        app = SimpleNamespace(state=SimpleNamespace(bff_assistant=Assistant(),
            bff_settings=SimpleNamespace(mode='b2b_business_test'), business_limiter=anyio.CapacityLimiter(2)))
        request = Request({'type':'http','app':app,'state':{'tenant_request':object(),'tenant_context':object()}},receive)
        assert await invoke(request, lambda a:a.read) == 'completed'
        assert active == 0 and not captured[0].cancelled.is_set()
        await sender.aclose(); await receiver.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("field,value", [("version", True), ("id", "private"), ("state", []),
    ("code", {}), ("phase", "arbitrary-private-text"), ("retryable", 1), ("code", "read_timeout")])
def test_malformed_attempt_metadata_is_not_trusted(field, value):
    reply = envelope("a" * 32)
    reply["_read_attempt"][field] = value
    assert attempt(reply) is None
