"""Tenant LangGraph composition. Mongo ORDER is the only stock mutation engine."""

import hashlib
import json
import re
import threading
from dataclasses import asdict
from datetime import datetime
from enum import Enum
from time import perf_counter
from typing import TypedDict

import httpx
from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from stockpilot.application.order_identifier import is_explicit_operation_id
from stockpilot.application.order_quantity_errors import InvalidOrderQuantityError
from stockpilot.application.order_quantity_evidence import (
    concordant_quantity,
    explicit_order_request,
)
from stockpilot.application.ports.tenant_knowledge import (
    TenantDataConflict,
    TenantDataNotFound,
    TenantDataUnavailable,
)
from stockpilot.application.ports.tenant_orders import (
    TenantOrderConflict,
    TenantOrderNotFound,
)
from stockpilot.application.resource_errors import (
    ModelInputLimitError,
    ModelTimeoutError,
    ModelUnavailableError,
)
from stockpilot.application.services.identity_authorization import AuthorizationDenied
from stockpilot.application.services.tenant_assistant_catalogue import (
    TenantAssistantCatalogue,
)
from stockpilot.application.use_cases.catalogue_advice import (
    ClarificationRequired,
    is_advice_followup,
    is_catalogue_advice,
    normalize,
)
from stockpilot.domain.entities.tenant_knowledge import text
from stockpilot.domain.entities.tenant_order import (
    CancelTenantOrder,
    ConfirmTenantOrder,
    PrepareTenantOrder,
    RequestedLine,
)
from stockpilot.domain.enums.permission import Permission as P


class AssistantBusy(Exception):
    pass


class AssistantPending(TenantDataUnavailable):
    """Original persisted turn/action must be reconciled; never generate another key."""


