"""Bounded ORDER grammar: model quantities are hints, never user evidence.

Only a count at the request prefix (or an entire quantitative continuation)
can supply a quantity. SKU spans and physical specifications are opaque.
Other standalone numbers are ambiguous, not additional inferred counts.
Unsupported request grammar requires clarification rather than a guessed count.
"""
import re
from dataclasses import dataclass, asdict

from stockpilot.application.order_quantity_errors import InvalidOrderQuantityError
from stockpilot.application.use_cases.resolve_product import SKU_PATTERN

_ACTION = r"(?:commander|acheter|r[eé]server)"
_PREFIX = re.compile(
    r"^\s*(?:"
    r"(?:(?:je\s+(?:veux|voudrais|souhaite)|j['’]aimerais)\s+)?" + _ACTION
    + r"|je\s+(?:commande|ach[eè]te|r[eé]serve)"
    + r"|(?:peux|pourrais|pouvez|pourriez)[- ](?:tu|vous)\s+(?:(?:me|nous)\s+)?" + _ACTION
    + r"|(?:tu|vous)\s+(?:peux|pourrais|pouvez|pourriez)\s+(?:(?:me|nous)\s+)?" + _ACTION
    + r"|(?:serait[- ]il|est[- ]il)\s+possible\s+de\s+" + _ACTION
    + r"|(?:commande|ach[eè]te|r[eé]serve)(?:z)?(?:[- ]moi)?"
    + r")\s+", re.I,
)
_CONTINUATION_START = (
    r"^\s*(?:et si j['’]en (?:demande|commande)|"
    r"(?:est ce que|est-ce que) je peux (?:demander|commander)|"
    r"(?:et )?(?:je voudrais|je veux|j['’]en veux|plut[oô]t|finalement))\s+"
)
_CONTINUATION_PREFIX = re.compile(_CONTINUATION_START, re.I)
_CONTINUATION = re.compile(
    _CONTINUATION_START +
    r"(?P<count>[+\-−]?\d+(?:[.,]\d+)?(?:[eE][+\-]?\d+)?(?:[ \u00a0\u202f]\d+)*|"
    r"(?:une\s+)?(?:demi[- ]douzaine|douzaine)|(?:un|une|deux|trois|quatre|cinq|six|sept|huit|neuf|dix)(?:\s+(?:et\s+demi|demi|dizaine|centaine|mille))?|beaucoup|plusieurs)"
    r"(?:\s+(?:unit[eé]s?|exemplaires?))?\s*[?!.]?\s*$", re.I,
)
_WORDS = {'un': 1, 'une': 1, 'deux': 2, 'trois': 3, 'quatre': 4,
          'cinq': 5, 'six': 6, 'sept': 7, 'huit': 8, 'neuf': 9, 'dix': 10,
          'douzaine': 12, 'une douzaine': 12, 'demi-douzaine': 6, 'une demi-douzaine': 6}
# Numbers are inspected only inside a recognized ORDER product phrase, after
# shielding explicit references and units; they are never harvested as quantities.
_NUMERIC_ATTEMPT = re.compile(r"(?<![\w-])[+\-−]?(?:\d|[.,]\d)|[½¼¾⅓⅔⅛]")
_NUMBER = re.compile(r"(?<![\w-])[+\-−]?\d+(?:[.,]\d+)?(?:[eE][+\-]?\d+)?(?![\w-])")
_SPEC = re.compile(r"[+\-−]?\d+(?:[.,]\d+)?\s*(?:mm|cm|m|m[eè]tres?|v|w|kw|ah|mah|kg|g|hz|tr/min)\b", re.I)
_HEAD = re.compile(r"(?P<count>(?>[+\-−]?\d+(?:[.,]\d+)?(?:[eE][+\-]?\d+)?|"
                   r"une|un|deux|trois|quatre|cinq|six|sept|huit|neuf|dix))(?=\s|$|[?!.,](?:\s|$))", re.I)
