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


def is_low_stock_request(question):
    text = normalize(question)
    # A monetary ceiling is never a stock threshold, even with "disponible".
    text = re.sub(
        r"(?:moins de|inferieur a)\s+\d+(?:[.,]\d+)?\s*(?:euros?\b|eur\b|€)", "", text
    )
    if re.search(r"\b(?:rupture|stocks? faibles?|presque epuises?|s'epuiser)\b", text):
        return True
    threshold = r"(?:moins de|inferieur a)\s+\d+|\d+ ou moins"
    if not re.search(threshold, text):
        return False
    if re.search(r"\bstocks?\b", text):
        return True
    # Bare ceilings in a buying search keep the historical budget contract.
    # A stock list without the word 'stock' needs explicit quantity scaffolding.
    return bool(
        re.search(r"\b(?:ont|avec)\s+(?:" + threshold + r")\s+disponibles?\b", text)
    )


@dataclass(frozen=True)
class LowStockCriteria:
    maximum: int
    label: str
    categories: tuple[str, ...]


def low_stock_criteria(question, categories):
    text = normalize(question).strip().rstrip(".?!")
    if re.search(r"€|\b(?:euros?|eur|inactifs?)\b", text):
        raise ClarificationRequired(
            "Précisez un seuil de stock disponible pour les produits actifs, sans budget."
        )
    strict = list(re.finditer(r"\b(?:moins de|inferieur a)\s+(\d+)\b", text))
    inclusive = list(re.finditer(r"\b(\d+) ou moins\b", text))
    rupture = bool(re.search(r"\b(?:en )?rupture(?: de stock)?\b", text))
    if len(strict) + len(inclusive) + int(rupture) > 1:
        raise ClarificationRequired("Précisez un seul seuil de stock disponible.")
    if strict:
        value = int(strict[0][1])
        maximum, label = value - 1, f"disponible < {value}"
    elif inclusive:
        value = int(inclusive[0][1])
        maximum, label = value, f"disponible ≤ {value}"
    elif rupture:
        maximum, label = 0, "disponible = 0"
    else:
        maximum, label = 4, "disponible < 5 (seuil par défaut)"
    if maximum > 2**63 - 1:
        raise ClarificationRequired("Le seuil de stock est trop élevé.")
    remainder = re.sub(r"\b(?:moins de|inferieur a)\s+\d+\b|\b\d+ ou moins\b", "", text)
    remainder = re.sub(
        r"\b(?:en )?rupture(?: de stock)?\b|\bpresque epuises?\b|\b(?:qui )?vont s'epuiser\b",
        "",
        remainder,
    )
    remainder = re.sub(
        r"\b(?:quels|quelles|sont|les|des|un|une|produits?|liste|avec|ont|en|de|stocks?|faibles?|disponibles?|montre[- ]moi|affiche[- ]moi)\b",
        " ",
        remainder,
    )
    remainder = " ".join(remainder.replace(",", " ").split())
    if not remainder:
        selected = ()
    else:
        selected = persisted_categories(remainder, categories)
        if not selected:
            # Never broaden an unknown category/name into a global stock list.
            raise ClarificationRequired(
                "Catégorie inconnue : choisissez une catégorie de votre catalogue."
            )
    return LowStockCriteria(maximum, label, selected)
