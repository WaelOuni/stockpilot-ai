import re
import unicodedata


_TOOLING_SIGNAL = re.compile(
    r"\b(?:perceuses?|drills?|visseuses?|screwdrivers?|scies?|saws?|"
    r"ponceuses?|sanders?|"
    r"perc(?:er|e|es|ent|ez|ons|age|ages|ant)|"
    r"(?:de)?viss(?:er|e|es|ent|ez|ons|age|ages|ant)|"
    r"coup(?:er|e|es|ent|ez|ons|ant)|"
    r"sci(?:er|e|es|ent|ez|ons|age|ages|ant)|"
    r"ponc(?:er|e|es|ent|ez|ons|age|ages|ant))\b"
)


def has_supported_tooling_signal(message: str) -> bool:
    normalized = "".join(
        character for character in unicodedata.normalize("NFKD", message.casefold())
        if not unicodedata.combining(character)
    )
    return _TOOLING_SIGNAL.search(normalized) is not None
