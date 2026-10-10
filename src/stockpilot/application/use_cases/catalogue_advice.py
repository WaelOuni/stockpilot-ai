"""Catalogue-grounded advice: no LLM-authored product facts cross this boundary."""
import json
import math
import re
import unicodedata
from stockpilot.application.dto.advice_criteria import AdviceCriteria

from stockpilot.domain.enums.product_category import ProductCategory


def normalize(text: str) -> str:
    return ''.join(c for c in unicodedata.normalize('NFKD', text.casefold())
                   if not unicodedata.combining(c)).replace('’', "'")


def is_catalogue_advice(text: str) -> bool:
    value = normalize(text)
    if (re.search(r'\b[a-z]+\d+[a-z0-9-]*\b', value)
            and re.search(r'\b(disponibles?|stocks?)\b', value)
            and not re.search(r'\b(cherche|propose|recommande|conseill\w*)\b', value)):
        return False
    return bool(re.search(r'\b(cherche|propose|recommande|conseill\w*|quelles?|quels?)\b', value)
                and re.search(r'\b(perceuses?|visseuses?|scies?|ponceuses?|produits?|outils?)\b', value)
                and not re.search(r'\b(command\w*|reserve\w*|annul\w*)\b', value))


class ClarificationRequired(ValueError):
    pass


def extract_criteria(question: str) -> AdviceCriteria:
    text = normalize(question)
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
    if re.search(r'-\s*\d|\b(sans limite|illimite)\b', text):
        raise ClarificationRequired('Indiquez un budget positif et précis.')
    amounts = re.findall(number + r'\s*(?:€|euros?\b|eur\b)', text)
    if not amounts:
        amounts = re.findall(r'\b(?:budget|moins de|maximum|maxi|max|plafond)\s*(?::|de)?\s*' + number, text)
    values = {float(amount.replace(',', '.')) for amount in amounts}
    if len(values) > 1 or re.search(r'\b(entre|a partir de|au moins|plus de)\s*\d', text):
        raise ClarificationRequired('Précisez un seul plafond de budget ; les intervalles et budgets minimum ne sont pas pris en charge.')
    maximum = next(iter(values), None)
    if maximum is not None and (not math.isfinite(maximum) or maximum <= 0):
        raise ClarificationRequired('Indiquez un budget strictement positif.')
    if re.search(r'\b(mon|ce|le meme) budget\b', text) and maximum is None:
        raise ClarificationRequired('Quel est votre budget maximum ?')
    if maximum is None and re.search(r'\b(budget|moins de|maximum|maxi)\b', text):
        raise ClarificationRequired('Quel est votre budget maximum, en chiffres ?')
    unavailable = bool(re.search(r'\bindisponibles?\b|\ben rupture\b', text))
    available = bool(re.search(r'\bdisponibles?\b|\ben stock\b', text))
    if re.search(r"\b(pas|non)\s+(?:disponibles?|en stock)\b", text):
        unavailable, available = True, False
    if available and unavailable:
        raise ClarificationRequired('Souhaitez-vous les produits disponibles ou indisponibles ?')
    count_matches = list(re.finditer(r'\b(\d+|un|une|deux|trois|quatre|cinq)\s+(?:produits?|outils?|perceuses?|visseuses?|scies?|ponceuses?)\b', text))
    count_match = count_matches[-1] if count_matches else None
    counts = {'un': 1, 'une': 1, 'deux': 2, 'trois': 3, 'quatre': 4, 'cinq': 5}
    count = counts.get(count_match[1], None) if count_match else 3
    if count_match and count is None:
        count = int(count_match[1])
    if not 1 <= count <= 5:
        raise ClarificationRequired('Combien de produits souhaitez-vous comparer ? Choisissez entre 1 et 5.')
    if not categories and maximum is None and not (available or unavailable):
        raise ClarificationRequired('Quelle catégorie ou quel budget maximum souhaitez-vous ?')
    return AdviceCriteria(category=next(iter(categories), None), maximum=maximum,
                          exclusive_maximum=bool(re.search(r'\b(moins de|inferieur a)\b', text)),
                          available=True if available else False if unavailable else None,
                          limit=count)


class CatalogueAdviceUseCase:
    def __init__(self, products, llm):
        self.products = products
        self.llm = llm

    async def execute(self, question: str) -> dict:
        empty = {'recommendations': [], 'advice_status': 'CLARIFICATION_REQUIRED'}
        try:
            criteria = extract_criteria(question)
        except ClarificationRequired as error:
            return {**empty, 'response': str(error)}
        try:
            candidates = await self.products.find_advice_candidates(criteria)
            verified = []
            for product in candidates:
                current = await self.products.find_by_sku(product.sku)
                if current is not None and criteria.matches(current):
                    verified.append(current)
        except Exception:
            return {'recommendations': [], 'advice_status': 'CATALOGUE_UNAVAILABLE',
                    'response': 'Le catalogue ne peut pas être vérifié actuellement. Réessayez plus tard.'}
        # Recheck constraints even with another adapter; never trust the LLM for selection.
        verified = sorted(verified, key=lambda p: (p.price, p.sku))[:criteria.limit]
        if not verified:
            return {'recommendations': [], 'advice_status': 'NO_RESULTS',
                    'response': 'Aucun produit vérifiable du catalogue ne correspond à ces critères. Vous pouvez modifier le budget ou la catégorie.'}
        recommendations = []
        llm_available = True
        shared_order = None
        for product in verified:
            facts = {
                'categorie': f'Catégorie catalogue : {product.category.value}.',
                'prix': f'Prix catalogue : {product.price:g}.' + (
                    f' Il respecte le plafond {"strict" if criteria.exclusive_maximum else "inclusif"} de {criteria.maximum:g}.'
                    if criteria.maximum is not None else ''),
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
            recommendations.append({'sku': product.sku, 'product_name': product.name,
                                    'category': product.category.value, 'price': product.price,
                                    'stock': product.stock, 'explanation': ' '.join(facts[key] for key in order)})
        return {'recommendations': recommendations, 'advice_status': 'RESULTS',
                'response': f'{len(recommendations)} produit(s) du catalogue correspondent aux critères. '
                'Seuls la catégorie, le prix numérique et le stock sont vérifiés ; la devise et les caractéristiques techniques ne sont pas renseignées. '
                'La disponibilité est un instantané.' + (
                    ' Présentation déterministe : le modèle est indisponible ou sa réponse a été refusée.' if not llm_available else '')}
