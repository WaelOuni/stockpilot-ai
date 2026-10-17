"""Bound provider inputs and translate transport failures without secret data."""
import httpx

from stockpilot.application.resource_errors import (
    ModelInputLimitError, ModelTimeoutError, ModelUnavailableError,
)
from stockpilot.telemetry import instrument_httpx


async def post_ollama(settings, endpoint: str, payload: dict, input_text: str):
    if len(input_text.encode("utf-8")) > settings.ollama_max_prompt_bytes:
        raise ModelInputLimitError()
    try:
        # HTTPX applies connect/read/write/pool inactivity budgets. This is
        # deliberately not cancellation of an entire workflow/transaction.
        async with httpx.AsyncClient(timeout=settings.ollama_timeout_seconds) as client:
            instrument_httpx(client)
            response = await client.post(f"{settings.ollama_base_url}{endpoint}", json=payload)
            response.raise_for_status()
            return response.json()
    except httpx.TimeoutException:
        raise ModelTimeoutError() from None
    except (httpx.HTTPError, ValueError):
        raise ModelUnavailableError() from None
