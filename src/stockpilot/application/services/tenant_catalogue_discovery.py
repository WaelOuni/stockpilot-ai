"""B2B lexical discovery. Aliases select persisted values, never rewrite identities."""

import re
from dataclasses import dataclass
from decimal import Decimal

from stockpilot.application.use_cases.catalogue_advice import (
    ClarificationRequired,
    normalize,
)

CATEGORY_ALIASES = {
    "DRILL": ("drill", "perceuse", "perceuses"),
    "SCREWDRIVER": ("screwdriver", "visseuse", "visseuses"),
    "SANDER": ("sander", "ponceuse", "ponceuses"),
    "SAW": ("saw", "scie", "scies"),
}

# These phrases describe an observation, never a forecast or an identity. Keep
# the same spans for routing and stripping so that unknown qualifiers survive.
_LOW_SIGNAL = (
    r"\b(?:bientot (?:etre )?epuises?|presque (?:epuises?|en rupture)|"
    r"risquent d'etre epuises?|(?:vont )?(?:bientot )?etre en rupture(?: de stock)?|"
    r"commencent a manquer|tres peu de stock(?: disponible)?|"
    r"risquent la rupture|n'ont presque plus de stock|au bord de la rupture|"
    r"(?:vont )?s'epuiser|stocks? faibles?)\b"
)

_MONEY = re.compile(
    r"(?:(?:avec un prix(?: de)?|(?:et )?cout(?:ent|ant)|qui coutent|"
    r"(?:et )?sont dans un budget de|(?:pour un )?budget de)\s+)?"
    r"(?P<bound>moins de|inferieur a|au plus|maximum|sous)?\s*"
    r"(?P<amount>\d+(?:[.,]\d{1,2})?)\s*(?:euros?\b|eur\b|€)(?:\s+(?P<maximum>maximum))?"
)


def stock_budget(question):
    matches = list(_MONEY.finditer(question))
    if not matches:
        return question, None
    if len(matches) != 1:
        raise ClarificationRequired("Précisez un seul plafond de prix en EUR.")
    match = matches[0]
    amount = Decimal(match['amount'].replace(',', '.'))
    if amount <= 0 or amount * 100 > 2**63 - 1:
        raise ClarificationRequired("Le plafond de prix en EUR doit être positif et borné.")
    maximum = int(amount * 100) - int(match['bound'] in ('moins de', 'inferieur a', 'sous'))
    return (question[:match.start()] + ' ' + question[match.end():]).strip(), maximum


def catalogue_question(question):
    """Normalize request scaffolding only; never rewrite a quoted name/SKU.

    This B2B grammar is independent of ORDER and of the historical assistant.
    Unknown suffixes are deliberately left to the existing exact resolver.
    """
    from stockpilot.application.use_cases.resolve_product import explicit_skus
    category = re.fullmatch(r'\s*Montre les produits de la cat[eé]gorie ["«](.+)["»][.!?]?\s*', question, re.I)
    if category:
        return "Je cherche " + category[1]
    if re.search(r'["«»]', question) or explicit_skus(question):
        return question
    value = normalize(question)
    bare = value.rstrip('.?! ')
    if re.fullmatch(r'(?:seulement celles|toujours) disponibles', bare):
        return 'Seulement celles qui sont disponibles'
    if re.fullmatch(r'quel est son (?:stock|prix)', bare):
        return 'ce produit'
    if bare == 'retourne au catalogue complet':
        return 'Je veux voir les produits actifs'
    if re.fullmatch(r'et si je retire la limite de prix', bare):
        return 'Et sans plafond'
    value = re.sub(r'^je prefere les (?=(?:perceuses?|visseuses?|scies?|ponceuses?)\b)', 'je cherche des ', value)
    value = re.sub(r'^maintenant a (?=moins de \d)', 'et uniquement a ', value)
    if re.match(r'^avec un budget\b', value):
        value = 'et ' + value
    value = re.sub(r"\bune bonne (?=(?:perceuse|visseuse|scie|ponceuse)\b)", "une ", value)
    value = re.sub(r"\bmoins cheres? que (?=\d)", "moins de ", value)
    value = re.sub(
        r"\b(?:avec un prix(?: de)?|dont le prix est|qui coutent|coutant|coutent)\s+",
        " ", value,
    )
    value = re.sub(r"\bau plus\s+(?=\d)", "maximum ", value)
    value = re.sub(r"\b(?:pour|avec) un budget\b", "budget", value)
    value = re.sub(r"\bpour (?=\d+(?:[.,]\d+)?\s*(?:euros?\b|eur\b|€))", "a ", value)
    value = re.sub(r"\b(?:et a|de) (?=moins de \d)", "a ", value)
    value = re.sub(r"\b(\d+(?:[.,]\d{1,2})?\s*(?:euros?|eur|€)) de budget\b", r"budget de \1", value)
    value = re.sub(r"\bavec budget\b", "budget", value)
    value = re.sub(r"^(?:liste|as[- ]tu)\s+", "montre ", value)
    value = re.sub(r"\b(?:retire la limite de prix|retire le plafond de prix)\b", "retire le budget", value)
    return " ".join(value.split())


