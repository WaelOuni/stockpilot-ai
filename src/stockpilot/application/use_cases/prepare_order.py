from uuid import uuid4
from stockpilot.application.order_quantity_errors import InvalidOrderQuantityError
from stockpilot.application.order_quantity_evidence import concordant_quantity
from stockpilot.telemetry import traced
from stockpilot.metrics import record

from stockpilot.application.dto.prepared_order import PreparedOrder
from stockpilot.application.use_cases.check_stock import CheckStockUseCase, StockResult
from stockpilot.application.use_cases.resolve_product import ResolveProductUseCase, ResolutionStatus
from stockpilot.domain.enums.order_status import PreparedOrderStatus


class PrepareOrderUseCase:
    def __init__(self, check_stock: CheckStockUseCase, resolver: ResolveProductUseCase | None = None) -> None:
        self._check_stock = check_stock
        self._resolver = resolver

    async def quantity_evidence(self, message: str, extracted=None, inherited: dict | None = None):
        try:
            return concordant_quantity(message, extracted, inherited)
        except InvalidOrderQuantityError:
            if self._resolver is None:
                raise
            # The strict parser remains authoritative. Its only fallback is a
            # complete name verified in the catalogue, never an extracted hint.
            name = inherited.get('product_name') if isinstance(inherited, dict) else None
            if inherited is not None and not name:
                raise
            resolution = await self._resolver.execute(name, None if inherited is not None else message)
            product = resolution.product
            if (resolution.status != ResolutionStatus.RESOLVED or product is None
                    or (name is not None and product.name != name)):
                raise
            return concordant_quantity(message, extracted, inherited, product_name=product.name)

    @traced("stockpilot.order.prepare")
    async def execute(
        self,
        product_name: str | None,
        quantity: int = 1,
        *, message: str | None = None, product_sku: str | None = None,
        product_document_id: str | None = None,
    ) -> PreparedOrder:
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
            record("orders.prepared", outcome="invalid")
            raise InvalidOrderQuantityError("La quantité doit être un entier strictement positif.")

        if not product_name and not (self._resolver and message):
            record("orders.prepared", outcome="invalid")
            return PreparedOrder(
                product_name=None,
                quantity=quantity,
                status=PreparedOrderStatus.PRODUCT_NOT_FOUND,
            )

        if self._resolver:
            resolution = await self._resolver.execute(product_sku or product_name, message)
            if resolution.status == ResolutionStatus.AMBIGUOUS:
                return PreparedOrder(product_name, quantity, PreparedOrderStatus.AMBIGUOUS)
            product = resolution.product
            if product_document_id is not None and (product is None or product.document_id != product_document_id):
                return PreparedOrder(product_name, quantity, PreparedOrderStatus.PRODUCT_IDENTITY_REQUIRED)
            stock = StockResult(product.sku, product.name, product.stock, product.available, product.document_id) if product else None
        else:
            stock = await self._check_stock.execute(product_name)

        if stock is None:
            record("orders.prepared", outcome="invalid")
            return PreparedOrder(
                product_name=product_name,
                quantity=quantity,
                status=PreparedOrderStatus.PRODUCT_NOT_FOUND,
            )

        if stock.stock == 0:
            record("orders.prepared", outcome="insufficient_stock")
            return PreparedOrder(
                product_name=stock.name,
                product_sku=stock.sku,
                product_document_id=stock.document_id,
                quantity=quantity,
                status=PreparedOrderStatus.OUT_OF_STOCK,
                available_stock=stock.stock,
            )

        if quantity > stock.stock:
            record("orders.prepared", outcome="insufficient_stock")
            return PreparedOrder(
                product_name=stock.name,
                product_sku=stock.sku,
                product_document_id=stock.document_id,
                quantity=quantity,
                status=PreparedOrderStatus.INSUFFICIENT_STOCK,
                available_stock=stock.stock,
            )

        record("orders.prepared", outcome="pending_confirmation")
        return PreparedOrder(
            product_name=stock.name,
            product_sku=stock.sku,
            product_document_id=stock.document_id,
            quantity=quantity,
            status=PreparedOrderStatus.PENDING_CONFIRMATION,
            available_stock=stock.stock,
            operation_id=str(uuid4()),
        )
