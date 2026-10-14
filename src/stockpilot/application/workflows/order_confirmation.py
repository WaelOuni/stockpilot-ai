import re

from stockpilot.domain.enums.intent import Intent


def confirmation_proposal(pending_order: dict) -> dict:
    """Bind eligibility to the exact newly prepared operation, not its status."""
    return {key: pending_order.get(key) for key in (
        "operation_id", "customer_id", "product_name", "product_sku",
        "product_document_id", "quantity",
    )}


def has_active_confirmation(state: dict, pending_order: dict) -> bool:
    proposal = state.get("active_order_confirmation")
    return (
        isinstance(proposal, dict)
        and type(proposal.get("quantity")) is int
        and isinstance(proposal.get("operation_id"), str)
        and bool(proposal["operation_id"].strip())
        and proposal == confirmation_proposal(pending_order)
        and proposal.get("customer_id") == state.get("customer_id")
        and state.get("intent") == Intent.ORDER
        and state.get("last_relevant_intent") == Intent.ORDER
        and state.get("context_customer_id") == state.get("customer_id")
    )


def normalize_order_confirmation(message: str) -> str | None:
    normalized = message.strip().lower().replace("’", "'")
    normalized = re.sub(r"[^\w\s']", " ", normalized)
    normalized = " ".join(normalized.split())

    confirmations = {
        "oui",
        "oui je confirme",
        "oui confirme",
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
