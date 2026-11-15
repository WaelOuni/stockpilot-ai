"""Bound provider inputs and translate transport failures without secret data."""
import httpx
from time import monotonic
from stockpilot.application.assistant_runtime import emit, phase

from stockpilot.application.resource_errors import (
    ModelInputLimitError, ModelTimeoutError,
    ModelProviderError, ModelTransportError, ModelResponseDecodeError,
)
from stockpilot.telemetry import instrument_httpx


async def post_ollama(settings, endpoint: str, payload: dict, input_text: str):
    if len(input_text.encode("utf-8")) > settings.ollama_max_prompt_bytes:
        raise ModelInputLimitError()
    started = {}
    async def trace(event, info):
        # httpcore trace info may contain sockets/URLs/exceptions. Never log it.
        names = {"connection.connect_tcp": "provider_connect", "connection.start_tls": "provider_connect",
                 "http11.receive_response_headers": "provider_headers", "http2.receive_response_headers": "provider_headers",
                 "http11.receive_response_body": "provider_body", "http2.receive_response_body": "provider_body"}
        stem, _, suffix = event.rpartition(".")
        if stem not in names:
            return
        if suffix == "started":
            started[stem] = monotonic()
        elif suffix in {"complete", "failed"} and stem in started:
            emit(names[stem], "success" if suffix == "complete" else "error",
                 (monotonic() - started.pop(stem)) * 1000)
    with phase("provider"):
        try:
            # HTTPX applies connect/read/write/pool inactivity budgets. This is
            # deliberately not cancellation of an entire workflow/transaction.
            async with httpx.AsyncClient(timeout=settings.ollama_timeout_seconds) as client:
                instrument_httpx(client)
                response = await client.post(f"{settings.ollama_base_url}{endpoint}", json=payload, extensions={"trace": trace})
                emit("provider", "observed", http_status=response.status_code)
                if not response.is_success:
                    raise ModelProviderError(response.status_code)
                try:
                    data = response.json()
                    if not isinstance(data, dict):
                        raise ValueError
                except ValueError:
                    raise ModelResponseDecodeError() from None
                for key, measured_phase in (("load_duration", "model_load"), ("prompt_eval_duration", "provider_prompt_evaluation"), ("eval_duration", "provider_generation")):
                    duration = data.get(key)
                    if type(duration) is int and duration >= 0:
                        emit(measured_phase, "observed", measured_ms=duration / 1_000_000)
                return data
        except httpx.TimeoutException as error:
            kinds = {httpx.ConnectTimeout: "connect_timeout", httpx.ReadTimeout: "read_timeout",
                     httpx.WriteTimeout: "write_timeout", httpx.PoolTimeout: "pool_timeout"}
            # A known timeout with an unknown phase stays a timeout; never
            # infer a read timeout from a base class or an exception message.
            code = next((value for kind, value in kinds.items() if isinstance(error, kind)), "transport_timeout")
            raise ModelTimeoutError(code) from None
        except httpx.HTTPError:
            raise ModelTransportError() from None
