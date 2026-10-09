import re


def normalize_order_confirmation(message: str) -> str | None:
    normalized = message.strip().lower().replace("’", "'")
    normalized = re.sub(r"[^\w\s']", " ", normalized)
    normalized = " ".join(normalized.split())

    confirmations = {
        "oui",
        "oui je confirme",
        "je confirme",
        "confirme",
        "c'est bon",
        "ok je confirme",
    }
    cancellations = {
        "non",
        "non annule",
        "annule",
        "j'annule",
        "non merci",
    }

    if normalized in confirmations:
        return "confirmed"
    if normalized in cancellations:
        return "cancelled"
    return None
