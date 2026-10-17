"""Synthetic read-only catalogue for deterministic Advice evaluations and tests."""
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

from langgraph.checkpoint.memory import InMemorySaver

from stockpilot.application.dto.intent_result import IntentResult
from stockpilot.application.use_cases.catalogue_advice import CatalogueAdviceUseCase
from stockpilot.application.use_cases.classify_intent import ClassifyIntentUseCase
from stockpilot.application.use_cases.resolve_product import normalize_name
from stockpilot.application.workflows.stockpilot_workflow import build_graph
from stockpilot.domain.entities.product import Product
from stockpilot.domain.enums.intent import Intent
from stockpilot.domain.enums.product_category import ProductCategory as C


def catalogue():
    return [
        Product('D-A', 'Perceuse Atelier', C.DRILL, 60, 4),
        Product('D-B', 'Perceuse Compact', C.DRILL, 80, 2),
        Product('D-C', 'Perceuse Concurrence Synthétique V017', C.DRILL, 99, 5),
        Product('D-E', 'Perceuse Plafond', C.DRILL, 100, 3),
        Product('D-H', 'Perceuse Plus', C.DRILL, 120, 2),
        Product('D-O', 'Perceuse Rupture', C.DRILL, 40, 0),
        Product('D-P1', 'Perceuse Pro V017 Rouge', C.DRILL, 180, 1),
        Product('D-P2', 'Perceuse Pro V017 Bleue', C.DRILL, 190, 1),
        Product('S-A', 'Visseuse Atelier', C.SCREWDRIVER, 70, 3),
        Product('W-A', 'Scie Atelier', C.SAW, 50, 1),
    ]


class MemoryCatalogue:
    def __init__(self, products=None):
        self.products = catalogue() if products is None else products

    async def find_advice_candidates(self, criteria):
        return sorted((p for p in self.products if criteria.matches(p)), key=lambda p: (p.price, p.sku))[:criteria.limit]

    async def find_advice_by_sku(self, sku):
        products = [p for p in self.products if p.sku == sku]
        if len(products) > 1:
            raise ValueError('Duplicate SKU')
        return products[0] if products else None

    async def find_resolution_skus(self, sku):
        return [p for p in self.products if p.sku.casefold() == sku.casefold()][:2]

    async def find_resolution_candidates(self, name, *, exact, limit):
        name = normalize_name(name)
        return [p for p in self.products if (
            normalize_name(p.name) == name if exact else all(
                re.search(r'(?<!\w)' + re.escape(token) + r'(?!\w)', normalize_name(p.name))
                for token in name.split()))][:limit]


def advice_graph(repository=None, checkpointer=None, check_stock=None):
    repo = repository or MemoryCatalogue()
    llm = SimpleNamespace(
        # Deliberately malicious classifier: deterministic advice guards must win.
        classify_intent=AsyncMock(return_value=IntentResult(intent=Intent.ORDER, product_name='INVENTED', quantity=4, budget=1)),
        generate=AsyncMock(return_value='{"raisons":["prix","categorie","stock"]}'),
    )
    forbidden = SimpleNamespace(execute=AsyncMock(side_effect=AssertionError('Advice cannot mutate business data')))
    graph = build_graph(ClassifyIntentUseCase(llm), check_stock or forbidden, forbidden, forbidden, forbidden,
                        get_order_status=forbidden, catalogue_advice=CatalogueAdviceUseCase(repo, llm),
                        checkpointer=checkpointer or InMemorySaver())
    return graph, repo, llm, forbidden
