import logging
from stockpilot.observability import Timer, bind_context, event, timed_event

from stockpilot.application.use_cases.catalogue_advice import extract_criteria, ClarificationRequired
from stockpilot.application.use_cases.classify_intent import ClassifyIntentUseCase
from stockpilot.application.dto.intent_result import IntentResult
from stockpilot.application.use_cases.resolve_context import (
    ConversationContext, ContextResolutionUseCase,
)
from stockpilot.domain.enums.intent import Intent
from stockpilot.application.order_transaction_errors import (
    OrderOutcomeUnknownError,
    OrderTransactionFailedError,
)
from stockpilot.application.order_quantity_errors import InvalidOrderQuantityError
from stockpilot.application.order_quantity_evidence import (
    concordant_quantity, strict_quantity, validate_evidence,
    quantitative_continuation, explicit_order_request, inspect_quantity,
)
from stockpilot.application.workflows.state import StockPilotState
from stockpilot.application.use_cases.check_stock import CheckStockUseCase
from stockpilot.application.use_cases.finalize_order import FinalizeOrderUseCase
from stockpilot.application.use_cases.get_order_status import GetOrderStatusUseCase
from stockpilot.application.use_cases.prepare_order import PrepareOrderUseCase
from stockpilot.application.use_cases.recommend_product import RecommendProductUseCase
from stockpilot.application.workflows.order_confirmation import (
    normalize_order_confirmation, confirmation_proposal, has_active_confirmation,
)
from stockpilot.domain.enums.order_status import OrderStatus, PreparedOrderStatus


logger = logging.getLogger(__name__)


def create_classify_node(classify_intent: ClassifyIntentUseCase, resolve_order_reference=None):
    async def classify_node(state: StockPilotState) -> StockPilotState:
        with timed_event(logger, "classification_started", "classification_completed",
                         "classification_failed"):
            result = await classify_intent.execute(state["message"])
        # A literal supported ORDER request cannot leave an older preparation
        # confirmable just because the model labels its invalid quantity UNKNOWN.
        if explicit_order_request(state["message"]):
            try:
                inspect_quantity(state["message"])
            except InvalidOrderQuantityError:
                result.intent = Intent.ORDER
            else:
                if result.intent == Intent.UNKNOWN:
                    result.intent = Intent.ORDER
        reference_update = {"reference_clarification": False}
        if resolve_order_reference is not None:
            reference = await resolve_order_reference.execute(state["message"], state.get("customer_id"))
            if reference is not None:
                result.intent = reference.intent
                result.order_id = reference.reference if reference.intent == Intent.ORDER_STATUS else None
                result.raw_product = reference.reference if reference.intent == Intent.ORDER else None
                result.product_name = result.raw_product
                reference_update.update(queried_order_id=None, queried_order_status=None,
                                        order_status_result=None)
                if reference.intent == Intent.UNKNOWN:
                    reference_update.update(
                        reference_clarification=True, last_relevant_intent=None,
                        last_product_name=None, last_quantity=None,
                        last_order_quantity_evidence=None, inherited_quantity_evidence=None,
                        last_product_sku=None, last_product_document_id=None,
                    )
                    result.quantity = None
                    if (state.get("pending_order") or {}).get("status") == PreparedOrderStatus.PENDING_CONFIRMATION.value:
                        reference_update.update(pending_order=None, order_status=None)
        bind_context(intent=result.intent.value)
        event(logger, "intent_detected", intent=result.intent.value)

        return {
            # Every non-confirmation turn retires the previous proposal. Only
            # successful preparation below can arm a new exact operation.
            "active_order_confirmation": None,
            **reference_update,
            "recommendations": [],
            "advice_status": None,
            "intent": result.intent,
            "raw_product": result.raw_product,
            "product_name": result.product_name,
            "category": result.category,
            "budget": result.budget,
            "usage": result.usage,
            "quantity": result.quantity,
            "order_id": result.order_id,
        }

    return classify_node