def stock_question(question):
    value = " ".join(normalize(question).split())
    for word, number in (("zero", "0"), ("cinq", "5"), ("quinze", "15")):
        value = re.sub(r"\b" + word + r"(?=\s+unites?\b)", number, value)
    value = re.sub(r"\bzero stock\b", "0 unites", value)
    value = re.sub(r"^c koi\b", "quels sont", value)
    value = re.sub(r"^montre moa\b", "montre", value)
    value = re.sub(r"\bki ont moin de\b", "qui ont moins de", value)
    return re.sub(r"\s+(?:stp|svp)[.!?]?\s*$", "", value)


def aliases(value):
    value = normalize(value)
    return next(
        (group for group in CATEGORY_ALIASES.values() if value in group), (value,)
    )


def persisted_categories(value, categories):
    group = aliases(value)
    return tuple(c for c in categories if normalize(c) in group)


def custom_category_question(question, categories):
    """Let the existing grammar strip scaffolding while preserving unknown suffixes.

    Known families keep their historic lexical grammar. Only a literal category
    actually present in this tenant can stand in for the grammar's category token.
    Names/SKUs remain subject to the unchanged exact identity resolver.
    """
    text = normalize(question)
    if re.search(r'\bsku\b|["«»]', text):
        return question, None
    matches = [
        c
        for c in categories
        if aliases(c) == (normalize(c),)
        and re.search(r"(?<!\w)" + re.escape(normalize(c)) + r"(?!\w)", text)
    ]
    if not matches:
        return question, None
    groups = {normalize(c) for c in matches}
    if len(groups) != 1:
        raise ClarificationRequired("Choisissez une seule catégorie du catalogue.")
    phrase = next(iter(groups))
    return re.sub(
        r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", "perceuses", text, count=1
    ), tuple(matches)


# Ordered lexical spans keep stock counts separate from budgets, SKU/model
# digits, and ORDER quantities. Overlapping "ont 5 ... ou moins" uses the
# inclusive comparator, not a second equality.
_STOCK_PATTERNS = (
    ("range", r"\bentre\s+(?P<a>\d+)\s+et\s+(?P<b>\d+)\b"),
    ("lte", r"\b(?P<a>\d+)(?:\s+unites?)?(?:\s+disponibles?)?\s+ou\s+moins\b"),
    ("lt", r"\b(?:moins de|inferieure?s? a)\s+(?P<a>\d+)\b"),
    ("gte", r"\bau moins\s+(?P<a>\d+)\b"),
    ("gt", r"\b(?:plus de|superieure?s? a)\s+(?P<a>\d+)\b"),
    ("eq", r"\b(?:exactement|seulement|que)\s+(?P<a>\d+)\b"),
    ("eq", r"\b(?:ont|avec|ayant)\s+(?P<a>\d+)\b"),
)


def _stock_matches(text):
    chosen = []
    for kind, pattern in _STOCK_PATTERNS:
        for match in re.finditer(pattern, text):
            if not any(match.start() < old.end() and match.end() > old.start() for _, old in chosen):
                chosen.append((kind, match))
    return sorted(chosen, key=lambda pair: pair[1].start())


def is_low_stock_request(question):
    text = stock_question(question)
    from stockpilot.application.use_cases.resolve_product import explicit_skus
    if explicit_skus(question) or re.search(r'["«»]', question):
        return False
    # Remove only monetary amounts before testing stock-specific scaffolding.
    text = re.sub(r"\d+(?:[.,]\d+)?\s*(?:euros?\b|eur\b|€)", "", text)
    if re.search(_LOW_SIGNAL, text) or re.search(r"\b(?:rupture|stock est faible)\b", text):
        return True
    for _, match in _stock_matches(text):
        if re.search(r"\bstocks?\b", text):
            return True
        if re.search(r"\bunites?\b", match.group()):
            return True
        if re.match(r"\s+(?:unites?(?:\s+disponibles?)?|disponibles?)\b", text[match.end():]):
            return True
    return False


