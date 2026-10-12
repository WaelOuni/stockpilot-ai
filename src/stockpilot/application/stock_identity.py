class StockIdentityRequiredError(RuntimeError):
    """Stock restitution cannot prove the historical document identity."""

    code = "MANUAL_INTERVENTION_REQUIRED"

    def __init__(self) -> None:
        super().__init__(
            "L'identité documentaire du produit réservé n'est pas vérifiable ; "
            "une intervention manuelle est nécessaire."
        )