def primitive(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {k: primitive(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [primitive(v) for v in value]
    return value


def order_snapshot(order):
    return primitive(asdict(order))


def operation_key(context, conversation_id, message_id):
    return hashlib.sha256(
        json.dumps(
            [
                context.organization_id,
                context.principal.user_id,
                conversation_id,
                message_id,
                "prepare",
            ],
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


class AssistantState(TypedDict, total=False):
    question: str
    turn_id: str
    customer_id: str | None
    criteria: dict | None
    advice_skus: list[str]
    last_order_id: str | None
    preparation_armed: bool
    reply: dict


class TenantAssistant:
    def __init__(self, store, catalogue, orders, retrieval, llm, *, concurrency=1):
        self.store, self.orders, self.retrieval, self.llm = (
            store,
            orders,
            retrieval,
            llm,
        )
        self.catalogue = TenantAssistantCatalogue(catalogue)
        self.admission = threading.BoundedSemaphore(concurrency)

    def current(self, request, context):
        current = self.store.current(request, context, P.CONVERSATION_OWN)
        for permission in (P.CATALOGUE_READ, P.STOCK_READ, P.RAG_READ):
            self.store.current(request, context, permission)
        return current

    def _reply(self, intent, message, **fields):
        return {
            "intent": intent,
            "text": message,
            "products": [],
            "order": None,
            "can_confirm": False,
            **fields,
        }

    def _graph(self, request, context, conversation_id, saver):
        async def understand(state):
            started = perf_counter()
            self.current(request, context)
            try:
                classification = await self.llm.classify_intent(state["question"])
            except (
                ModelTimeoutError,
                ModelUnavailableError,
                ModelInputLimitError,
                httpx.HTTPError,
                TimeoutError,
                ValueError,
                ValidationError,
            ):
                raise TenantDataUnavailable(
                    "Assistant model unavailable or invalid structured response."
                ) from None
            return {
                "reply": {
                    "classification": primitive(classification.model_dump()),
                    "classification_ms": round((perf_counter() - started) * 1000, 2),
                }
            }

        async def answer(state):
            started = perf_counter()
            reply = await self._answer(request, context, conversation_id, state)
            self.current(request, context)
            reply["timing_ms"] = {
                "classification": state["reply"]["classification_ms"],
                "processing": round((perf_counter() - started) * 1000, 2),
            }
            return {
                "reply": reply,
                "criteria": reply.get("criteria", state.get("criteria")),
                "advice_skus": reply.get("advice_skus", state.get("advice_skus", [])),
                "last_order_id": reply["order"]["order_id"]
                if reply.get("order")
                else state.get("last_order_id"),
                "preparation_armed": reply.get("can_confirm", False),
            }

        graph = StateGraph(AssistantState)
        graph.add_node("understand", understand)
        graph.add_node("answer", answer)
        graph.add_edge(START, "understand")
        graph.add_edge("understand", "answer")
        graph.add_edge("answer", END)
        return graph.compile(checkpointer=saver)

    async def _answer(self, request, context, conversation_id, state):
        question = state["question"]
        value = normalize(question).strip()
        classification = state["reply"]["classification"]
        try:
            if (
                re.search(r"\b(annul\w*|statut|ou en est|derniere commande)\b", value)
                or classification["intent"] == "ORDER_STATUS"
            ):
                order_id = classification.get("order_id")
                if (
                    not order_id
                    or order_id not in question
                    or is_explicit_operation_id(question, order_id)
                ):
                    order_id = state.get("last_order_id")
                if not order_id:
                    raise ClarificationRequired(
                        "Précisez une commande de cette conversation ou son identifiant exact."
                    )
                order = self.orders.get(request, context, order_id)
                return self._reply(
                    "CANCEL_REQUEST" if "annul" in value else "ORDER_STATUS",
                    "État courant vérifié. Toute annulation exige une action distincte.",
                    order=order_snapshot(order),
                )
            if value in {"oui", "oui je confirme", "je confirme", "confirmer"}:
                order = (
                    self.orders.get(request, context, state["last_order_id"])
                    if state.get("last_order_id")
                    else None
                )
                previous = self.store.history(request, context, conversation_id, 2)[
                    "items"
                ]
                preparation = next(
                    (
                        t
                        for t in reversed(previous)
                        if t["turn_id"] != state["turn_id"]
                        and t.get("action")
                        and t["action"]["namespace"] == "prepare"
                    ),
                    None,
                )
                if preparation and order and state.get("preparation_armed"):
                    self.store.plan(
                        request,
                        context,
                        conversation_id,
                        state["turn_id"],
                        preparation["action"],
                    )
                return self._reply(
                    "CONFIRMATION_REQUIRED",
                    "Le texte ne réserve aucun stock. Utilisez le bouton de confirmation du récapitulatif.",
                    order=order_snapshot(order) if order else None,
                    can_confirm=bool(
                        order
                        and order.status.value == "prepared"
                        and state.get("preparation_armed")
                    ),
                )
            # A complete generic buying enquiry has no exact identity or ORDER
            # quantity. Only this bounded category phrase is routed to discovery.
            purchase = re.fullmatch(
                r"(?:je (?:voudrais|souhaite|veux)|j'aimerais) acheter "
                r"(?:une?|des) (perceuses?|visseuses?|scies?|ponceuses?)[?!.]?",
                value,
            )
            catalogue_question = "Je cherche " + purchase[1] if purchase else question
            if explicit_order_request(question) and not purchase:
                product = await self.catalogue.resolve(
                    request, context, question, order=True
                )
                # Bind the complete literal catalogue name before treating its
                # numeric model tokens as identity. The untouched quantity prefix
                # still goes through the shared strict evidence parser.
                named = re.search(
                    r"(?<![\w-])" + re.escape(product.name) + r"\s*[?!.]?\s*$",
                    question,
                    re.IGNORECASE,
                )
                quantity_question = (
                    question[: named.start()] + "SKU " + product.sku
                    if named and re.match(r"[^\W\d_]", product.name)
                    else question
                )
                evidence = concordant_quantity(
                    quantity_question, classification.get("quantity")
                )
                if P.ORDER_CREATE not in context.principal.permissions:
                    raise AuthorizationDenied("Order creation permission required.")
                if not state.get("customer_id"):
                    raise ClarificationRequired(
                        "Sélectionnez un acheteur autorisé avant de préparer la commande."
                    )
                action = {
                    "namespace": "prepare",
                    "operation_id": operation_key(
                        context, conversation_id, state["turn_id"]
                    ),
                    "customer_id": state["customer_id"],
                    "lines": [{"sku": product.sku, "quantity": evidence.quantity}],
                    "session": context.authorization_context_id,
                }
                self.store.plan(
                    request, context, conversation_id, state["turn_id"], action
                )
                order = self.orders.prepare(
                    request,
                    context,
                    PrepareTenantOrder(
                        action["operation_id"],
                        action["customer_id"],
                        (RequestedLine(product.sku, evidence.quantity),),
                    ),
                )
                return self._reply(
                    "ORDER_PREPARE",
                    "Préparation enregistrée. Aucun stock réservé. Vérifiez puis confirmez explicitement.",
                    order=order_snapshot(order),
                    can_confirm=True,
                )
            from stockpilot.application.use_cases.catalogue_advice import (
                extract_criteria,
            )

            try:
                lexical = (
                    extract_criteria(catalogue_question)
                    if len(catalogue_question) <= 256
                    else None
                )
            except ClarificationRequired:
                lexical = None
            if (
                purchase is not None
                or is_catalogue_advice(catalogue_question)
                or is_advice_followup(question)
                or (lexical is not None and lexical.category_explicit)
                or (lexical is not None and lexical.maximum is not None)
                or (lexical is not None and lexical.minimum is not None)
                or classification["intent"] == "PRODUCT_ADVICE"
            ):
                result = await self.catalogue.advice(
                    request, context, catalogue_question, state
                )
                return self._reply(
                    result["intent"],
                    result["text"],
                    **{k: v for k, v in result.items() if k not in {"intent", "text"}},
                )
            if classification["intent"] == "STOCK_CHECK" or (
                lexical is not None
                and lexical.reference
                and lexical.available is not None
            ):
                # A model name is a hint only; exact literal resolution still uses the user phrase.
                reference = lexical.reference if lexical is not None else None
                if not reference:
                    reference = classification.get("product_name")
                    if reference and normalize(reference) not in value:
                        reference = None
                if not reference:
                    raise ClarificationRequired(
                        "Précisez le SKU exact pour vérifier le stock."
                    )
                product = await self.catalogue.resolve(request, context, reference)
                return self._reply(
                    "STOCK_CHECK",
                    "Stock vérifié sans réservation.",
                    products=[self.catalogue.card(request, context, product)],
                )
            # A model ORDER without the explicit lexical request is never a write.
            if classification["intent"] == "ORDER":
                raise ClarificationRequired(
                    "Précisez explicitement la quantité et le SKU pour préparer une commande."
                )
            timings = {}
            answer = await self.retrieval.execute(
                request, context, question, timings=timings
            )
            return self._reply(
                "RAG",
                answer
                or "Aucun passage documentaire compatible n’est disponible dans votre organisation.",
                rag_timing_ms=timings,
            )
        except TenantOrderConflict:
            return self._reply(
                "ORDER_REFUSED",
                "La préparation est refusée : vérifiez les prix, le stock, la session et la référence avant de préparer à nouveau.",
            )
        except (ClarificationRequired, InvalidOrderQuantityError) as error:
            return self._reply("CLARIFICATION_REQUIRED", str(error))

    def _public_turn(self, request, context, turn):
        reply = turn["reply"]
        if reply and reply.get("order"):
            try:
                self.orders.get(request, context, reply["order"]["order_id"])
            except TenantOrderNotFound:
                reply = self._reply(
                    "RESOURCE_UNAVAILABLE", "La commande n’est plus accessible."
                )
            else:
                reply = {
                    **reply,
                    "can_confirm": bool(
                        reply.get("can_confirm")
                        and turn.get("action")
                        and turn["action"].get("session")
                        == context.authorization_context_id
                    ),
                }
        action = turn.get("action")
        return {
            "turn_id": turn["turn_id"],
            "kind": turn["kind"],
            "message": turn["input"].get("message", ""),
            "status": turn["status"],
            "reply": reply,
            "created_at": turn["created_at"],
            "operation": {k: action[k] for k in ("namespace", "operation_id")}
            if action
            else None,
        }

    async def send(
        self, request, context, conversation_id, turn_id, message, customer_id=None
    ):
        text(message, 4000)
        if not self.admission.acquire(blocking=False):
            raise AssistantBusy()
        try:
            self.current(request, context)
            with self.store.execution(request, context, conversation_id):
                self.store.begin(
                    request,
                    context,
                    conversation_id,
                    turn_id,
                    "message",
                    {"message": message, "customer_id": customer_id},
                )
                old = self.store.turn(request, context, conversation_id, turn_id)
                if old["status"] == "completed":
                    return self._public_turn(request, context, old)
                if old["action"]:
                    return await self._reconcile(request, context, conversation_id, old)
                saver = self.store.bind(request, context, conversation_id)
                result = await self._graph(
                    request, context, conversation_id, saver
                ).ainvoke(
                    {
                        "question": message,
                        "turn_id": turn_id,
                        "customer_id": customer_id,
                    },
                    saver.config(),
                )
                self.store.finish(
                    request, context, conversation_id, turn_id, result["reply"]
                )
                return self._public_turn(
                    request,
                    context,
                    self.store.turn(request, context, conversation_id, turn_id),
                )
        finally:
            self.admission.release()

    def _refused(self, request, context, conversation_id, turn_id):
        reply = self._reply(
            "ORDER_REFUSED",
            "Opération refusée. Vérifiez la commande et préparez à nouveau si nécessaire.",
        )
        self.store.finish(request, context, conversation_id, turn_id, reply)
        return self._public_turn(
            request,
            context,
            self.store.turn(request, context, conversation_id, turn_id),
        )

    async def _project(self, request, context, conversation_id, turn, order):
        action = turn["action"]
        reply = self._reply(
            "ORDER_PREPARE" if action["namespace"] == "prepare" else "ORDER_RESULT",
            "État de commande vérifié ; aucune nouvelle commande créée.",
            order=order_snapshot(order),
            can_confirm=action["namespace"] == "prepare"
            and order.status.value == "prepared"
            and action.get("session") == context.authorization_context_id,
        )
        saver = self.store.bind(request, context, conversation_id)
        graph = self._graph(request, context, conversation_id, saver)
        # No graph node executes here. A projection failure retains the write-ahead row.
        await graph.aupdate_state(
            saver.config(),
            {
                "last_order_id": order.order_id,
                "preparation_armed": reply["can_confirm"],
                "reply": reply,
            },
            as_node="answer",
        )
        self.store.finish(request, context, conversation_id, turn["turn_id"], reply)
        return self._public_turn(
            request,
            context,
            self.store.turn(request, context, conversation_id, turn["turn_id"]),
        )

    async def _reconcile(self, request, context, conversation_id, turn):
        action = turn["action"]
        try:
            order = self.orders.result(
                request, context, action["namespace"], action["operation_id"]
            )
        except TenantOrderNotFound:
            # 404 does NOT prove abort. Caller may explicitly replay the exact
            # persisted request below, never invent a new operation.
            raise AssistantPending("Original operation not yet reconciled.") from None
        return await self._project(request, context, conversation_id, turn, order)

    async def retry(self, request, context, conversation_id, turn_id):
        old = self.store.turn(request, context, conversation_id, turn_id)
        if old is None:
            raise TenantDataNotFound("Turn unavailable.")
        if old["status"] == "completed":
            return self._public_turn(request, context, old)
        if not old["action"]:
            return await self.send(
                request,
                context,
                conversation_id,
                turn_id,
                old["input"]["message"],
                old["input"].get("customer_id"),
            )
        with self.store.execution(request, context, conversation_id):
            action = old["action"]
            try:
                return await self._reconcile(request, context, conversation_id, old)
            except AssistantPending:
                pass
            try:
                # Explicit retry only, using the exact stored plan/key and fresh B2 rights.
                if action["namespace"] == "prepare":
                    order = self.orders.prepare(
                        request,
                        context,
                        PrepareTenantOrder(
                            action["operation_id"],
                            action["customer_id"],
                            tuple(RequestedLine(**line) for line in action["lines"]),
                        ),
                    )
                elif action["namespace"] == "confirm":
                    order = self.orders.confirm(
                        request,
                        context,
                        ConfirmTenantOrder(
                            action["operation_id"],
                            action["order_id"],
                            action["preparation_digest"],
                            True,
                        ),
                    )
                else:
                    order = self.orders.cancel(
                        request,
                        context,
                        CancelTenantOrder(action["operation_id"], action["order_id"]),
                    )
            except TenantOrderConflict:
                return self._refused(request, context, conversation_id, turn_id)
            return await self._project(request, context, conversation_id, old, order)

    async def resume(self, request, context, conversation_id):
        with self.store.execution(request, context, conversation_id):
            rows = self.store.history(request, context, conversation_id)["items"]
            if rows and rows[-1]["status"] == "pending" and rows[-1]["action"]:
                await self._reconcile(request, context, conversation_id, rows[-1])
        return self.read(request, context, conversation_id)

    def read(self, request, context, conversation_id, limit=20, offset=0):
        self.current(request, context)
        history = self.store.history(request, context, conversation_id, limit, offset)
        return {
            "conversation_id": conversation_id,
            **history,
            "items": [
                self._public_turn(request, context, turn) for turn in history["items"]
            ],
        }

    async def action(
        self,
        request,
        context,
        conversation_id,
        namespace,
        operation_id,
        order_id,
        preparation_digest=None,
    ):
        if namespace not in {"confirm", "cancel"}:
            raise ValueError("Explicit order action required.")
        with self.store.execution(request, context, conversation_id):
            body = {"order_id": order_id, "preparation_digest": preparation_digest}
            old = self.store.turn(request, context, conversation_id, operation_id)
            if old is None:
                history = self.store.history(request, context, conversation_id, 1)[
                    "items"
                ]
                last = history[-1] if history else None
                reply = (
                    last["reply"] if last and last["status"] == "completed" else None
                )
                if (
                    not reply
                    or not reply.get("order")
                    or reply["order"]["order_id"] != order_id
                ):
                    raise TenantDataConflict("Current conversation order required.")
                if namespace == "confirm" and not reply.get("can_confirm"):
                    raise TenantDataConflict("Preparation is not armed.")
            self.store.begin(
                request, context, conversation_id, operation_id, namespace, body
            )
            action = {
                "namespace": namespace,
                "operation_id": operation_id,
                **body,
                "session": old["action"]["session"]
                if old and old["action"]
                else context.authorization_context_id,
            }
            self.store.plan(request, context, conversation_id, operation_id, action)
            if old and old["status"] == "completed":
                return self._public_turn(request, context, old)
            try:
                # Always the existing B2 engine; no reservation logic exists in this graph.
                if namespace == "confirm":
                    order = self.orders.confirm(
                        request,
                        context,
                        ConfirmTenantOrder(
                            operation_id, order_id, preparation_digest, True
                        ),
                    )
                else:
                    order = self.orders.cancel(
                        request, context, CancelTenantOrder(operation_id, order_id)
                    )
            except TenantOrderConflict:
                return self._refused(request, context, conversation_id, operation_id)
            turn = self.store.turn(request, context, conversation_id, operation_id)
            return await self._project(request, context, conversation_id, turn, order)
