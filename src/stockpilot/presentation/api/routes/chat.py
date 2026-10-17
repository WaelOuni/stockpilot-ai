from stockpilot.presentation.api.auth import require_customer_identity
from fastapi import APIRouter, Depends
import logging
from stockpilot.observability import timed_event

from stockpilot.application.dto.intent_result import IntentResult
from stockpilot.application.use_cases.classify_intent import ClassifyIntentUseCase
from stockpilot.application.use_cases.generate_response import GenerateResponseUseCase
from stockpilot.presentation.api.dependencies import (
    get_classify_intent_use_case,
    get_generate_response_use_case,
)
from stockpilot.presentation.api.schemas.chat import ChatRequest, ChatResponse


router = APIRouter(dependencies=[Depends(require_customer_identity)])
logger = logging.getLogger(__name__)


@router.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    use_case: GenerateResponseUseCase = Depends(get_generate_response_use_case),
) -> ChatResponse:
    with timed_event(logger, "chat_request_started", "chat_request_completed",
                     "chat_request_failed"):
        response = await use_case.execute(request.message)
    return ChatResponse(response=response)


@router.post("/classify", response_model=IntentResult)
async def classify(
    request: ChatRequest,
    use_case: ClassifyIntentUseCase = Depends(get_classify_intent_use_case),
) -> IntentResult:
    return await use_case.execute(request.message)
