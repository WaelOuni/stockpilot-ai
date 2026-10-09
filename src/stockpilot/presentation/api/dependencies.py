from fastapi import Request

from stockpilot.application.use_cases.classify_intent import ClassifyIntentUseCase
from stockpilot.application.use_cases.generate_response import GenerateResponseUseCase


def get_generate_response_use_case(request: Request) -> GenerateResponseUseCase:
    return request.app.state.generate_response_use_case


def get_classify_intent_use_case(request: Request) -> ClassifyIntentUseCase:
    return request.app.state.classify_intent_use_case


def get_stockpilot_workflow(request: Request):
    return request.app.state.stockpilot_workflow