def create_context_resolution_node(resolve_context: ContextResolutionUseCase):
    async def context_node(state: StockPilotState) -> StockPilotState:
        pending = state.get("pending_order") or {}
        armed = (
            pending.get("status") == PreparedOrderStatus.PENDING_CONFIRMATION.value
            and state.get("order_status") == PreparedOrderStatus.PENDING_CONFIRMATION.value
            and pending.get("customer_id") == state.get("customer_id")
        )
        complete_request = explicit_order_request(state["message"])
        if armed and (complete_request or quantitative_continuation(state["message"])):
            try:
                inspect_quantity(state["message"])
                if state.get("intent") not in (Intent.ORDER, Intent.UNKNOWN):
                    raise InvalidOrderQuantityError(
                        "La demande de commande est incertaine. Précisez le SKU et la quantité "
                        "pour préparer à nouveau la commande."
                    )
                if not complete_request and (state.get("last_relevant_intent") != Intent.ORDER
                        or state.get("context_customer_id") != state.get("customer_id")
                        or not state.get("last_product_name")):
                    raise InvalidOrderQuantityError(
                        "La quantité a changé, mais le contexte produit n'est plus fiable. "
                        "Précisez le SKU et la quantité pour préparer à nouveau la commande."
                    )
            except InvalidOrderQuantityError as error:
                return {
                    **_invalid_quantity(error), "quantity_validation_failed": True,
                    "last_relevant_intent": None,
                    "context_customer_id": state.get("customer_id"),
                }
        previous = ConversationContext()
        owner_matches = state.get("context_customer_id") == state.get("customer_id")
        order_finished = (
            state.get("last_relevant_intent") == Intent.ORDER
            and state.get("order_status") in {
                PreparedOrderStatus.CONFIRMED.value, PreparedOrderStatus.CANCELLED.value,
                PreparedOrderStatus.ORDER_OUTCOME_UNKNOWN.value,
                PreparedOrderStatus.ORDER_CREATION_FAILED.value,
                PreparedOrderStatus.RESERVATION_FAILED.value,
                PreparedOrderStatus.PRODUCT_IDENTITY_REQUIRED.value,
            }
        )
        if owner_matches and not order_finished:
            previous = ConversationContext(
                intent=state.get("last_relevant_intent"),
                product_name=state.get("last_product_name"), quantity=state.get("last_quantity"),
                category=state.get("last_category"), budget=state.get("last_budget"),
                usage=state.get("last_usage"), advice_query=state.get("last_advice_query"),
            )
        result = resolve_context.execute(
            state["message"],
            IntentResult(**{key: state.get(key) for key in IntentResult.model_fields}),
            previous,
        )
        if result.quantity_error is not None:
            return {
                **result.classification.model_dump(),
                **_invalid_quantity(InvalidOrderQuantityError(result.quantity_error)),
                "quantity_validation_failed": True,
                "last_relevant_intent": None,
                "context_customer_id": state.get("customer_id"),
            }
        inherited_evidence = None
        if result.quantity_from_context:
            inherited_evidence = state.get("last_order_quantity_evidence")
        return {
            **result.classification.model_dump(),
            **({"pending_order": None, "order_status": None}
               if pending.get("status") == PreparedOrderStatus.PENDING_CONFIRMATION.value
               and result.classification.intent not in (Intent.ORDER, Intent.ORDER_STATUS) else {}),
            "quantity_validation_failed": False,
            "inherited_quantity_evidence": inherited_evidence,
            "product_from_context": result.product_from_context,
            "product_resolution_reference": (
                state.get("last_product_sku") if result.product_from_context and previous.intent == Intent.ORDER
                else None
            ),
            "product_resolution_document_id": (
                state.get("last_product_document_id") if result.product_from_context and previous.intent == Intent.ORDER
                else None
            ),
            "last_product_document_id": (
                state.get("last_product_document_id") if result.product_from_context and previous.intent == Intent.ORDER
                else None
            ),
            "last_product_sku": (
                state.get("last_product_sku") if result.product_from_context and previous.intent == Intent.ORDER
                else None
            ),
            "last_relevant_intent": result.context.intent,
            "last_product_name": result.context.product_name,
            "last_quantity": result.context.quantity,
            "last_category": result.context.category,
            "last_budget": result.context.budget,
            "last_usage": result.context.usage,
            "last_advice_query": result.context.advice_query,
            "context_customer_id": state.get("customer_id"),
            "advice_query": result.advice_query,
        }

    return context_node


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
        is_confirmed = (
            pending_order is not None
            and pending_order.get("status") == PreparedOrderStatus.CONFIRMED.value
            and state.get("order_status") == PreparedOrderStatus.CONFIRMED.value
        )
        decision = (
            normalize_order_confirmation(state["message"])
            if is_pending or is_confirmed
            else None
        )

        if decision is None or (is_confirmed and decision != "confirmed"):
            # Persist retirement before classification/provider calls. A failed
            # provider must not leave an old proposal armed in the checkpoint.
            return {
                "confirmation_handled": False,
                **({"active_order_confirmation": None}
                   if state.get("active_order_confirmation") is not None else {}),
            }

        if pending_order.get("customer_id") != state.get("customer_id"):
            raise ValueError("La commande en attente appartient à un autre client.")

        if is_confirmed:
            # A finished operation can only be recovered, never reserved again.
            quantity = pending_order.get("quantity")
            order_id = state.get("created_order_id")
            if (isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1
                    or not isinstance(order_id, str) or not order_id.strip()):
                raise ValueError("Le checkpoint de confirmation est incohérent.")
            try:
                existing = await finalize_order.execute(
                    pending_order["product_name"], quantity,
                    operation_id=pending_order["operation_id"],
                    customer_id=state.get("customer_id"),
                    product_sku=pending_order.get("product_sku"),
                    product_document_id=pending_order.get("product_document_id"),
                    replay_only=True,
                )
            except OrderOutcomeUnknownError:
                existing = None
            if existing is None:
                return {
                    "active_order_confirmation": None,
                    "intent": Intent.ORDER,
                    "queried_order_id": None, "queried_order_status": None,
                    "order_status_result": None,
                    "pending_order": {**pending_order, "status": PreparedOrderStatus.ORDER_OUTCOME_UNKNOWN.value},
                    "order_status": PreparedOrderStatus.ORDER_OUTCOME_UNKNOWN.value,
                    "confirmation_handled": True, "created_order_id": None,
                    "response": "Impossible de vérifier cette confirmation. Aucune nouvelle réservation n'a été effectuée.",
                }
            if (existing.order_id != order_id
                    or existing.operation_id != pending_order["operation_id"]):
                raise ValueError("L'identité de la commande confirmée ne correspond pas au checkpoint.")
            status = (PreparedOrderStatus.CANCELLED if existing.status == OrderStatus.CANCELLED
                      else PreparedOrderStatus.CONFIRMED)
            return {
                "active_order_confirmation": None,
                "intent": Intent.ORDER,
                "queried_order_id": None, "queried_order_status": None,
                "order_status_result": None,
                "pending_order": {**pending_order, "status": status.value},
                "order_status": status.value, "confirmation_handled": True,
                "created_order_id": existing.order_id, "last_created_order_id": existing.order_id,
                "response": (
                    f"La commande {existing.order_id} est déjà annulée. Aucune nouvelle réservation n'a été effectuée."
                    if status == PreparedOrderStatus.CANCELLED else
                    f"La commande {existing.order_id} est déjà confirmée. Aucune nouvelle réservation n'a été effectuée."
                ),
            }

        updated_order = dict(pending_order)
        if decision == "confirmed":
            product_name = str(updated_order["product_name"])
            try:
                quantity = strict_quantity(updated_order.get("quantity"))
                if updated_order.get("quantity_evidence") is not None:
                    if validate_evidence(updated_order["quantity_evidence"]).quantity != quantity:
                        raise InvalidOrderQuantityError("La quantité du checkpoint a changé.")
            except InvalidOrderQuantityError as error:
                return {**_invalid_quantity(error), "confirmation_handled": True}
            operation_id = str(updated_order["operation_id"])
            eligible = has_active_confirmation(state, updated_order)
            bind_context(operation_id=operation_id, intent="ORDER")
            event(logger, "order_confirmation_received",
                  product_name=product_name, quantity=quantity)
            timer = Timer()
            try:
                created_order = await finalize_order.execute(
                    product_name, quantity, operation_id=operation_id,
                    customer_id=state.get("customer_id"),
                    **({"product_sku": updated_order["product_sku"]} if updated_order.get("product_sku") else {}),
                    **({"product_document_id": updated_order["product_document_id"]} if updated_order.get("product_document_id") else {}),
                    **({"replay_only": True} if not (eligible and updated_order.get("product_sku") and updated_order.get("product_document_id") and updated_order.get("quantity_evidence")) else {}),
                )
            except OrderOutcomeUnknownError:
                event(logger, "order_outcome_unknown", level=logging.CRITICAL,
                      duration_ms=timer.duration_ms, exc_info=True)
                status = PreparedOrderStatus.ORDER_OUTCOME_UNKNOWN
                updated_order["status"] = status.value
                return {
                    "active_order_confirmation": None,
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
                    "active_order_confirmation": None,
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
                if not eligible or not updated_order.get("product_sku") or not updated_order.get("product_document_id") or not updated_order.get("quantity_evidence"):
                    status = PreparedOrderStatus.PRODUCT_IDENTITY_REQUIRED
                    updated_order["status"] = status.value
                    return {
                        "active_order_confirmation": None,
                        "pending_order": updated_order, "order_status": status.value,
                        "confirmation_handled": True, "created_order_id": None,
                        "response": "Cette proposition n'est plus active ou vérifiable. Précisez le SKU et la quantité pour préparer à nouveau la commande. Aucune nouvelle réservation n'a été effectuée.",
                    }
                status = PreparedOrderStatus.RESERVATION_FAILED
                updated_order["status"] = status.value
                return {
                    "active_order_confirmation": None,
                    "pending_order": updated_order,
                    "order_status": status.value,
                    "confirmation_handled": True,
                    "created_order_id": None,
                    "response": (
                        f"La réservation de {product_name} ne peut plus être confirmée "
                        "avec cette identité et cette quantité. Préparez à nouveau la commande."
                    ),
                }
            order_status = (
                PreparedOrderStatus.CANCELLED
                if created_order.status == OrderStatus.CANCELLED
                else PreparedOrderStatus.CONFIRMED
            )
            updated_order["status"] = order_status.value
            bind_context(order_id=created_order.order_id)
            event(logger, "order_finalized", order_id=created_order.order_id,
                  operation_id=operation_id, quantity=quantity,
                  product_name=product_name, order_status=order_status.value,
                  duration_ms=timer.duration_ms)
            if created_order.status == OrderStatus.CANCELLED:
                response = (
                    f"La commande {created_order.order_id} existe déjà, "
                    "mais elle est annulée. Aucune nouvelle commande n'a été créée."
                )
            else:
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

        result: StockPilotState = {
            "active_order_confirmation": None,
            "pending_order": updated_order,
            "order_status": order_status.value,
            "confirmation_handled": True,
            "created_order_id": created_order_id,
            "response": response,
        }
        if created_order_id is not None:
            result["last_created_order_id"] = created_order_id
        return result

    return resolve_order_confirmation_node


def route_after_confirmation(state: StockPilotState) -> str:
    if state.get("confirmation_handled"):
        return "end"
    return "classify"


def create_advice_node(
    recommend_product: RecommendProductUseCase,
    check_stock: CheckStockUseCase,
    catalogue_advice=None,
):
    async def advice_node(
        state: StockPilotState,
    ) -> StockPilotState:
        if catalogue_advice is not None:
            question = state["message"]
            # Reuse only user-authored evidence from a resolved same-owner follow-up,
            # never classifier-supplied budgets/categories in the generated summary.
            if state.get("advice_query") and state.get("last_advice_query"):
                question = state["last_advice_query"] + "\n" + question
            advice = await catalogue_advice.execute(question)
            try:
                criteria = extract_criteria(question)
            except ClarificationRequired:
                criteria = None
            # Do not expose model-invented single-product/constraint metadata beside
            # verified multi-product cards; subsequent context is grounded too.
            return {
                **advice, "product_name": None, "raw_product": None,
                "quantity": None, "usage": None, "last_product_name": None,
                "last_quantity": None, "last_usage": None,
                "category": criteria.category if criteria else None,
                "budget": criteria.maximum if criteria else None,
                "last_category": criteria.category if criteria else None,
                "last_budget": criteria.maximum if criteria else None,
            }
        recommendation = await recommend_product.execute(
            state.get("advice_query") or state["message"]
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


def _invalid_quantity(error: InvalidOrderQuantityError) -> StockPilotState:
    return {
        "active_order_confirmation": None,
        "order_status": PreparedOrderStatus.INVALID_QUANTITY.value,
        "pending_order": None, "created_order_id": None,
        "queried_order_id": None, "queried_order_status": None, "order_status_result": None,
        "last_product_sku": None, "last_product_document_id": None,
        "product_resolution_reference": None, "product_resolution_document_id": None,
        "last_product_name": None, "last_quantity": None,
        "last_order_quantity_evidence": None, "inherited_quantity_evidence": None,
        "response": str(error),
    }


def create_order_node(prepare_order: PrepareOrderUseCase):
    async def order_node(state: StockPilotState) -> StockPilotState:
        quantity = state.get("quantity")
        try:
            evidence = concordant_quantity(
                state["message"], quantity, state.get("inherited_quantity_evidence"),
            )
            result = await prepare_order.execute(
                product_name=state.get("product_name"),
                product_sku=state.get("product_resolution_reference"),
                product_document_id=state.get("product_resolution_document_id"),
                quantity=evidence.quantity,
                message=None if state.get("product_from_context") else state.get("message"),
            )
        except InvalidOrderQuantityError as error:
            return _invalid_quantity(error)
        product_name = result.product_name
        status = result.status
        pending_order = None

        if status == PreparedOrderStatus.PENDING_CONFIRMATION:
            pending_order = {
                "operation_id": result.operation_id,
                "product_name": product_name or "",
                **({"product_sku": result.product_sku} if result.product_sku else {}),
                **({"product_document_id": result.product_document_id} if result.product_document_id else {}),
                "quantity": result.quantity,
                "quantity_evidence": evidence.to_dict(),
                "status": status.value,
                **({"customer_id": state["customer_id"]} if state.get("customer_id") is not None else {}),
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
        elif status == PreparedOrderStatus.AMBIGUOUS:
            response = "Plusieurs produits correspondent. Précisez le SKU ou le nom complet du produit."
        elif status == PreparedOrderStatus.PRODUCT_IDENTITY_REQUIRED:
            response = "Le produit préparé a changé. Précisez à nouveau le SKU pour préparer une nouvelle commande."
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
            "intent": Intent.ORDER,
            "last_relevant_intent": Intent.ORDER,
            "context_customer_id": state.get("customer_id"),
            "active_order_confirmation": (
                confirmation_proposal(pending_order) if pending_order is not None else None
            ),
            "pending_order": pending_order,
            "order_status": status.value,
            "last_quantity": result.quantity,
            "last_order_quantity_evidence": evidence.to_dict(),
            "last_product_name": result.product_name,
            "last_product_sku": result.product_sku,
            "last_product_document_id": result.product_document_id,
            "quantity": result.quantity,
            "response": response,
        }

    return order_node


def create_order_status_node(get_order_status: GetOrderStatusUseCase):
    async def order_status_node(state: StockPilotState) -> StockPilotState:
        order_id = (
            state.get("order_id")
            or state.get("last_created_order_id")
            or state.get("created_order_id")
        )
        result = await get_order_status.execute(order_id, customer_id=state.get("customer_id"))
        return {
            "queried_order_id": result.order_id,
            "queried_order_status": result.status.value if result.status else None,
            "order_status_result": result.result,
            "response": result.response,
        }

    return order_status_node


async def unknown_node(state: StockPilotState) -> StockPilotState:
    return {
        "response": (
            "Précisez si vous souhaitez acheter un produit (avec son SKU et la quantité) "
            "ou consulter le statut d’une commande (avec son identifiant)."
            if state.get("reference_clarification") else
            "I could not determine the requested action."
        )
    }