@dataclass(frozen=True)
class LowStockCriteria:
    maximum: int | None
    label: str
    categories: tuple[str, ...]
    minimum: int = 0
    maximum_price_minor: int | None = None


def low_stock_criteria(question, categories):
    text = stock_question(question).rstrip(".?!")
    text, maximum_price = stock_budget(text)
    if re.search(r"€|\b(?:euros?|eur|inactifs?|dollars?|usd)\b", text):
        raise ClarificationRequired(
            "Précisez un intervalle de stock disponible pour les produits actifs, sans budget."
        )
    # Number words are accepted only in stock constraints, not product names.
    text = re.sub(r"\bzero(?=\s+unite)", "0", text)
    text = re.sub(r"\bzero stock\b", "0 unites", text)
    exclude_zero = bool(re.search(r"\bmais pas zero\b", text))
    text = re.sub(r"\bmais pas zero\b", "", text).rstrip(" ,")
    matches = _stock_matches(text)
    text_without_low = re.sub(_LOW_SIGNAL, " ", text)
    rupture = bool(re.search(r"\b(?:en )?rupture(?: de stock)?\b", text_without_low))
    if len(matches) + int(rupture) > 1:
        raise ClarificationRequired("Précisez un seul intervalle de stock disponible.")
    minimum = 0
    if matches:
        kind, match = matches[0]
        value = int(match["a"])
        if kind == "range":
            minimum, maximum = value, int(match["b"])
            if minimum > maximum:
                raise ClarificationRequired("Le minimum disponible dépasse le maximum.")
            label = f"{minimum} ≤ disponible ≤ {maximum}"
        elif kind == "eq":
            minimum = maximum = value
            label = f"disponible = {value}"
        elif kind == "lt":
            maximum, label = value - 1, f"disponible < {value}"
        elif kind == "lte":
            maximum, label = value, f"disponible ≤ {value}"
        elif kind == "gt":
            minimum, maximum, label = value + 1, None, f"disponible > {value}"
        else:
            minimum, maximum, label = value, None, f"disponible ≥ {value}"
        if value > 2**63 - 1:
            raise ClarificationRequired("Le seuil de stock est trop élevé.")
    elif rupture:
        maximum, label = 0, "disponible = 0"
    else:
        maximum, label = 4, "disponible < 5 (seuil par défaut)"
    if minimum > 2**63 - 1 or maximum is not None and maximum > 2**63 - 1:
        raise ClarificationRequired("Le seuil de stock est trop élevé.")
    remainder = text
    for _, match in reversed(matches):
        remainder = remainder[:match.start()] + " " + remainder[match.end():]
    remainder = re.sub(
        _LOW_SIGNAL + r"|\b(?:en )?rupture(?: de stock)?\b|\bstock est faible\b",
        "", remainder,
    )
    remainder = re.sub(
        r"^(?:je (?:voulais|voudrais|veux)(?: savoir)?|je (?:re)?cherche|y a[- ]t[- ]il|"
        r"tu peux me|peux[- ]tu|montre[- ]moi|affiche[- ]moi)\s+", "", remainder,
    )
    remainder = re.sub(r"\bet uniquement ceux-la\b|\bceux\b", "", remainder)
    remainder = re.sub(
        r"\b(?:quels|quelles|sont|le|les|des|un|une|produits?|articles?|references?|unites?|liste|lister|avec|qui|ont|ayant|en|de|du|dont|la|categorie|mon catalogue|et|mais|a|stocks?|faibles?|disponibles?|montre(?:[- ]moi)?|affiche(?:[- ]moi)?)\b",
        " ", remainder,
    )
    remainder = " ".join(remainder.replace(",", " ").split())
    selected = persisted_categories(remainder, categories) if remainder else ()
    if remainder and not selected:
        raise ClarificationRequired(
            "Catégorie inconnue : choisissez une catégorie de votre catalogue."
        )
    if exclude_zero:
        minimum = max(1, minimum)
        if maximum is not None and maximum < minimum:
            raise ClarificationRequired("Les contraintes de stock se contredisent.")
        label += " ; hors ruptures"
    if maximum_price is not None:
        label += f" ; prix ≤ {Decimal(maximum_price) / 100} EUR"
    return LowStockCriteria(maximum, label, selected, minimum, maximum_price)
