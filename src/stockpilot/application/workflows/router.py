from stockpilot.application.workflows.state import StockPilotState
from stockpilot.domain.enums.intent import Intent


def route_by_intent(state: StockPilotState) -> str:
    intent = state.get("intent")

    if intent == Intent.PRODUCT_ADVICE:
        return "advice"

    if intent == Intent.STOCK_CHECK:
        return "stock"

    if intent == Intent.ORDER:
        return "order"

    if intent == Intent.ORDER_STATUS:
        return "order_status"

    return "unknown"
