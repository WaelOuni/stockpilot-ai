"""Bounded, literal catalogue resolution; model output is only a grounded hint."""
import re
import unicodedata
from dataclasses import dataclass
from enum import Enum

from stockpilot.application.ports.product_repository import ProductRepositoryPort
from stockpilot.domain.entities.product import Product

CANDIDATE_LIMIT = 21
SKU_PATTERN = re.compile(r"(?<![\w-])[A-Za-z0-9]+(?:-[A-Za-z0-9]+)+(?![\w-])")


def normalize_name(value: str) -> str:
    value = "".join(c for c in unicodedata.normalize("NFKD", value.casefold())
                    if not unicodedata.combining(c))
    return " ".join(value.split())


def explicit_skus(message: str) -> list[str]:
    prefix = _ORDER_PREFIX.match(message)
    product_start = prefix.end() if prefix else 0
    references = list(dict.fromkeys(m.group() for m in SKU_PATTERN.finditer(message)
                              if m.start() >= product_start
                              and (any(c.isdigit() for c in m.group()) or m.group().isupper())))
    labeled = re.findall(r"\bsku\s*[:=]?\s*([A-Za-z0-9][A-Za-z0-9_-]*)", message, re.I)
    return list(dict.fromkeys(labeled + references))


# Parse request grammar only at the beginning. Product evidence is never
# reconstructed from a classifier hint or stripped by global replacements.
_ORDER_ACTION = r"(?:commander|acheter|r[eé]server)"
_ORDER_PREFIX = re.compile(
    r"^\s*(?:"
    r"(?:(?:je\s+(?:veux|voudrais|souhaite)|j['’]aimerais)\s+)?" + _ORDER_ACTION
    + r"|(?:peux|pourrais|pouvez|pourriez)[- ](?:tu|vous)\s+(?:(?:me|nous)\s+)?" + _ORDER_ACTION
    + r"|(?:tu|vous)\s+(?:peux|pourrais|pouvez|pourriez)\s+(?:(?:me|nous)\s+)?" + _ORDER_ACTION
    + r"|(?:serait[- ]il|est[- ]il)\s+possible\s+de\s+" + _ORDER_ACTION
    + r"|(?:commande|ach[eè]te|r[eé]serve)(?:[- ]moi)?"
    + r")\s+(?:(?:\d+|un|une)\s+)?(?:(?:la|le)\s+)?",
    re.I,
)


def order_product_phrase(value: str) -> str:
    """Remove a bounded request prefix; retain every product discriminant."""
    raw = _ORDER_PREFIX.sub("", value, count=1).strip()
    if _ORDER_PREFIX.match(value):
        # Reuse the validated quantity span rather than teaching a second
        # parser to remove number words. Import locally: quantity evidence
        # also uses this module's literal SKU pattern.
        from stockpilot.application.order_quantity_evidence import inspect_quantity
        from stockpilot.application.order_quantity_errors import InvalidOrderQuantityError

        try:
            evidence = inspect_quantity(value)
        except InvalidOrderQuantityError:
            evidence = None
        if evidence is not None and evidence.start is not None:
            token = value[evidence.start:evidence.end].casefold()
            if token not in {"un", "une"} and not re.fullmatch(r"[+\-−]?\d+", token):
                raw = value[evidence.end:].lstrip()
                if token.endswith("douzaine"):
                    # The existing quantity grammar requires this connector.
                    raw = re.sub(r"^de\s+", "", raw, count=1, flags=re.I)
    raw = raw.rstrip("?.!").strip()
    # Sentence punctuation is outside a quoted name; punctuation inside it is evidence.
    return raw[1:-1].strip() if len(raw) >= 2 and raw.startswith('"') and raw.endswith('"') else raw


class ResolutionStatus(str, Enum):
    RESOLVED = "RESOLVED"
    AMBIGUOUS = "AMBIGUOUS"
    NOT_FOUND = "NOT_FOUND"


@dataclass(frozen=True)
class ProductResolution:
    status: ResolutionStatus
    product: Product | None = None