_LEXICAL_HEAD = re.compile(r'(?P<count>(?:une\s+)?(?:demi[- ]douzaine|douzaine))(?=\s+de\b)', re.I)
_UNSUPPORTED_WORD = re.compile(
    r'(?:[½¼¾⅓⅔⅛]|exactement|environ|en tout|[àa] peu pr[eè]s|approximativement|au moins|au plus|un\b|une\b|deux|trois|quatre|cinq|six|sept|huit|neuf|dix|z[eé]ro|moins|demi|douzaines?|paires?|couples?|trios?|lots?|packs?|paquets?|bo[iî]tes?|poign[eé]es?|quelques|plusieurs|beaucoup|une?\s+(?:dizaine|centaine)|quantit[eé]|'
    r'onze|douze|treize|quatorze|quinze|seize|dix[- ]sept|dix[- ]huit|dix[- ]neuf|'
    r'vingt|trente|quarante|cinquante|soixante|cent|cents|mille)\b', re.I,
)
_COUNT_LEXEME = re.compile(
    r"\b(?:un|une|deux|trois|quatre|cinq|six|sept|huit|neuf|dix|onze|douze|"
    r"treize|quatorze|quinze|seize|vingt|trente|quarante|cinquante|soixante|"
    r"cent|cents|mille|demi[- ]douzaine|douzaines?|dizaines?|centaines?|"
    r"paires?|couples?|trios?|lots?|packs?|paquets?|bo[iî]tes?|poign[eé]es?|"
    r"z[eé]ro|quelques|plusieurs|beaucoup)\b", re.I,
)
_OTHER_COUNT = re.compile(
    r'\b(?:et|ou|puis|plus|voire)\s+(?P<count>[+\-−]?\d+(?:[.,]\d+)?(?:[eE][+\-]?\d+)?|'
    r'un\b|une\b|deux\b|trois\b|quatre\b|cinq\b|six\b|sept\b|huit\b|neuf\b|dix\b)', re.I,
)
_QUANTITY_MARKER = re.compile(
    r"\b(?:quantit[eé]|par\s+(?:douzaines?|paires?|lots?)|"
    r"en\s+(?:lots?|packs?|paquets?)|(?:exactement|environ|en tout|au moins|au plus)\s+"
    r"(?:[+\-−]?\d|un\b|une\b|deux\b|trois\b|quatre\b|cinq\b|six\b|sept\b|huit\b|neuf\b|dix\b))", re.I,
)
_WORD_COMPOUND = re.compile(r"[,.]?\s+(?:(?:gros|grosse|petit|petite|grand|grande)\s+)?(?:et\s+demi|demi|douzaines?|paires?|couples?|trios?|lots?|packs?|paquets?|bo[iî]tes?|poign[eé]es?|dizaine|centaine|mille|cent|cents|vingt|ou|plus|voire|[àa])\b", re.I)


@dataclass(frozen=True)
class QuantityEvidence:
    message: str
    start: int | None
    end: int | None
    quantity: int
    kind: str  # explicit / default

    def to_dict(self):
        return asdict(self)


