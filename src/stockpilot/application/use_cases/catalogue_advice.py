"""Catalogue-grounded advice: no LLM-authored product facts cross this boundary."""
import json
import math
import re
import unicodedata
from dataclasses import asdict, replace
from decimal import Decimal
from stockpilot.application.dto.advice_criteria import AdviceCriteria
from stockpilot.application.use_cases.advice_presentation import (
    advice_summary, catalogue_number, no_results_summary, relevance_reasons,
)

from stockpilot.domain.enums.product_category import ProductCategory


# One bounded request prefix, shared by routing and identity extraction.
_ASK_ADVICE_PREFIX = re.compile(r'^demander\s+conseil\s+sur\b\s*')
# Bounded lexical grammar shared by routing and reference extraction. An unknown
# model/name suffix must survive; these are request words, not fuzzy identity rules.
_SEARCH_ACTION = r'(?:(?:re)?cherche(?:r)?|trouve(?:r)?)'
_REQUEST_ACTION = rf'(?:{_SEARCH_ACTION}|propose|affiche|montre|donne|recommand\w*|conseill\w*)'
_REQUEST_CUE = re.compile(rf'\b(?:{_REQUEST_ACTION}|voudrais)\b')
_ADVICE_CUE = re.compile(rf'\b(?:{_REQUEST_ACTION}|voudrais|quelles?|quels?)\b')
_REQUEST_PREFIX = re.compile(
    r'^(?:(?:peux|pourrais|pouvez)[- ](?:tu|vous) (?:me )?)?'
    rf'(?:(?:je (?:{_SEARCH_ACTION}|voudrais|veux|souhaite))|{_REQUEST_ACTION}(?:[- ]moi)?)\b\s*'
    r'(?:(?:sur|pour|de)\s+)?')
# 'tous les stocks' removes an Advice availability filter; it is not a stock lookup.
_NON_ADVICE = re.compile(r'\b(command\w*|achet\w*|reserve\w*|annul\w*)\b|\b(?:le|(?<!tous )les) stocks?\b')
_STRICT_BUDGET = r'(?:moins de|inferieur a|sous|en dessous de)'
_BUDGET_TERM = rf'(?:{_STRICT_BUDGET}|budget|maximum|maxi|plafond|descends|monte)'


def normalize(text: str) -> str:
    return ''.join(c for c in unicodedata.normalize('NFKD', text.casefold())
                   if not unicodedata.combining(c)).replace('’', "'")


def is_advice_followup(text: str) -> bool:
    value = normalize(text).strip()
    if _NON_ADVICE.search(value):
        return False
    return bool(re.search(
        r"^(?:et (?:si .*\b(?:budget|monte|descends|perceuse|visseuse|scie|ponceuse)|"
        r"pour |avec un budget |sans (?:plafond|budget|(?:contrainte de )?disponibilite)|parmi )|compare\b|"
        r"(?:sans (?:plafond|budget)|retire le budget)|"
        r"(?:montre|donne|affiche)(?:[- ]moi)? (?:seulement )?(?:[1-5]|deux|trois|quatre|cinq) produits?\b|"
        r"montre[- ]moi (?:seulement |\w+ alternatives)|"
        r"(?:propose|recommande)[- ]moi .*mon budget)|"
        r"\b(parmi (?:ceux|ces)|le premier|celui-ci|cette perceuse|ce produit|moins chere?)\b", value))


def is_catalogue_advice(text: str) -> bool:
    value = normalize(text)
    if _NON_ADVICE.search(value):
        return False
    targeted = bool(_ASK_ADVICE_PREFIX.match(value.lstrip()) or re.search(
        r'^(?:(?:peux|pourrais)[- ]tu (?:me )?)?conseill\w*(?:[- ]moi)? sur\b', value))
    if (not targeted and re.search(r'\b[a-z]+\d+[a-z0-9-]*\b', value)
            and re.search(r'\b(disponibles?|stocks?)\b', value)
            and not (_REQUEST_CUE.search(value) or re.search(r'\b(compare|alternatives?)\b', value))):
        return False
    return bool(targeted or is_advice_followup(text) or re.search(r'\bcompare\b|\balternatives?\b', value)
                or (_ADVICE_CUE.search(value)
                    and re.search(r'\b(perceuses?|visseuses?|scies?|ponceuses?|produits?|outils?|sku|[a-z]+\d+[a-z0-9-]*)\b', value)))


