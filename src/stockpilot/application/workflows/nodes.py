import logging
from stockpilot.observability import Timer, bind_context, event, timed_event

from stockpilot.application.use_cases.classify_intent import ClassifyIntentUseCase
from stockpilot.application.order_transaction_errors import (
    OrderOutcomeUnknownError,
    OrderTransactionFailedError,
)
from stockpilot.application.workflows.state import StockPilotState
from stockpilot.application.use_cases.check_stock import CheckStockUseCase
from stockpilot.application.use_cases.finalize_order import FinalizeOrderUseCase
from stockpilot.application.use_cases.prepare_order import PrepareOrderUseCase
from stockpilot.application.use_cases.recommend_product import RecommendProductUseCase
from stockpilot.application.workflows.order_confirmation import (
    normalize_order_confirmation,
)
from stockpilot.domain.enums.order_status import PreparedOrderStatus


logger = logging.getLogger(__name__)


def create_classify_node(classify_intent: ClassifyIntentUseCase):
    async def classify_node(state: StockPilotState) -> StockPilotState:
        with timed_event(logger, "classification_started", "classification_completed",
                         "classification_failed"):
            result = await classify_intent.execute(state["message"])
        bind_context(intent=result.intent.value)
        event(logger, "intent_detected", intent=result.intent.value)

        return {
            "intent": result.intent,
            "raw_product": result.raw_product,
            "product_name": result.product_name,
            "category": result.category,
            "budget": result.budget,
            "usage": result.usage,
            "quantity": result.quantity,
        }

    return classify_node


def create_order_confirmation_node(
    finalize_order: FinalizeOrderUseCase,
):
    async def resolve_order_confirmation_node(
        state: StockPilotState,
    ) -> StockPilotState:
        pending_order = state.get("pending_order")
        is_pending = (
            pending_order is not None
            and pending_order.get("status")
            == PreparedOrderStatus.PENDING_CONFIRMATION.value
            and state.get("order_status")
            == PreparedOrderStatus.PENDING_CONFIRMATION.value
        )
        decision = (
            normalize_order_confirmation(state["message"])
            if is_pending
            else None
        )

        if decision is None:
            return {"confirmation_handled": False}

        updated_order = dict(pending_order)
        if decision == "confirmed":
            product_name = str(updated_order["product_name"])
            quantity = int(updated_order["quantity"])
            operation_id = str(updated_order["operation_id"])
            bind_context(operation_id=operation_id, intent="ORDER")
            event(logger, "order_confirmation_received",
                  product_name=product_name, quantity=quantity)
            timer = Timer()
            try:
                created_order = await finalize_order.execute(
                    product_name, quantity, operation_id=operation_id
                )
            except OrderOutcomeUnknownError:
                event(logger, "order_outcome_unknown", level=logging.CRITICAL,
                      duration_ms=timer.duration_ms, exc_info=True)
                status = PreparedOrderStatus.ORDER_OUTCOME_UNKNOWN
                updated_order["status"] = status.value
                return {
                    "pending_order": updated_order,
                    "order_status": status.value,
                    "confirmation_handled": True,
                    "created_order_id": None,
                    "response": (
                        "Impossible de vérifier la création de la commande. "
                        "Une intervention manuelle est nécessaire."
                    ),
                }
            except OrderTransactionFailedError:
                event(logger, "order_transaction_failed", level=logging.ERROR,
                      duration_ms=timer.duration_ms, exc_info=True)
                status = PreparedOrderStatus.ORDER_CREATION_FAILED
                updated_order["status"] = status.value
                return {
                    "pending_order": updated_order,
                    "order_status": status.value,
                    "confirmation_handled": True,
                    "created_order_id": None,
                    "response": (
                        "Impossible de finaliser la commande. "
                        "La transaction a été annulée, sans modification du stock."
                    ),
                }
            if created_order is None:
                status = PreparedOrderStatus.RESERVATION_FAILED
                updated_order["status"] = status.value
                return {
                    "pending_order": updated_order,
                    "order_status": status.value,
                    "confirmation_handled": True,
                    "created_order_id": None,
                    "response": (
                        f"Le stock de {product_name} n'est plus suffisant "
                        "pour finaliser la commande."
                    ),
                }
            updated_order["status"] = PreparedOrderStatus.CONFIRMED.value
            bind_context(order_id=created_order.order_id)
            event(logger, "order_finalized", order_id=created_order.order_id,
                  operation_id=operation_id, quantity=quantity,
                  product_name=product_name, order_status="CONFIRMED",
                  duration_ms=timer.duration_ms)
            order_status = PreparedOrderStatus.CONFIRMED
            response = (
                f"Commande {created_order.order_id} créée pour "
                f"{quantity} {product_name}."
            )
            created_order_id = created_order.order_id
        else:
            updated_order["status"] = PreparedOrderStatus.CANCELLED.value
            order_status = PreparedOrderStatus.CANCELLED
            response = "Commande annulée."
            created_order_id = None

        return {
            "pending_order": updated_order,
            "order_status": order_status.value,
            "confirmation_handled": True,
            "created_order_id": created_order_id,
            "response": response,
        }

    return resolve_order_confirmation_node