def strict_quantity(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise InvalidOrderQuantityError('La quantité doit être un entier strictement positif.')
    return value


def _integer(token: str) -> int:
    word = _WORDS.get(' '.join(token.casefold().split()).replace('demi douzaine', 'demi-douzaine'))
    if word is not None:
        return word
    # Validate the entire lexeme before conversion; no decimals, exponent,
    # grouping, signs lost, floats or partially consumed numeric tokens.
    if not re.fullmatch(r'[+\-−]?[0-9]{1,18}', token):
        raise InvalidOrderQuantityError('La quantité demandée doit être un entier strictement positif.')
    return strict_quantity(int(token.replace('−', '-')))


def _protected_spans(text: str) -> list[tuple[int, int]]:
    references = [
        m.span() for m in SKU_PATTERN.finditer(text)
        if any(c.isalpha() for c in m.group())
        and not re.fullmatch(r'[+\-−]?\d+(?:[.,]\d+)?[eE][+\-]?\d+', m.group())
    ]
    return references + [m.span() for m in _SPEC.finditer(text)]


def _shielded(span: tuple[int, int], protected: list[tuple[int, int]]) -> bool:
    return any(span[0] >= left and span[1] <= right for left, right in protected)


def quantitative_continuation(message: str):
    """Detect a quantity attempt separately from parsing a valid whole count.

    An ambiguous/malformed quantitative tail must disarm an old preparation;
    it must not become an unrelated UNKNOWN turn. Product model fragments
    beginning with letters remain outside this bounded detector.
    """
    if _PREFIX.match(message):
        return None  # A complete ORDER request uses its own quantity slot.
    prefix = _CONTINUATION_PREFIX.match(message)
    tail = message[prefix.end():] if prefix else ""
    if prefix and (_HEAD.match(tail) or _UNSUPPORTED_WORD.match(tail)):
        return prefix
    if prefix:
        protected = _protected_spans(tail)
        # A residual quantitative marker is an uncertain role, not a count to
        # extract. Scan only this bounded continuation, shielding product refs
        # and measures; modifiers cannot leave the old operation confirmable.
        for pattern in (_NUMERIC_ATTEMPT, _UNSUPPORTED_WORD):
            if any(not _shielded(m.span(), protected) for m in pattern.finditer(tail)):
                return prefix
    return None


def inspect_quantity(message: str) -> QuantityEvidence:
    if len(message) > 4096:
        raise InvalidOrderQuantityError('Précisez une quantité entière et un seul produit.')
    continuation = quantitative_continuation(message)
    if continuation:
        valid = _CONTINUATION.fullmatch(message)
        if valid is None:
            raise InvalidOrderQuantityError('La quantité est ambiguë ou malformée. Précisez un seul entier et préparez à nouveau la commande.')
        start, end = valid.span('count')
        return QuantityEvidence(message, start, end, _integer(message[start:end]), 'explicit')
    prefix = _PREFIX.match(message)
    if not prefix:
        raise InvalidOrderQuantityError('Précisez la quantité et le produit dans une demande de commande explicite.')
    start = prefix.end()
    body = message[start:]
    protected = _protected_spans(body)
    def shielded(a, b):
        return _shielded((a, b), protected)
    lexical = _LEXICAL_HEAD.match(body)
    head = lexical or _HEAD.match(body)
    count = None
    if head and (lexical or not shielded(*head.span('count'))):
        token = head.group('count')
        # "un et demi" and "deux mille" are not the integer prefix.
        if _WORD_COMPOUND.match(body, head.end()) or re.match(r"\s*[½¼¾⅓⅔⅛]", body[head.end():]):
            raise InvalidOrderQuantityError('La quantité est ambiguë. Indiquez un entier sans séparateurs.')
        count = _integer(token)
    elif _UNSUPPORTED_WORD.match(body):
        raise InvalidOrderQuantityError('Précisez la quantité entière souhaitée.')
    if re.match(r"[+\-−]?(?:\d|[.,]\d)|[½¼¾⅓⅔⅛]", body) and count is None and not any(left == 0 for left, _ in protected):
        raise InvalidOrderQuantityError('Précisez une quantité entière sans notation ambiguë.')
    if any(not shielded(*m.span()) for m in re.finditer(r"[½¼¾⅓⅔⅛]", body)):
        raise InvalidOrderQuantityError('La quantité ou la mesure fractionnaire est incertaine. Précisez une quantité entière et le produit.')
    for marker in _QUANTITY_MARKER.finditer(body):
        if not shielded(*marker.span()):
            raise InvalidOrderQuantityError('La position de la quantité ou le conditionnement est incertain. Précisez une quantité entière avant le SKU.')
    for other in _OTHER_COUNT.finditer(body):
        if not shielded(*other.span('count')):
            raise InvalidOrderQuantityError('Précisez une quantité et un seul produit par commande.')
    for word in _COUNT_LEXEME.finditer(body):
        if shielded(*word.span()):
            continue
        if count is not None and _shielded(word.span(), [head.span('count')]):
            continue
        raise InvalidOrderQuantityError('Une expression quantitative reste incertaine. Précisez une quantité entière avant le SKU.')
    for number in _NUMBER.finditer(body):
        if shielded(*number.span()):
            continue
        if count is not None and number.span() == head.span('count'):
            continue
        raise InvalidOrderQuantityError('Plusieurs nombres ou une quantité non identifiée : précisez la quantité et un seul produit.')
    if count is None:
        return QuantityEvidence(message, None, None, 1, 'default')
    return QuantityEvidence(message, start + head.start('count'), start + head.end('count'), count, 'explicit')


def concordant_quantity(message: str, extracted, inherited: dict | None = None) -> QuantityEvidence:
    evidence = validate_evidence(inherited) if inherited is not None else inspect_quantity(message)
    if extracted is not None and strict_quantity(extracted) != evidence.quantity:
        raise InvalidOrderQuantityError('La quantité extraite ne correspond pas à votre demande. Précisez la quantité entière souhaitée.')
    return evidence


def validate_evidence(evidence: dict) -> QuantityEvidence:
    """Recompute persisted evidence; a restored scalar alone is insufficient."""
    if not isinstance(evidence, dict) or not isinstance(evidence.get('message'), str):
        raise InvalidOrderQuantityError('Préparez à nouveau la commande avec une quantité explicite.')
    original = inspect_quantity(evidence['message'])
    if original.to_dict() != evidence or type(evidence.get('quantity')) is not int:
        raise InvalidOrderQuantityError('La quantité du checkpoint ne correspond pas à la demande.')
    return original


def explicit_order_request(message: str) -> bool:
    return _PREFIX.match(message) is not None
