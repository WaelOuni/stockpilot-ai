"""Reuse the historical lexical contracts, never its repositories or float prices."""

import re
from dataclasses import asdict, replace
from datetime import UTC, datetime
from decimal import Decimal

from stockpilot.application.dto.advice_criteria import AdviceCriteria
from stockpilot.application.use_cases.catalogue_advice import (
    ClarificationRequired,
    criteria_context,
    extract_criteria,
    is_advice_followup,
    normalize,
    resolve_advice_reference,
)
from stockpilot.application.use_cases.resolve_product import (
    ResolutionStatus,
    ResolveProductUseCase,
)
from stockpilot.domain.entities.tenant_product import TenantProductQuery
from stockpilot.application.services.tenant_catalogue_discovery import (
    catalogue_question,
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
    def is_grounded_request(question):
        value = normalize(question).strip().rstrip('.?!').strip()
        return bool(re.fullmatch(
            r"compare (?:deux|2) (?:perceuses?|visseuses?|scies?|ponceuses?) disponibles|"
            r"je cherche un outil abordable pour mon atelier|"
            r"je prefere celle avec le plus de stock disponible|"
            r"affiche seulement des alternatives disponibles|"
            r"pourquoi recommandes-tu ce produit|"
            r"qu'est-ce qui est mieux, une visseuse ou une perceuse|"
            r"montre-moi les moins cheres|compare les deux premieres|et une autre moins chere",
            value,
        ))

    @staticmethod
    def _saved_query(state):
        if not state.get('catalogue_search'):
            raise ClarificationRequired("Commencez par une recherche dans cette conversation.")
        values = dict(state['catalogue_search'])
        for name in ('categories', 'exclude_skus'):
            values[name] = tuple(values.get(name, ()))
        return TenantProductQuery(**values)

    def _query_reply(self, request, context, query, state, explanation):
        page = self.catalogue.search_stock(request, context, query)
        observed = datetime.now(UTC).isoformat()
        cards = [self.card(request, context, p.product, p.stock, observed) for p in page.items[:5]]
        return {
            'intent': 'PRODUCT_ADVICE',
            'text': ('Aucun produit ne correspond aux critères. ' if not cards else f'{page.total} résultat(s) vérifié(s). ') + explanation,
            'products': cards, 'total': page.total,
            'criteria': state.get('criteria'),
            'catalogue_categories': list(query.categories),
            'advice_skus': [p['sku'] for p in cards],
            'catalogue_search': asdict(query),
        }

    async def _grounded_advice(self, request, context, question, state):
        value = normalize(question).strip().rstrip('.?!').strip()
        if value.startswith("qu'est-ce qui est mieux"):
            return {'intent': 'PRODUCT_ADVICE', 'products': [], 'total': 0,
                    'text': "Le choix dépend de votre usage. Le catalogue ne permet pas d'établir qu'une visseuse ou une perceuse est meilleure : performances, qualité et notes clients ne sont pas renseignées. Pour des références précises, je peux comparer uniquement leur prix, leur devise et leur stock disponible vérifiés."}
        if value == 'je cherche un outil abordable pour mon atelier':
            return self._query_reply(request, context, TenantProductQuery(limit=3, status='active', currency='EUR', sort_by_price=True), {},
                "Les prix EUR les plus bas du catalogue sont affichés ; aucun budget personnel n'est supposé et aucune qualité ou adéquation à l'atelier n'est établie.")
        if re.fullmatch(r'compare (?:deux|2) (?:perceuses?|visseuses?|scies?|ponceuses?) disponibles', value):
            reply = await self.advice(request, context, re.sub(r'^compare (?:deux|2)', 'Je cherche deux', question, flags=re.I), {})
            reply['intent'] = 'COMPARISON'
            reply['text'] += " Comparaison limitée aux prix, devises et stocks vérifiés ; aucune qualité ou performance supposée."
            return reply
        if value == 'montre-moi les moins cheres':
            query = replace(self._saved_query(state), limit=5, offset=0, sort_by_price=True, sort_by_available=False)
            if query.currency is None:
                currencies = set()
                for sku in state.get('advice_skus', [])[:5]:
                    product = self.catalogue.by_sku(request, context, sku)
                    if product is None or product.status != 'active':
                        raise ClarificationRequired('Une référence précédente n’est plus accessible ; relancez la recherche.')
                    currencies.add(product.currency)
                if len(currencies) != 1:
                    raise ClarificationRequired('Choisissez une recherche dans une seule devise ; aucune conversion n’est supposée.')
                query = replace(query, currency=next(iter(currencies)))
            return self._query_reply(request, context, query, state,
                f'Prix {query.currency} croissants, puis SKU ; autres devises non comparées. Filtres conservés, observation actuelle sans réservation.')
        if value in ('affiche seulement des alternatives disponibles', 'et une autre moins chere'):
            criteria = state.get('criteria') or {}
            if criteria.get('mode') != 'compare' or not criteria.get('reference'):
                raise ClarificationRequired('Comparez d’abord un produit par son SKU dans cette conversation.')
            anchor = await self.resolve(request, context, criteria['reference'])
            query = self._saved_query(state)
            query = replace(query, offset=0, sort_by_price=True, sort_by_available=False,
                            categories=(anchor.category,), currency=anchor.currency,
                            exclude_skus=tuple(dict.fromkeys((*query.exclude_skus, anchor.sku))))
            if value == 'affiche seulement des alternatives disponibles':
                query = replace(query, available=True)
            else:
                if anchor.price_minor == 0:
                    raise ClarificationRequired('Aucun prix positif ne peut être inférieur à ce prix nul.')
                cap = min(anchor.price_minor - 1, query.maximum_price_minor) if query.maximum_price_minor is not None else anchor.price_minor - 1
                query = replace(query, limit=1, maximum_price_minor=cap,
                                exclude_skus=tuple(dict.fromkeys((*query.exclude_skus, *state.get('advice_skus', [])))))
            return self._query_reply(request, context, query, state,
                'Alternatives de la même catégorie et devise, critères revérifiés dans le catalogue courant ; aucune équivalence de performance supposée.')
        skus = state.get('advice_skus', [])[:5]
        needed = 2 if value == 'compare les deux premieres' else 1
        if len(skus) < needed or value == 'pourquoi recommandes-tu ce produit' and len(skus) != 1:
            raise ClarificationRequired('Précisez les références concernées dans cette conversation.')
        cards = []
        for sku in skus[:2] if needed == 2 else skus:
            product = self.catalogue.by_sku(request, context, sku)
            if product is None or product.status != 'active':
                raise ClarificationRequired('Une référence précédente n’est plus accessible ; relancez la recherche.')
            cards.append(self.card(request, context, product))
        if value == 'je prefere celle avec le plus de stock disponible':
            cards = sorted(cards, key=lambda p: (-p['available'], p['sku']))[:1]
            explanation = 'Stock disponible le plus élevé parmi les références précédemment affichées, revérifiées maintenant ; aucune réservation.'
        elif needed == 2:
            explanation = 'Comparaison des deux premières références affichées : prix, devises et stocks actuels uniquement ; aucune performance supposée.'
        else:
            p = cards[0]
            explanation = f"Faits revérifiés pour {p['sku']} : prix {Decimal(p['price_minor']) / 100} {p['currency']}, stock physique {p['quantity']}, réservé {p['reserved']}, disponible {p['available']}. Ce sont les seules raisons vérifiables ; aucune qualité, note client ou performance n'est déduite."
        return {'intent': 'COMPARISON' if needed == 2 else 'PRODUCT_ADVICE', 'products': cards,
                'total': len(cards), 'text': explanation, 'criteria': state.get('criteria'),
                'catalogue_categories': state.get('catalogue_categories', []),
                'catalogue_search': None, 'advice_skus': [p['sku'] for p in cards]}

    @staticmethod
    def criteria(question, previous):
        # A generic workshop context is not a product-name discriminant. Strip
        # it only when the remaining request is an explicit category search;
        # named/quoted references and unknown technical qualifiers stay literal.
        quoted = re.fullmatch(
            r'\s*(?:je (?:re)?cherche|montre(?:-moi)?)\s+(?:(?:un|le) produit (?:appele|appelé)\s+)?["«](.+?)["»][.!?]?\s*',
            question, re.I,
        )
        if quoted:
            return replace(previous or AdviceCriteria(), mode="product", reference=quoted[1], category_explicit=False)
        question = catalogue_question(question)
        if re.fullmatch(r"(?:quels outils sont presents dans le catalogue|je veux voir les produits actifs)", normalize(question).rstrip(".!? ")):
            return AdviceCriteria()
        cheapest = re.fullmatch(r"(?:quelle est la|quelles sont les) (?:(deux|trois|[1-5]) )?(perceuses?|visseuses?|scies?|ponceuses?) (?:la|les) moins cheres?", normalize(question).rstrip(".!? "))
        if cheapest:
            return extract_criteria(f"Je cherche {cheapest[1] or 'exactement une'} {cheapest[2]}", previous)
        if re.fullmatch(r"(?:je veux )?(?:moins de|plus de|au moins) \d+[.!?]?", question):
            raise ClarificationRequired("Précisez la catégorie et l’unité : prix en EUR ou stock disponible.")
        usage = re.search(r"\s+pour (?:mon|notre) atelier[?!.]?\s*$", question, re.I)
        if usage:
            generic = extract_criteria(question[: usage.start()], previous)
            if (
                generic.category_explicit
                and generic.reference is None
                and generic.mode == "search"
            ):
                return TenantAssistantCatalogue.criteria(question[: usage.start()], previous)
        criteria = extract_criteria(question, previous)
        amounts = re.findall(r"(?<![\w.,])\d+(?:[.,]\d{1,2})?(?=\s*(?:€|euros?\b|eur\b))", question)
        if not amounts:
            amounts = re.findall(r"\b(?:moins de|sous|maximum|maxi|budget|plafond|descends|monte)\s*(?::|de|a)?\s*(\d+(?:[.,]\d{1,2})?)", question)
        if criteria.maximum is not None and amounts:
            exact = Decimal(amounts[0].replace(',', '.'))
            if exact != Decimal(str(criteria.maximum)) or exact * 100 > 2**63 - 1:
                raise ClarificationRequired("Ce plafond ne peut pas être représenté exactement en centimes ; précisez un montant plus petit.")
        if criteria.maximum is not None and re.search(r"\bexactement\s+\d+\s*(?:€|euros?)", question):
            raise ClarificationRequired("Précisez un plafond de prix ; le prix exact n’est pas pris en charge.")
        return criteria

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
        empty = criteria.maximum is not None and criteria.maximum < 0
        query = TenantProductQuery(
            limit=5, status="active", categories=criteria.categories,
            minimum_available=criteria.minimum,
            maximum_available=max(0, criteria.maximum) if criteria.maximum is not None else None,
            maximum_price_minor=criteria.maximum_price_minor,
            currency="EUR" if criteria.maximum_price_minor is not None else None,
            sort_by_available=True,
        )
        page = self.catalogue.search_stock(request, context, query)
        items, total = ((), 0) if empty else (page.items, page.total)
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
            "catalogue_search": None if empty else asdict(query),
        }

    def refine_stock(self, request, context, question, saved):
        if not saved:
            raise ClarificationRequired("Commencez par une recherche dans cette conversation.")
        values = dict(saved)
        for field in ("categories", "exclude_skus"):
            values[field] = tuple(values.get(field, ()))
        query = TenantProductQuery(**values)
        value = normalize(question).rstrip('.?! ')
        if value.startswith('et avec exactement '):
            criteria = low_stock_criteria('Produits avec ' + value[8:] + ' unités disponibles', ())
            query = replace(query, minimum_available=criteria.minimum, maximum_available=criteria.maximum)
        elif value == 'exclue les ruptures':
            query = replace(query, available=True)
        else:
            categories = persisted_categories(value.removeprefix('montre uniquement les '), self.catalogue.categories(request, context))
            if not categories:
                raise ClarificationRequired("Choisissez une catégorie du catalogue.")
            query = replace(query, categories=categories)
        query = replace(query, offset=0, limit=5)
        page = self.catalogue.search_stock(request, context, query)
        observed = datetime.now(UTC).isoformat()
        return {
            'intent':'LOW_STOCK', 'text':f'{page.total} produit(s) actif(s). Filtres de cette conversation conservés ; observation actuelle, sans réservation.',
            'products':[self.card(request, context, p.product, p.stock, observed) for p in page.items],
            'total':page.total, 'criteria':None, 'catalogue_categories':[], 'advice_skus':[], 'catalogue_search':asdict(query),
        }

    async def advice(self, request, context, question, state):
        if self.is_grounded_request(question):
            return await self._grounded_advice(request, context, question, state)
        value = normalize(catalogue_question(question)).strip()
        follows = is_advice_followup(value) or re.match(r'^(?:et|seulement|toujours|sans|retire|parmi|maintenant)\b', value)
        if follows and state.get('catalogue_search'):
            saved = self._saved_query(state)
            # AdviceCriteria has price/availability fields but no stock range.
            # Never rebuild a wider query by silently dropping such a range.
            if saved.minimum_available is not None or saved.maximum_available is not None or not state.get('criteria'):
                raise ClarificationRequired('Cette recherche conserve des filtres de stock non représentables par cet affinage. Précisez une nouvelle recherche complète ou revenez explicitement au catalogue complet.')
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
        if any(bound is not None and not 0 <= bound <= 2**63 - 1 for bound in (minimum, maximum)):
            raise ClarificationRequired("Le plafond de prix dépasse les bornes du catalogue.")
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
            "catalogue_search": asdict(query) if query is not None else None,
        }

    def results(self, request, context, saved, *, limit=20, offset=0,
                custom_stock_range=False, minimum_available=None, maximum_available=None,
                exclude_sold_out=None, sort=None):
        """Current catalogue facts; never update a historical reply/checkpoint."""
        values = dict(saved)
        for field in ("categories", "exclude_skus"):
            values[field] = tuple(values.get(field, ()))
        original = TenantProductQuery(**values)
        if not custom_stock_range and (minimum_available is not None or maximum_available is not None):
            raise ValueError("An explicit stock range selection is required.")
        selected_sort = sort or ("available" if original.sort_by_available else "price" if original.sort_by_price else "sku")
        if selected_sort not in ("available", "price", "sku"):
            raise ValueError("Invalid catalogue sort.")
        query = replace(
            original, limit=limit, offset=offset, status="active",
            minimum_available=minimum_available if custom_stock_range else original.minimum_available,
            maximum_available=maximum_available if custom_stock_range else original.maximum_available,
            available=original.available if exclude_sold_out is None else True if exclude_sold_out else None,
            sort_by_available=selected_sort == "available", sort_by_price=selected_sort == "price",
        )
        page = self.catalogue.search_stock(request, context, query)
        observed = datetime.now(UTC).isoformat()
        return {
            "items": [self.card(request, context, pair.product, pair.stock, observed) for pair in page.items],
            "total": page.total, "has_more": page.has_more, "limit": limit, "offset": offset,
            "observed_at": observed,
            "filters": {"minimum_available": query.minimum_available, "maximum_available": query.maximum_available,
                        "exclude_sold_out": query.available is True, "sort": selected_sort},
        }
