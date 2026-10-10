import re
import unicodedata


def is_explicit_operation_id(message: str, identifier: str) -> bool:
    normalized = "".join(
        character for character in unicodedata.normalize("NFKD", message.casefold())
        if not unicodedata.combining(character)
    ).replace("’", "'")
    label = (
        r"\b(?:operation[_\s]+id|operation\s+identifier|"
        r"(?:identifiant|id|numero)\s+(?:d['\s]+|de\s+l['\s]+)operation)\b"
    )
    return re.search(
        label + r"(?:\s+(?:est|is))?\s*[:=]?\s*['\"]?"
        + re.escape(identifier.casefold()) + r"(?![\w-])",
        normalized,
    ) is not None
