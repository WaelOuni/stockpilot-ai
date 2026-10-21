"""Trusted composition of existing B1/B2/B3 ports, on the session identity DB."""

from dataclasses import dataclass

from stockpilot.application.ports.tenant_orders import TenantOrdersPort
from stockpilot.application.ports.tenant_stock_operations import (
    TenantStockOperationsPort,
)
from stockpilot.application.services.tenant_authorization import (
    TenantAuthorizationService,
)
from stockpilot.infrastructure.identity.bff_tenant_context import BffTenantContextSource
from stockpilot.infrastructure.persistence.mongodb.tenant_catalogue import (
    MongoCatalogueOwnership,
    MongoTenantCatalogue,
)
from stockpilot.infrastructure.persistence.mongodb.tenant_orders import (
    MongoBuyerAssociations,
    MongoTenantOrders,
)
from stockpilot.infrastructure.persistence.mongodb.tenant_stock_operations import (
    MongoTenantStockOperations,
)


@dataclass(frozen=True)
class BffBusiness:
    authorization: TenantAuthorizationService
    catalogue: MongoTenantCatalogue
    orders: TenantOrdersPort
    stock: TenantStockOperationsPort

    @classmethod
    def compose(cls, database, sessions):
        authorization = TenantAuthorizationService(
            BffTenantContextSource(sessions),
            MongoCatalogueOwnership(database),
            MongoBuyerAssociations(database),
        )
        return cls(
            authorization,
            MongoTenantCatalogue(database, authorization),
            MongoTenantOrders(database, authorization),
            MongoTenantStockOperations(database, authorization),
        )