class ClarificationRequired(ValueError):
    pass


def extract_criteria(question: str, previous: AdviceCriteria | None = None) -> AdviceCriteria:
    if len(question) > 4096:
        raise ClarificationRequired('Précisez une demande de moins de 4097 caractères.')
    text = normalize(question)
    from stockpilot.application.use_cases.resolve_product import explicit_skus
    for sku in explicit_skus(question):
        text = text.replace(normalize(sku), '')
    categories = {category for pattern, category in (
        (r'\b(perceuses?|drill)\b', ProductCategory.DRILL),
        (r'\b(visseuses?|screwdriver)\b', ProductCategory.SCREWDRIVER),
        (r'\b(scies?|saw)\b', ProductCategory.SAW),
        (r'\b(ponceuses?|sander)\b', ProductCategory.SANDER),
    ) if re.search(pattern, text)}
    if len(categories) > 1:
        raise ClarificationRequired('Quelle catégorie souhaitez-vous comparer ? Choisissez une seule catégorie.')
    if re.search(r'\d[ \u00a0\u202f]+\d|\d[.,]\d{3,}', text):
        raise ClarificationRequired('Écrivez le budget sans séparateur de milliers et avec au plus deux décimales.')
    number = r'(\d+(?:[.,]\d{1,2})?)'
    # No currency is stored in Product: other currencies cannot be converted.
    if re.search(r'\$|\b(dollars?|usd|gbp|livres?)\b', text):
        raise ClarificationRequired('Le catalogue ne précise pas de devise. Indiquez un plafond numérique comparable au prix catalogue.')
    if re.search(r'-\s*\d', text):
        raise ClarificationRequired('Indiquez un budget positif et précis.')
    amounts = re.findall(number + r'\s*(?:€|euros?\b|eur\b)', text)
    # Inspect all expressed bounds, including mixed bare/currency wording. A
    # currency suffix must not conceal a conflicting numeric constraint.
    amounts += re.findall(rf'\b{_BUDGET_TERM}\s*(?::|de|a)?\s*' + number, text)
    # With an explicit amount, 'Max 18 Atelier' can be identity evidence, not
    # another cap. Accept abbreviated 'max N' only as a complete constraint.
    max_suffix = (r'(?=\s*(?:€|euros?\b|eur\b|[,;.!?]|$|et\b|disponibles?\b|en stock\b))'
                  if amounts else '')
    amounts += re.findall(r'\bmax\s*(?::|de|a)?\s*' + number + max_suffix, text)
    values = {float(amount.replace(',', '.')) for amount in amounts}
    if len(values) > 1 or re.search(r'\b(entre|a partir de|au moins|plus de)\s*\d', text):
        raise ClarificationRequired('Précisez un seul plafond de budget ; les intervalles et budgets minimum ne sont pas pris en charge.')
    maximum = next(iter(values), None)
    if maximum is not None and (not math.isfinite(maximum) or maximum <= 0):
        raise ClarificationRequired('Indiquez un budget strictement positif.')
    if re.search(r'\b(mon|ce|le meme) budget\b', text) and maximum is None and previous is None:
        raise ClarificationRequired('Quel est votre budget maximum ?')
    remove_budget = bool(re.search(r'\b(sans (?:limite de )?budget|sans plafond|retire le budget)\b', text))
    # A bare "Max" can be a product suffix; do not reinterpret it as a budget.
    if maximum is None and not remove_budget and not (previous and re.search(r'\bmon budget\b', text)) and re.search(rf'\b(?:{_STRICT_BUDGET}|budget|maximum|maxi)\b', text):
        raise ClarificationRequired('Quel est votre budget maximum, en chiffres ?')
    unavailable = bool(re.search(r'\bindisponibles?\b|\ben rupture\b', text))
    available = bool(re.search(r'\bdisponibles?\b|\ben stock\b', text))
    if re.search(r"\b(pas|non)\s+(?:disponibles?|en stock)\b", text):
        unavailable, available = True, False
    if available and unavailable:
        raise ClarificationRequired('Souhaitez-vous les produits disponibles ou indisponibles ?')
    count_matches = list(re.finditer(
        r'\b(\d+|deux|trois|quatre|cinq|(?:un|une)(?=\s+seul)|(?<=exactement )(?:un|une)|(?<=seulement )(?:un|une))'
        r'\s+(?:seule?\s+)?(?:produits?|outils?|perceuses?|visseuses?|scies?|ponceuses?|autres|alternatives?)\b', text))
    count_match = count_matches[-1] if count_matches else None
    counts = {'un': 1, 'une': 1, 'deux': 2, 'trois': 3, 'quatre': 4, 'cinq': 5}
    count = counts.get(count_match[1]) if count_match else (previous.limit if previous else 3)
    if count_match and count is None:
        count = int(count_match[1])
    if not 1 <= count <= 5:
        raise ClarificationRequired('Combien de produits souhaitez-vous comparer ? Choisissez entre 1 et 5.')
    mode = 'compare' if re.search(r'\b(compare|alternatives?)\b', text) else 'search'
    if re.search(r'\b(moins cher|moins chere)\b', text):
        mode = 'cheapest'
    reference = extract_reference(question)
    if (previous and previous.mode in {'product', 'compare'} and previous.reference
            and not reference and mode == 'search' and not categories and not count_match):
        mode, reference = previous.mode, previous.reference
    if reference and mode == 'search':
        mode = 'product'
    if mode == 'compare':
        count = count if count_match else previous.limit if previous and previous.mode == 'compare' else 2
        if count > 4:
            raise ClarificationRequired('Choisissez au plus quatre alternatives, en plus du produit de référence.')
    if previous:
        maximum = maximum if maximum is not None else (None if remove_budget else previous.maximum)
    exclusive = bool(re.search(rf'\b{_STRICT_BUDGET}\b', text))
    if previous and not amounts and not remove_budget:
        exclusive = previous.exclusive_maximum
    remove_availability = bool(re.search(r'\b(?:meme indisponibles?|sans (?:contrainte de )?disponibilite|tous les stocks)\b', text))
    if remove_availability:
        available, unavailable = False, False
    criteria = AdviceCriteria(
        category=next(iter(categories), previous.category if previous else None),
        maximum=maximum, exclusive_maximum=exclusive,
        available=(True if available else False if unavailable else
                   previous.available if previous and not remove_availability else None),
        limit=count, mode=mode, reference=reference, category_explicit=bool(categories),
    )
    if criteria.category is None and maximum is None and criteria.available is None and not reference and not count_match and mode == 'search':
        raise ClarificationRequired('Quelle catégorie ou quel budget maximum souhaitez-vous ?')
    return criteria


