def validate_customer_id(customer_id: str | None) -> None:
    """None is reserved for technically created, unowned legacy orders."""
    if customer_id is not None and (
        not isinstance(customer_id, str) or not customer_id.strip()
    ):
        raise ValueError("L'identifiant client ne doit pas être vide.")