def route_after_confirmation(state: StockPilotState) -> str:
    if state.get("confirmation_handled"):
        return "end"
    return "classify"


def create_advice_node(
    recommend_product: RecommendProductUseCase,
    check_stock: CheckStockUseCase,
):
    async def advice_node(
        state: StockPilotState,
    ) -> StockPilotState:
        recommendation = await recommend_product.execute(
            state["message"]
        )
        response = recommendation.answer
        product_name = recommendation.product_name

        if product_name:
            stock_result = await check_stock.execute(product_name)

            if stock_result is None:
                response += "\n\nLa disponibilité de ce produit n'a pas pu être vérifiée."
            elif stock_result.stock > 0:
                response += (
                    f"\n\nIl est actuellement disponible avec "
                    f"{stock_result.stock} unité(s) en stock."
                )
            else:
                response += "\n\nIl est actuellement indisponible."

        return {
            "response": response,
        }

    return advice_node


def create_stock_node(check_stock: CheckStockUseCase):
    async def stock_node(state: StockPilotState) -> StockPilotState:
        product_name = state.get("product_name")

        if not product_name:
            return {
                "response": "Je n'ai pas identifié le produit à vérifier."
            }

        with timed_event(logger, "stock_check_started", "stock_check_completed",
                         "stock_check_failed"):
            result = await check_stock.execute(product_name)

        if result is None:
            return {
                "response": f"Je n'ai pas trouvé le produit {product_name}."
            }

        if result.available:
            return {
                "response": (
                    f"{result.name} est disponible "
                    f"avec {result.stock} unité(s) en stock."
                )
            }

        return {
            "response": f"{result.name} n'est actuellement plus en stock."
        }

    return stock_node


def create_order_node(prepare_order: PrepareOrderUseCase):
    async def order_node(state: StockPilotState) -> StockPilotState:
        quantity = state.get("quantity")
        result = await prepare_order.execute(
            product_name=state.get("product_name"),
            quantity=1 if quantity is None else quantity,
        )
        product_name = result.product_name
        status = result.status
        pending_order = None

        if status == PreparedOrderStatus.PENDING_CONFIRMATION:
            pending_order = {
                "operation_id": result.operation_id,
                "product_name": product_name or "",
                "quantity": result.quantity,
                "status": status.value,
            }
            if result.quantity == 1:
                response = (
                    f"La {product_name} est disponible avec "
                    f"{result.available_stock} unités en stock. "
                    f"Confirmez-vous la commande d’une {product_name} ?"
                )
            else:
                response = (
                    f"La {product_name} est disponible avec "
                    f"{result.available_stock} unités en stock. "
                    f"Confirmez-vous la commande de {result.quantity} "
                    f"{product_name} ?"
                )
        elif status == PreparedOrderStatus.OUT_OF_STOCK:
            response = f"La {product_name} est actuellement indisponible."
        elif status == PreparedOrderStatus.INSUFFICIENT_STOCK:
            response = (
                f"Il ne reste que {result.available_stock} unité(s) de "
                f"{product_name}, mais {result.quantity} sont demandées."
            )
        elif product_name:
            response = f"Je n'ai pas trouvé le produit {product_name}."
        else:
            response = "Je n'ai pas identifié le produit à commander."

        return {
            "pending_order": pending_order,
            "order_status": status.value,
            "quantity": result.quantity,
            "response": response,
        }

    return order_node


async def unknown_node(state: StockPilotState) -> StockPilotState:
    return {
        "response": "I could not determine the requested action."
    }