def extract_reference(question: str) -> str | None:
    """Strip only supported advice scaffolding; unknown name discriminants survive."""
    from stockpilot.application.use_cases.resolve_product import explicit_skus
    skus = explicit_skus(question)
    if len(skus) > 1:
        raise ClarificationRequired('Précisez un seul SKU de référence.')
    if skus:
        return 'SKU ' + skus[0]
    text = normalize(question).strip().rstrip('?.!').strip()
    if re.search(r'\bmoins chere?\b', text):
        return None
    # Anaphora are resolved only against the previous authorized SKU list.
    if re.search(r"\b(le premier|celui-ci|cette perceuse|ce produit)\b", text):
        return None
    if is_advice_followup(text) and not text.startswith('compare'):
        return None
    prefix = _ASK_ADVICE_PREFIX.match(text)
    if prefix:
        text = text[prefix.end():]
    else:
        text = _REQUEST_PREFIX.sub('', text, count=1)
        text = re.sub(r'^compare(?:[- ]moi)?\b\s*(?:(?:sur|pour)\s+)?', '', text, count=1)
    text = re.split(r'\s+(?:avec|a)\s+(?:(?:\d+|deux|trois|quatre|cinq)\s+)?(?:autres|alternatives)\b', text, maxsplit=1)[0]
    # Only complete trailing constraints can be removed. In particular, Max/Pro
    # and unknown model suffixes remain identity evidence when no amount follows.
    amount = r'\d+(?:[.,]\d{1,2})?'
    currency = r'(?:€|euros?\b|eur\b)'
    budget_suffix = (rf'[\s,;]+(?:(?:(?:a|pour) )?(?:{_STRICT_BUDGET}|maximum|maxi|max|'
                     r'(?:avec un )?budget|plafond)(?:\s+(?:de|a))?\s*:?\s*'
                     + amount + r'(?:\s*' + currency + r')?|(?:a )?'
                     + amount + r'\s*' + currency + r')(?:\s+maximum)?$')
    for _ in range(3):
        text = re.sub(budget_suffix, '', text).rstrip(' ,;')
        text = re.sub(r'[\s,;]+(?:(?:et|sont) )?(?:(?:non|pas) )?(?:disponibles?|indisponibles?|en stock|en rupture)$', '', text).rstrip(' ,;')

    text = re.sub(r'^(?:(?:exactement|seulement)\s+)?(?:un|une|la|le|les|des|du|deux|trois|quatre|cinq|\d+)\s+(?:seule?\s+)?', '', text)
    if re.fullmatch(r'(?:quelles? |quels? )?(?:perceuses?|visseuses?|scies?|ponceuses?|produits?|outils?)(?: sont)?', text):
        return None
    if not text or re.fullmatch(r'(?:et .*|pour .*|.*mon budget)', text):
        return None
    text = text.strip('"«» ')
    if len(text) > 256:
        raise ClarificationRequired('Précisez un nom ou SKU de moins de 257 caractères.')
    return text



