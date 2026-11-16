"""Bounded documentary reads precede catalogue heuristics, never business actions."""

import re
from typing import Literal

from stockpilot.application.use_cases.catalogue_advice import normalize
from stockpilot.application.use_cases.resolve_product import explicit_skus


_DOCUMENT_NOUN = (
    r"(?:documents?|documentation|procedures?|politiques?|polic(?:y|ies))"
)
# A grammatical documentary target, not "manual" as a tool adjective, a
# physical drilling guide, or Documentation Pro as an unqualified product name.
_TARGET = (
    r"(?:(?:le|la|les|un|une|des|notre|nos|votre|vos|ce|cette|ces|"
    r"the|our|your|my|this|these|company|corporate) "
    r"(?:(?:available|company|corporate) )?|"
    r"(?:(?:my|our|the) )?(?:organization|organisation|company)'s )"
    + _DOCUMENT_NOUN + r"\b"
)
_DOCUMENT = re.compile(r"\b" + _TARGET)
_SOURCE = re.compile(r"^(?:selon|d'apres|according to) " + _TARGET)
_QUESTION = re.compile(
    r"^(?:comment|pourquoi|quand|combien|quel(?:le)?s?|qui|que|qu'|"
    r"how|why|when|what|which|who|does|do|is|are)\b"
)
_READ = re.compile(
    r"^(?:(?:peux|pourrais|pouvez|pourriez)[- ](?:tu|vous) (?:me )?)?"
    r"(?:resume(?:r)?|explique(?:r)?|decri(?:s|re)|synthetise(?:r)?|lis)\b|"
    r"^(?:(?:can|could|would) you )?(?:please )?"
    r"(?:summari[sz]e|explain|describe|outline|read)\b|"
    r"^(?:que (?:dit|disent|prevoit|prevoient)|qu'indiquent?|what (?:does|do)) "
    r"(?:(?:le|la|les|nos|notre|ce|cette|ces|the|our|this|these) )?"
    + _DOCUMENT_NOUN + r"\b|"
    r"^(?:selon|d'apres|according to) " + _TARGET
)
# Detect a second request, not a topic such as "procédure d'annulation".
# Unsupported mixtures are clarified rather than partially executed.
_ACTION = (
    r"(?:commande(?:r)?|achete(?:r)?|reserve(?:r)?|prepare(?:r)?|"
    r"confirme(?:r)?|annule(?:r)?|passe(?:r)?|place|order|buy|reserve|prepare|confirm|cancel|"
    r"montre|affiche|cherche|trouve|compare|liste|verifie|donne|"
    r"show|list|find|search|check|give)\b"
)
_ACTION_MATCH = re.compile(r"\b" + _ACTION)
_WISH = r"(?:je (?:veux|souhaite|voudrais)|j'aimerais|i (?:want|would like) to)\s+"
_POLITE = (
    r"(?:(?:peux|pourrais|pouvez|pourriez)[- ](?:tu|vous) (?:me |nous )?|"
    r"(?:can|could|would) you )?(?:please |svp |stp )?"
)
_SECOND_REQUEST = re.compile(
    r"(?:[,:;.!?&]|\b(?:et|puis|ensuite|and|then|also)\b)\s*"
    + r"(?:" + _WISH + r")?" + _POLITE + _ACTION
    + r"|\b" + _WISH + _ACTION
)
_CURRENT = re.compile(
    r"\b(?:actuel(?:le)?s?|courant(?:e)?s?|maintenant|aujourd'hui|"
    r"en temps reel|current|currently|now|today|real[- ]time)\b"
)
_BUSINESS_VALUE = re.compile(
    r"\b(?:stocks?|disponibilite|disponibles?|prix|quantites?|"
    r"availability|available|prices?|quantit(?:y|ies))\b"
)


def documentary_route(
    question: str,
) -> Literal["DOCUMENTARY", "DOCUMENTARY_CLARIFICATION"] | None:
    """Require an explicit read purpose and documentary target in the same request.

    This does not classify every natural-language question. Non-matching requests
    retain the existing routing. Callers keep exact history/citation, current
    catalogue-price and explicit ORDER routes ahead of this bounded rule.
    """
    normalized = normalize(question)
    value = " ".join(normalized.split())
    # A quoted product name is not an instruction or a documentary target. The
    # original question/identity is still passed untouched to the chosen route.
    surface = re.sub(r'"[^"\n]*"|«[^»\n]*»', "", value)
    if not _READ.search(surface) or not _DOCUMENT.search(surface):
        return None
    # A second line remains a clause boundary, not whitespace joining its action
    # to the documentary request. This changes recognition only, never the input.
    clauses = " ".join(normalized.replace("\n", ";").replace("\r", ";").split())
    if _SECOND_REQUEST.search(clauses):
        return "DOCUMENTARY_CLARIFICATION"
    source = _SOURCE.match(value)
    if source:
        remainder = value[source.end():].lstrip(" ,:")
        if not _QUESTION.match(remainder):
            # A qualified title may precede a comma and the actual question:
            # "Selon le document synthétique, quelle ...". A source prefix alone
            # does not turn an imperative such as "buy" into a documentary read.
            title, comma, remainder = remainder.partition(",")
            if not comma or _ACTION_MATCH.search(title) or not _QUESTION.match(remainder.strip()):
                return "DOCUMENTARY_CLARIFICATION"
    # A document is not the authority for live inventory/prices. Even without a
    # temporal adjective, asking for an identified SKU's availability is mixed.
    if _BUSINESS_VALUE.search(value) and (
        _CURRENT.search(value) or explicit_skus(question)
    ):
        return "DOCUMENTARY_CLARIFICATION"
    return "DOCUMENTARY"
