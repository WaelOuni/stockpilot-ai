from fastapi import Request

from stockpilot.application.use_cases.classify_intent import ClassifyIntentUseCase
from stockpilot.application.use_cases.generate_response import GenerateResponseUseCase
from stockpilot.application.use_cases.update_order_status import UpdateOrderStatusUseCase
from stockpilot.application.use_cases.cancel_order import CancelOrderUseCase
from stockpilot.application.use_cases.list_customer_orders import ListCustomerOrdersUseCase


def get_generate_response_use_case(request: Request) -> GenerateResponseUseCase:
    return request.app.state.generate_response_use_case


def get_classify_intent_use_case(request: Request) -> ClassifyIntentUseCase:
    return request.app.state.classify_intent_use_case


def get_stockpilot_workflow(request: Request):
    return request.app.state.stockpilot_workflow


def get_update_order_status_use_case(request: Request) -> UpdateOrderStatusUseCase:
    return request.app.state.update_order_status_use_case


def get_cancel_order_use_case(request: Request) -> CancelOrderUseCase:
    return request.app.state.cancel_order_use_case


def get_list_customer_orders_use_case(request: Request) -> ListCustomerOrdersUseCase:
    return request.app.state.list_customer_orders_use_case