class CatalogueAdviceUseCase:
    def __init__(self, products, llm):
        self.products = products
        self.llm = llm

    async def execute(self, question: str, *, previous: AdviceCriteria | None = None,
                      previous_skus: tuple[str, ...] = ()) -> dict:
        empty = {'recommendations': [], 'advice_status': 'CLARIFICATION_REQUIRED',
                 'advice_criteria': None, 'advice_skus': []}
        reference_product = None
        try:
            criteria = extract_criteria(question, previous)
            text = normalize(question)
            contextual = re.search(r'\b(le premier|celui-ci|cette perceuse|ce produit)\b', text)
            if contextual and not criteria.reference:
                if not previous_skus or (contextual[1] != 'le premier' and len(previous_skus) != 1):
                    raise ClarificationRequired('Quel produit souhaitez-vous comparer ou consulter ? Précisez son nom ou son SKU.')
                criteria = replace(criteria, reference='SKU ' + previous_skus[0],
                                   mode='compare' if criteria.mode == 'compare' else 'product')
            if criteria.mode == 'compare' and not criteria.reference:
                raise ClarificationRequired('Quel produit souhaitez-vous comparer ? Précisez son nom ou son SKU.')
            if criteria.mode == 'cheapest' and criteria.reference:
                raise ClarificationRequired('Précisez les produits à comparer ; choisissez la liste précédente ou une référence avec alternatives.')
            if criteria.mode == 'cheapest' and not previous_skus:
                raise ClarificationRequired('Quels produits souhaitez-vous comparer ? Commencez par une recherche.')
        except ClarificationRequired as error:
            return {**empty, 'response': str(error)}
        context = {'advice_criteria': criteria_context(criteria), 'advice_skus': []}
        try:
            if criteria.reference:
                from stockpilot.application.use_cases.resolve_product import ResolutionStatus
                resolution = await resolve_advice_reference(self.products, criteria.reference)
                if resolution.status == ResolutionStatus.AMBIGUOUS:
                    return {**empty, 'response': 'Plusieurs produits correspondent à cette référence. Précisez le nom complet ou le SKU.'}
                if resolution.product is None:
                    return {**empty, 'advice_status': 'NO_RESULTS',
                            'response': 'Ce produit ne peut pas être identifié dans le catalogue. Précisez son nom ou son SKU ; aucune alternative automatique.'}
                reference_product = await self.products.find_advice_by_sku(resolution.product.sku)
                if (reference_product is None or reference_product.sku != resolution.product.sku
                        or not AdviceCriteria().matches(reference_product)
                        or reference_product.name != resolution.product.name
                        or reference_product.document_id != resolution.product.document_id):
                    return {**empty, 'response': 'L’identité du produit a changé ou ne peut pas être vérifiée. Précisez à nouveau son SKU.'}
                if criteria.mode == 'compare':
                    same_comparison = (previous and previous.mode == 'compare'
                                       and previous.reference == 'SKU ' + reference_product.sku)
                    alternative_category = (criteria.category if criteria.category_explicit or same_comparison
                                            else reference_product.category)
                    criteria = replace(criteria, category=alternative_category,
                                       exclude_skus=(reference_product.sku,))
                criteria = replace(criteria, reference='SKU ' + reference_product.sku)
                context['advice_criteria'] = criteria_context(criteria)
            if criteria.mode == 'product':
                verified = [reference_product]
            else:
                candidates = ([await self.products.find_advice_by_sku(sku) for sku in previous_skus[:5]]
                              if criteria.mode == 'cheapest' else await self.products.find_advice_candidates(criteria))
                verified_by_sku = {}
                # Both adapters and remembered identities are untrusted until a current exact read.
                for product in candidates[:5]:
                    if product is None:
                        continue
                    current = await self.products.find_advice_by_sku(product.sku)
                    if current is not None and current.sku == product.sku and criteria.matches(current):
                        verified_by_sku[current.sku] = current
                verified = sorted(verified_by_sku.values(), key=lambda p: (p.price, p.sku))[
                    :1 if criteria.mode == 'cheapest' else criteria.limit]
                if reference_product is not None:
                    verified.insert(0, reference_product)
        except Exception:
            return {**empty, 'advice_status': 'CATALOGUE_UNAVAILABLE',
                    'response': 'Le catalogue ne peut pas être vérifié actuellement. Réessayez plus tard.'}
        if not verified:
            return {**empty, **context, 'advice_status': 'NO_RESULTS',
                    'response': no_results_summary(criteria)}
        recommendations = []
        llm_available = True
        shared_order = None
        for product in verified:
            facts = {
                'categorie': f'Catégorie catalogue : {product.category.value}.',
                'prix': f'Prix catalogue : {product.price:g}.' + (
                    f' Il respecte le plafond {"strict" if criteria.exclusive_maximum else "inclusif"} de {criteria.maximum:g}.'
                    if criteria.maximum is not None and replace(criteria, exclude_skus=()).matches(product) else ''),
                'stock': f'Disponibilité vérifiée : {"disponible" if product.available else "indisponible"} ({product.stock} unité(s)).',
            }
            order = list(facts)
            if llm_available and shared_order is None:
                try:
                    # The model formulates by ordering verified sentences only. Free prose,
                    # invented SKUs/specs/prices and prompt injections cannot be rendered.
                    response = await self.llm.generate(
                        'Explique en français la correspondance produit/demande en ordonnant ces trois raisons. '
                        'Réponds uniquement {"raisons": ["categorie", "prix", "stock"]} dans l’ordre le plus pertinent. '
                        + json.dumps({'demande': question, 'faits': facts}, ensure_ascii=False),
                        response_format={'type': 'object', 'properties': {'raisons': {
                            'type': 'array', 'items': {'type': 'string', 'enum': list(facts)},
                            'minItems': 3, 'maxItems': 3}}, 'required': ['raisons'], 'additionalProperties': False},
                    )
                    parsed = json.loads(response)
                    if set(parsed) != {'raisons'} or sorted(parsed['raisons']) != sorted(facts):
                        raise ValueError('Unverified explanation')
                    shared_order = parsed['raisons']
                except Exception:
                    llm_available = False
            order = shared_order or order
            reasons = relevance_reasons(criteria, product)
            comparison = {}
            if criteria.mode == 'compare':
                is_reference = product.sku == reference_product.sku
                matches = replace(criteria, exclude_skus=()).matches(product)
                comparison = {'role': 'reference' if is_reference else 'alternative',
                              'matches_criteria': matches,
                              'price_difference': product.price - reference_product.price}
                difference = Decimal(str(product.price)) - Decimal(str(reference_product.price))
                reasons['prix'] += (' Produit de référence.' if is_reference else
                                    f' Écart de prix avec la référence : {"+" if difference > 0 else ""}'
                                    f'{catalogue_number(difference, price=True)}.')
                if not matches:
                    reasons['prix'] += ' Hors critères' + (' : hors budget.' if criteria.maximum is not None and (
                        product.price >= criteria.maximum if criteria.exclusive_maximum else product.price > criteria.maximum
                    ) else ' (catégorie ou disponibilité).')
            elif criteria.mode == 'product' and not criteria.matches(product):
                reasons['prix'] += ' Fiche ciblée hors critères demandés ; ce produit n’est pas une correspondance valide.'
            recommendations.append({**comparison, 'sku': product.sku, 'product_name': product.name,
                                    'category': product.category.value, 'price': product.price,
                                    'stock': product.stock,
                                    'explanation': (' '.join(reasons[key].strip() for key in order if reasons[key].strip())
                                                    or 'Produit sélectionné dans le catalogue vérifié.')})
        return {'recommendations': recommendations, 'advice_status': 'RESULTS',
                **context, 'advice_skus': [p['sku'] for p in recommendations],
                'response': advice_summary(criteria, verified) + (
                    ' Présentation déterministe : le modèle est indisponible ou sa réponse a été refusée.' if not llm_available else '')}


