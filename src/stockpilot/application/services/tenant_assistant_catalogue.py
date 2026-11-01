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
from stockpilot.application.services.tenant_catalogue_discovery import (
    custom_category_question,
    low_stock_criteria,
    persisted_categories,
)


class ScopedResolution:
    """Only the two literal lookup operations used by the existing resolver."""

    def __init__(self, catalogue, request, context):
        self.catalogue, self.request, self.context = catalogue, request, context

    async def find_resolution_skus(self, sku):
        # The shared resolver also probes the complete natural-language phrase.
        # B1 exact keys are bounded to 128 characters; longer text can still be
        # a literal product name. Do not truncate it or weaken the store guard.
        if len(sku) > 128:
            return []
        product = self.catalogue.by_sku(self.request, self.context, sku)
        return [product] if product is not None and product.status == "active" else []

    async def find_resolution_candidates(self, reference, *, exact, limit):
        return self.catalogue.resolution_candidates(
            self.request, self.context, reference, exact=exact, limit=limit
        )


class TenantAssistantCatalogue:
    def __init__(self, catalogue):
        self.catalogue = catalogue

    @staticmethod
    def criteria(question, previous):
        # A generic workshop context is not a product-name discriminant. Strip
        # it only when the remaining request is an explicit category search;
        # named/quoted references and unknown technical qualifiers stay literal.
        usage = re.search(r"\s+pour (?:mon|notre) atelier[?!.]?\s*$", question, re.I)
        if usage:
            generic = extract_criteria(question[: usage.start()], previous)
            if (
                generic.category_explicit
                and generic.reference is None
                and generic.mode == "search"
            ):
                return generic
        return extract_criteria(question, previous)

    def card(self, request, context, product, stock=None, observed_at=None):
        stock = (
            stock
            if stock is not None
            else self.catalogue.stock(request, context, product.product_id)
        )
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
            "observed_at": observed_at or datetime.now(UTC).isoformat(),
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

    def low_stock(self, request, context, question):
        categories = self.catalogue.categories(request, context)
        criteria = low_stock_criteria(question, categories)
        observed = datetime.now(UTC).isoformat()
        page = self.catalogue.search_stock(
            request,
            context,
            TenantProductQuery(
                limit=5,
                status="active",
                categories=criteria.categories,
                maximum_available=max(0, criteria.maximum),
                sort_by_available=True,
            ),
        )
        items, total = (page.items, page.total) if criteria.maximum >= 0 else ((), 0)
        cards = [
            self.card(request, context, pair.product, pair.stock, observed)
            for pair in items
        ]
        label = criteria.label + (
            " ; catégorie : " + ", ".join(criteria.categories)
            if criteria.categories
            else ""
        )
        return {
            "intent": "LOW_STOCK",
            "products": cards,
            "total": total,
            "text": f"{total} produit(s) actif(s) : {label}. "
            + (
                f"Les {len(cards)} premiers sont affichés ; résultat partiel. "
                if total > len(cards)
                else ""
            )
            + f"Observation : {observed}. Consultation en lecture seule, sans réservation.",
            "criteria": None,
            "catalogue_categories": [],
            "advice_skus": [],
        }

    async def advice(self, request, context, question, state):
        previous = (
            AdviceCriteria(**state["criteria"]) if state.get("criteria") else None
        )
        actual_categories = self.catalogue.categories(request, context)
        parsed_question, custom = custom_category_question(question, actual_categories)
        criteria = self.criteria(parsed_question, previous)
        if custom and criteria.reference:
            # A category inside a product name must not change that identity.
            criteria = self.criteria(question, previous)
            custom = None
        if custom:
            categories = custom
            criteria = replace(criteria, category=None, category_explicit=True)
        elif criteria.category_explicit:
            categories = persisted_categories(
                criteria.category.value, actual_categories
            )
        elif state.get("catalogue_categories"):
            categories = tuple(
                c for c in state["catalogue_categories"] if c in actual_categories
            )
        elif criteria.category:
            categories = persisted_categories(
                criteria.category.value, actual_categories
            )
        else:
            categories = ()
        category_requested = bool(
            custom or criteria.category or state.get("catalogue_categories")
        )
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
        maximum = (
            (
                int(Decimal(str(criteria.maximum)) * 100)
                - int(criteria.exclusive_maximum)
            )
            if criteria.maximum is not None
            else None
        )
        minimum = (
            int(Decimal(str(criteria.minimum)) * 100)
            if criteria.minimum is not None
            else None
        )
        if reference and criteria.mode == "compare" and not criteria.category_explicit:
            categories, category_requested = (reference.category,), True
        cards = []
        total = 0
        query = None
        if criteria.mode == "product":
            cards = [self.card(request, context, reference)]
            total = 1
        elif criteria.mode == "cheapest":
            for sku in prior_skus:
                p = self.catalogue.by_sku(request, context, sku)
                if p is None or p.status != "active" or p.sku in criteria.exclude_skus:
                    continue
                card = self.card(request, context, p)
                if (
                    category_requested
                    and p.category not in categories
                    or maximum is not None
                    and (p.currency != "EUR" or p.price_minor > maximum)
                    or minimum is not None
                    and (p.currency != "EUR" or p.price_minor < minimum)
                    or criteria.available is not None
                    and (card["available"] > 0) != criteria.available
                ):
                    continue
                cards.append(card)
            if len({p["currency"] for p in cards}) > 1:
                raise ClarificationRequired(
                    "Commencez par une liste dans une seule devise."
                )
            cards = sorted(cards, key=lambda p: (p["price_minor"], p["sku"]))[:1]
            total = len(cards)
        elif not category_requested or categories:
            query = TenantProductQuery(
                limit=criteria.limit,
                categories=categories,
                exclude_skus=tuple(
                    dict.fromkeys(
                        (
                            *criteria.exclude_skus,
                            *((reference.sku,) if reference else ()),
                        )
                    )
                ),
                minimum_price_minor=minimum,
                maximum_price_minor=maximum,
                currency="EUR"
                if maximum is not None or minimum is not None
                else reference.currency
                if reference
                else None,
                available=criteria.available,
                status="active",
                sort_by_price=True,
            )
            page = self.catalogue.search_stock(request, context, query)
            observed = datetime.now(UTC).isoformat()
            cards = [
                self.card(request, context, pair.product, pair.stock, observed)
                for pair in page.items
            ]
            total = page.total
        if reference and criteria.mode == "compare":
            cards.insert(0, self.card(request, context, reference))
        outside = (
            reference is not None
            and maximum is not None
            and (reference.currency != "EUR" or reference.price_minor > maximum)
        )
        labels = []
        if category_requested:
            labels.append(
                "catégorie : " + (", ".join(categories) or "absente du catalogue")
            )
        if criteria.maximum is not None:
            labels.append(
                f"prix {'<' if criteria.exclusive_maximum else '≤'} {Decimal(str(criteria.maximum))} EUR"
            )
        if criteria.available is not None:
            labels.append("disponible > 0" if criteria.available else "disponible = 0")
        summary = "; ".join(labels) or "produits actifs"
        if cards:
            message = (
                f"{total} résultat(s) trouvé(s) ; {len(cards)} fiche(s) affichée(s). "
            )
            if total == 1 and criteria.limit > 1:
                message += "Un seul produit correspond aux critères. "
            message += (
                f"Critères : {summary}. Disponibilité observée, sans réservation."
            )
        else:
            message = f"Aucun produit ne correspond aux critères : {summary}. "
            if query is not None and maximum is not None:
                without_budget = self.catalogue.search_stock(
                    request,
                    context,
                    replace(
                        query,
                        limit=1,
                        maximum_price_minor=None,
                    ),
                )
                if without_budget.total:
                    message += "Le plafond de prix exclut les produits satisfaisant les autres critères. "
            if query is not None and criteria.available is not None:
                without_availability = self.catalogue.search_stock(
                    request, context, replace(query, limit=1, available=None)
                )
                if without_availability.total:
                    message += "Le critère de disponibilité exclut les produits satisfaisant les autres critères. "
            message += "Vous pouvez élargir la catégorie ou le plafond de prix puis relancer la recherche."
        return {
            "intent": "COMPARISON" if criteria.mode == "compare" else "PRODUCT_ADVICE",
            "text": ("Fiche ciblée hors du budget précédent. " if outside else "")
            + message,
            "products": cards[:5],
            "total": total,
            "criteria": criteria_context(criteria),
            "catalogue_categories": list(categories),
            "advice_skus": [p["sku"] for p in cards[:5]],
        }
