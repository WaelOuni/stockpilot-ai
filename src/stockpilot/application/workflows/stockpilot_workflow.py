from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from stockpilot.application.use_cases.finalize_order import FinalizeOrderUseCase
from stockpilot.application.use_cases.get_order_status import GetOrderStatusUseCase
from stockpilot.application.use_cases.classify_intent import ClassifyIntentUseCase
from stockpilot.application.use_cases.check_stock import CheckStockUseCase
from stockpilot.application.use_cases.prepare_order import PrepareOrderUseCase
from stockpilot.application.use_cases.recommend_product import RecommendProductUseCase
from stockpilot.application.use_cases.resolve_context import ContextResolutionUseCase
from stockpilot.application.workflows.nodes import (
    create_advice_node,
    create_classify_node,
    create_context_resolution_node,
    create_order_confirmation_node,
    create_order_node,
    create_order_status_node,
    create_stock_node,
    route_after_confirmation,
    unknown_node,
)
from stockpilot.application.workflows.router import route_by_intent
from stockpilot.application.workflows.state import StockPilotState


def build_graph(
    classify_intent: ClassifyIntentUseCase,
    check_stock: CheckStockUseCase,
    recommend_product: RecommendProductUseCase,
    prepare_order: PrepareOrderUseCase,
    finalize_order: FinalizeOrderUseCase,
    checkpointer: BaseCheckpointSaver | None = None,
    *,
    get_order_status: GetOrderStatusUseCase,
    resolve_context: ContextResolutionUseCase | None = None,
    catalogue_advice=None,
    resolve_order_reference=None,
):
    graph = StateGraph(StockPilotState)

    graph.add_node(
        "resolve_order_confirmation",
        create_order_confirmation_node(
            finalize_order,
        ),
    )
    graph.add_node("classify", create_classify_node(classify_intent, resolve_order_reference))
    graph.add_node(
        "resolve_context", create_context_resolution_node(resolve_context or ContextResolutionUseCase()),
    )
    graph.add_node(
        "advice",
        create_advice_node(recommend_product, check_stock, catalogue_advice),
    )
    graph.add_node("stock", create_stock_node(check_stock))
    graph.add_node("order", create_order_node(prepare_order))
    graph.add_node("order_status", create_order_status_node(get_order_status))
    graph.add_node("unknown", unknown_node)

    graph.add_edge(START, "resolve_order_confirmation")
    graph.add_conditional_edges(
        "resolve_order_confirmation",
        route_after_confirmation,
        {
            "end": END,
            "classify": "classify",
        },
    )

    graph.add_edge("classify", "resolve_context")
    graph.add_conditional_edges(
        "resolve_context",
        route_by_intent,
        {
            "advice": "advice",
            "stock": "stock",
            "order": "order",
            "order_status": "order_status",
            "unknown": "unknown",
            "end": END,
        },
    )

    graph.add_edge("advice", END)
    graph.add_edge("stock", END)
    graph.add_edge("order", END)
    graph.add_edge("order_status", END)
    graph.add_edge("unknown", END)

    return graph.compile(checkpointer=checkpointer)