def criteria_context(criteria: AdviceCriteria) -> dict:
    """Persist bounded criteria and the verified reference, never a prompt history or product facts."""
    return {key: value.value if key == "category" and value is not None else value for key, value in asdict(criteria).items()
            if key in {'category', 'minimum', 'maximum', 'exclusive_maximum', 'available', 'limit', 'mode', 'reference'}}


async def resolve_advice_reference(products, reference: str):
    """Reuse identity resolution; allow two literal discriminants only for read-only advice."""
    from stockpilot.application.use_cases.resolve_product import (
        CANDIDATE_LIMIT, ProductResolution, ResolveProductUseCase, ResolutionStatus,
        explicit_skus, normalize_name,
    )
    resolution = await ResolveProductUseCase(products).execute(reference)
    if resolution.status != ResolutionStatus.NOT_FOUND or explicit_skus(reference):
        return resolution
    terms = normalize_name(reference).split()
    if terms and terms[0] in {'perceuse', 'visseuse', 'scie', 'ponceuse', 'outil', 'produit'}:
        terms = terms[1:]
    if len(terms) != 2 or not all(term.isalpha() and len(term) >= 3 for term in terms):
        return resolution
    candidates = await products.find_resolution_candidates(' '.join(terms), exact=False, limit=CANDIDATE_LIMIT)
    if len(candidates) >= CANDIDATE_LIMIT:
        return ProductResolution(ResolutionStatus.AMBIGUOUS)
    candidates = [p for p in candidates if all(re.search(
        r'(?<!\w)' + re.escape(term) + r'(?!\w)', normalize_name(p.name),
    ) for term in terms)]
    if len(candidates) > 1:
        return ProductResolution(ResolutionStatus.AMBIGUOUS)
    return ProductResolution(ResolutionStatus.RESOLVED, candidates[0]) if candidates else resolution