class ResolveProductUseCase:
    def __init__(self, repository: ProductRepositoryPort):
        self.repository = repository

    async def execute(self, reference: str | None, message: str | None = None) -> ProductResolution:
        if (message is None and len(reference or "") > 256) or len(message or "") > 4096:
            return ProductResolution(ResolutionStatus.NOT_FOUND)
        # An explicit SKU is authoritative even if the model rewrites or omits it.
        skus = explicit_skus(message or reference or "")
        if skus:
            if len(skus) != 1:
                return ProductResolution(ResolutionStatus.AMBIGUOUS)
            products = await self.repository.find_resolution_skus(skus[0])
            if len(products) > 1:
                return ProductResolution(ResolutionStatus.AMBIGUOUS)
            return ProductResolution(ResolutionStatus.RESOLVED if products else ResolutionStatus.NOT_FOUND,
                                     products[0] if products else None)
        if not reference and not message:
            return ProductResolution(ResolutionStatus.NOT_FOUND)
        raw = order_product_phrase(message or reference or "")
        # Direct SKU references also come from the user phrase, never a shorter
        # classifier hint. Unknown compact alphanumeric codes are not names.
        products = await self.repository.find_resolution_skus(raw) if raw else []
        if len(products) > 1:
            return ProductResolution(ResolutionStatus.AMBIGUOUS)
        if products:
            return ProductResolution(ResolutionStatus.RESOLVED, products[0])
        if re.fullmatch(r"(?=.*[A-Za-z])(?=.*\d)[A-Za-z0-9_]+", raw):
            return ProductResolution(ResolutionStatus.NOT_FOUND)
        # Full literal name from the user wins over any shorter model hint.
        candidates = await self.repository.find_resolution_candidates(raw, exact=True, limit=2) if raw else []
        if len(candidates) > 1:
            return ProductResolution(ResolutionStatus.AMBIGUOUS)
        if candidates:
            return ProductResolution(ResolutionStatus.RESOLVED, candidates[0])
        # Model terms may help classify intent, but catalogue selection uses the
        # user's complete product evidence. Remove only request scaffolding and
        # generic tool nouns; unknown variants/specifications remain constraints.
        ignored = {
            "je", "veux", "voudrais", "souhaite", "aimerais", "j'aimerais",
            "commander", "commande", "commande-moi", "acheter", "reserver",
            "reserve", "merci", "moi", "de", "du", "la", "le", "les", "des",
            "un", "une", "et", "a", "pour", "svp", "s'il", "vous", "plait",
            "produit", "produits", "perceuse", "perceuses", "scie", "scies",
            "visseuse", "visseuses", "outil", "outils",
        }
        terms = normalize_name(raw).split()
        # Request grammar is a prefix, not a global stop-word filter. Once the
        # product phrase starts, even a token such as A or Reserve is evidence.
        removed = []
        while terms and terms[0] in ignored:
            removed.append(terms.pop(0))
        if removed and terms and terms[0].isdecimal():
            terms.pop(0)
        if not terms:
            return ProductResolution(ResolutionStatus.NOT_FOUND)
        candidates = await self.repository.find_resolution_candidates(
            " ".join(terms), exact=False, limit=CANDIDATE_LIMIT,
        )
        if len(candidates) >= CANDIDATE_LIMIT:
            return ProductResolution(ResolutionStatus.AMBIGUOUS)
        # Validate every returned candidate against the same literal evidence,
        # even for alternate repository implementations. A shorter exact hint
        # never relaxes user constraints such as 12V, Pro, Max or a model code.
        candidates = [p for p in candidates if all(re.search(
            r"(?<!\w)" + re.escape(term) + r"(?!\w)", normalize_name(p.name),
        ) for term in terms)]
        if len(candidates) > 1:
            return ProductResolution(ResolutionStatus.AMBIGUOUS)
        tokens = re.findall(r"\w+", " ".join(terms))
        meaningful = [t for t in tokens if t not in {"de", "du", "la", "le", "les", "des", "un", "une", "et", "a", "pour"}]
        discriminating = len(meaningful) >= 2 and (
            any(any(c.isdigit() for c in token) for token in meaningful) or len(meaningful) >= 3
        )
        if candidates and discriminating:
            return ProductResolution(ResolutionStatus.RESOLVED, candidates[0])
        return ProductResolution(ResolutionStatus.NOT_FOUND)
