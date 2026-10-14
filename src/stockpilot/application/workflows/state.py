from typing import TypedDict

from stockpilot.domain.enums.intent import Intent
from stockpilot.domain.enums.product_category import ProductCategory


class StockPilotState(TypedDict, total=False):
    message: str
    customer_id: str | None
    intent: Intent
    raw_product: str | None
    last_product_sku: str | None
    last_product_document_id: str | None
    product_resolution_document_id: str | None
    product_resolution_reference: str | None
    product_from_context: bool
    product_name: str | None
    category: ProductCategory | None
    budget: float | None
    usage: str | None
    quantity: int | float | bool | None
    pending_order: dict[str, str | int | dict | None] | None
    active_order_confirmation: dict | None
    last_order_quantity_evidence: dict | None
    inherited_quantity_evidence: dict | None
    quantity_validation_failed: bool
    order_status: str | None
    confirmation_handled: bool
    created_order_id: str | None
    last_created_order_id: str
    order_id: str | None
    queried_order_id: str | None
    queried_order_status: str | None
    order_status_result: str
    recommendations: list[dict]
    advice_status: str | None
    response: str
    last_relevant_intent: Intent | None
    last_product_name: str | None
    last_quantity: int | None
    last_category: ProductCategory | None
    last_budget: float | None
    last_usage: str | None
    last_advice_query: str | None
    context_customer_id: str | None
    advice_query: str | None
