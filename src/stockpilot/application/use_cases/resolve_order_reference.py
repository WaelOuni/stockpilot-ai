"""Disambiguate only the complete bare ``Commande <reference>`` form.

Identifiers have no reserved prefixes. Evidence is a literal catalogue match
and an owner-scoped order lookup, never the model's suggested identifier.
All preparation, quantity and confirmation checks remain downstream.
"""
import re
from dataclasses import dataclass

from stockpilot.application.ports.product_repository import ProductRepositoryPort
from stockpilot.application.ports.order_repository import OrderRepositoryPort
from stockpilot.application.order_quantity_evidence import inspect_quantity
from stockpilot.application.order_quantity_errors import InvalidOrderQuantityError
from stockpilot.domain.enums.intent import Intent

_BARE_REFERENCE = re.compile(r'\s*commande\s+(?P<reference>\S+)\s*', re.I)


@dataclass(frozen=True)
class OrderReferenceResolution:
    intent: Intent
    reference: str | None = None


class ResolveOrderReferenceUseCase:
    def __init__(self, products: ProductRepositoryPort, orders: OrderRepositoryPort):
        self.products = products
        self.orders = orders

    async def execute(
        self, message: str, customer_id: str | None,
    ) -> OrderReferenceResolution | None:
        if len(message) > 4096:
            return None
        match = _BARE_REFERENCE.fullmatch(message)
        if match is None:
            return None
        reference = match['reference']
        unclear = OrderReferenceResolution(Intent.UNKNOWN)
        if not customer_id or not customer_id.strip() or len(reference) > 256:
            return unclear
        # A bare count without a product is not evidence of a SKU, even if an
        # identically named numeric SKU exists. Preserve the quantity contract.
        try:
            evidence = inspect_quantity(message)
        except InvalidOrderQuantityError:
            # Malformed quantities must still reach the existing refusal path.
            return None
        if evidence.kind == 'explicit':
            return unclear
        products = await self.products.find_resolution_skus(reference)
        order = await self.orders.find_by_order_id_and_customer_id(reference, customer_id)
        # Do not turn repository errors into user errors or trust a broken port.
        if any(p.sku.casefold() != reference.casefold() for p in products):
            raise ValueError('Non-literal product reference returned by repository')
        if order is not None and (order.order_id != reference or order.customer_id != customer_id):
            raise ValueError('Non-owned order reference returned by repository')
        if len(products) == 1 and order is None:
            return OrderReferenceResolution(Intent.ORDER, products[0].sku)
        if not products and order is not None:
            return OrderReferenceResolution(Intent.ORDER_STATUS, reference)
        # Unknown reference, duplicate SKU or overlapping namespaces. No model
        # fallback, and no disclosure of another customer's order existence.
        return unclear
