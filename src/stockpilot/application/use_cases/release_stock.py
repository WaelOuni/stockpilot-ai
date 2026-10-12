from stockpilot.application.stock_identity import StockIdentityRequiredError
from stockpilot.application.ports.product_repository import ProductRepositoryPort


class ReleaseStockUseCase:
    def __init__(self, product_repository: ProductRepositoryPort) -> None:
        self._product_repository = product_repository

    async def execute(self, product_name: str, quantity: int) -> bool:
        normalized_name = product_name.strip()
        if not normalized_name:
            raise ValueError("Le nom du produit est obligatoire.")
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
            raise ValueError("La quantité doit être un entier strictement positif.")

        # Restitution belongs to the idempotent order-cancellation transaction,
        # where the persisted reservation document identity can be verified.
        raise StockIdentityRequiredError()
