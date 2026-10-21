"""Reuse the historical lexical contracts, never its repositories or float prices."""

import re
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

from stockpilot.application.dto.advice_criteria import AdviceCriteria
from stockpilot.application.use_cases.catalogue_advice import (
    ClarificationRequired,
    criteria_context,
    extract_criteria,
    normalize,
    resolve_advice_reference,
)
from stockpilot.application.use_cases.resolve_product import (
    ResolutionStatus,
    ResolveProductUseCase,
)
from stockpilot.domain.entities.tenant_product import TenantProductQuery


class ScopedResolution:
    """Only the two literal lookup operations used by the existing resolver."""

    def __init__(self, catalogue, request, context):
        self.catalogue, self.request, self.context = catalogue, request, context

    async def find_resolution_skus(self, sku):
        product = self.catalogue.by_sku(self.request, self.context, sku)
        return [product] if product is not None and product.status == "active" else []

    async def find_resolution_candidates(self, reference, *, exact, limit):
        return self.catalogue.resolution_candidates(
            self.request, self.context, reference, exact=exact, limit=limit
        )


class TenantAssistantCatalogue:
    def __init__(self, catalogue):
        self.catalogue = catalogue

    def card(self, request, context, product):
        stock = self.catalogue.stock(request, context, product.product_id)
        if (
            stock is None
            or stock.organization_id != context.organization_id
            or product.organization_id != context.organization_id
        ):
            raise ClarificationRequired("Le produit ne peut pas être vérifié.")
        return {
            "product_id": product.product_id,
            "organization_id": product.organization_id,
            "sku": product.sku,
            "name": product.name,
            "price_minor": product.price_minor,
            "currency": product.currency,
            "quantity": stock.quantity,
            "reserved": stock.reserved,
            "available": stock.available,
            "revision": stock.revision,
            "observed_at": datetime.now(UTC).isoformat(),
        }

    async def resolve(self, request, context, reference, *, order=False):
        bridge = ScopedResolution(self.catalogue, request, context)
        resolved = (
            await ResolveProductUseCase(bridge).execute(None, reference)
            if order
            else await resolve_advice_reference(bridge, reference)
        )
        if resolved.status == ResolutionStatus.AMBIGUOUS:
            raise ClarificationRequired(
                "Référence ambiguë : précisez le nom complet ou le SKU exact."
            )
        if resolved.product is None:
            raise ClarificationRequired(
                "Produit introuvable : précisez son nom complet ou son SKU exact."
            )
        current = self.catalogue.by_sku(request, context, resolved.product.sku)
        if (
            current is None
            or current.product_id != resolved.product.product_id
            or current.name != resolved.product.name
        ):
            raise ClarificationRequired(
                "L’identité du produit a changé ; précisez à nouveau son SKU."
            )
        return current

    async def advice(self, request, context, question, state):
        previous = (
            AdviceCriteria(**state["criteria"]) if state.get("criteria") else None
        )
        criteria = extract_criteria(question, previous)
        prior_skus = state.get("advice_skus", [])[:5]
        reference = None
        if (
            re.search(
                r"\b(le premier|celui-ci|cette perceuse|ce produit)\b",
                normalize(question),
            )
            and not criteria.reference
        ):
            if not prior_skus or (
                "le premier" not in normalize(question) and len(prior_skus) != 1
            ):
                raise ClarificationRequired("Précisez le SKU du produit concerné.")
            criteria = replace(
                criteria,
                reference="SKU " + prior_skus[0],
                mode="compare" if criteria.mode == "compare" else "product",
            )
        if criteria.reference:
            reference = await self.resolve(request, context, criteria.reference)
            criteria = replace(criteria, reference="SKU " + reference.sku)
        if criteria.mode == "compare" and reference is None:
            raise ClarificationRequired(
                "Quel produit souhaitez-vous comparer ? Précisez son SKU."
            )
        maximum = None
        if criteria.maximum is not None:
            # The shared grammar accepts at most two decimal digits. Decimal(str)
            # translates that lexical bound; no product amount passes through float.
            maximum = int(Decimal(str(criteria.maximum)) * 100) - int(
                criteria.exclusive_maximum
            )
        if criteria.mode == "product":
            products = [reference]
        elif criteria.mode == "cheapest":
            products = [
                self.catalogue.by_sku(request, context, sku) for sku in prior_skus
            ]
            products = [
                p
                for p in products
                if p is not None
                and p.status == "active"
                and p.sku not in criteria.exclude_skus
                and (
                    criteria.category is None
                    or p.category.upper() == criteria.category.value
                )
                and (
                    maximum is None
                    or (p.currency == "EUR" and p.price_minor <= maximum)
                )
                and (
                    criteria.minimum is None
                    or (
                        p.currency == "EUR"
                        and p.price_minor >= int(Decimal(str(criteria.minimum)) * 100)
                    )
                )
                and (
                    criteria.available is None
                    or (
                        self.catalogue.stock(request, context, p.product_id).available
                        > 0
                    )
                    == criteria.available
                )
            ]
            if not products:
                return {
                    "intent": "PRODUCT_ADVICE",
                    "text": "Aucun produit ne correspond aux critères actuels.",
                    "products": [],
                    "criteria": criteria_context(criteria),
                    "advice_skus": [],
                }
            if len({p.currency for p in products}) != 1:
                raise ClarificationRequired(
                    "Commencez par une liste dans une seule devise."
                )
            products = sorted(products, key=lambda p: (p.price_minor, p.sku))[:1]
        else:
            categories = (
                [criteria.category.value, criteria.category.value.lower()]
                if criteria.category
                else [None]
            )
            if (
                reference
                and criteria.mode == "compare"
                and not criteria.category_explicit
            ):
                categories = [reference.category]
            candidates = {}
            for category in dict.fromkeys(categories):
                page = self.catalogue.search(
                    request,
                    context,
                    TenantProductQuery(
                        limit=5,
                        category=category,
                        minimum_price_minor=int(Decimal(str(criteria.minimum)) * 100)
                        if criteria.minimum is not None
                        else None,
                        maximum_price_minor=maximum,
                        currency="EUR"
                        if maximum is not None
                        else reference.currency
                        if reference
                        else None,
                        available=criteria.available,
                        status="active",
                        sort_by_price=True,
                    ),
                )
                for item in page.items:
                    if item.sku not in criteria.exclude_skus and (
                        reference is None or item.product_id != reference.product_id
                    ):
                        candidates[item.sku] = item
            products = sorted(
                candidates.values(), key=lambda p: (p.currency, p.price_minor, p.sku)
            )[: criteria.limit]
            if reference:
                products.insert(0, reference)
        cards = [self.card(request, context, p) for p in products[:5] if p is not None]
        outside = (
            reference is not None
            and maximum is not None
            and (reference.currency != "EUR" or reference.price_minor > maximum)
        )
        return {
            "intent": "COMPARISON" if criteria.mode == "compare" else "PRODUCT_ADVICE",
            "text": ("Fiche ciblée hors du budget précédent. " if outside else "")
            + (
                "Produits vérifiés dans votre organisation ; disponibilité observée, sans réservation."
                if cards
                else "Aucun produit ne correspond aux critères de votre organisation."
            ),
            "products": cards,
            "criteria": criteria_context(criteria),
            "advice_skus": [p["sku"] for p in cards],
        }
