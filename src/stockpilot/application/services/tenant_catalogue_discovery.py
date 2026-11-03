"""B2B lexical discovery. Aliases select persisted values, never rewrite identities."""

import re
from dataclasses import dataclass

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
    text = normalize(question)
    # Remove only monetary amounts before testing stock-specific scaffolding.
    text = re.sub(r"\d+(?:[.,]\d+)?\s*(?:euros?\b|eur\b|€)", "", text)
    if re.search(r"\b(?:rupture|stocks? faibles?|presque epuises?|s'epuiser)\b", text):
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


def low_stock_criteria(question, categories):
    text = normalize(question).strip().rstrip(".?!")
    if re.search(r"€|\b(?:euros?|eur|inactifs?)\b", text):
        raise ClarificationRequired(
            "Précisez un intervalle de stock disponible pour les produits actifs, sans budget."
        )
    matches = _stock_matches(text)
    rupture = bool(re.search(r"\b(?:en )?rupture(?: de stock)?\b", text))
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
        r"\b(?:en )?rupture(?: de stock)?\b|\bpresque epuises?\b|\b(?:qui )?vont s'epuiser\b",
        "", remainder,
    )
    remainder = re.sub(
        r"\b(?:quels|quelles|sont|les|des|un|une|produits?|articles?|unites?|liste|avec|qui|ont|ayant|en|de|stocks?|faibles?|disponibles?|montre(?:[- ]moi)?|affiche[- ]moi)\b",
        " ", remainder,
    )
    remainder = " ".join(remainder.replace(",", " ").split())
    selected = persisted_categories(remainder, categories) if remainder else ()
    if remainder and not selected:
        raise ClarificationRequired(
            "Catégorie inconnue : choisissez une catégorie de votre catalogue."
        )
    return LowStockCriteria(maximum, label, selected, minimum)
