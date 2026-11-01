"""Scoped B2B reads. Never composes or imports a historical repository."""

import re

from bson import ObjectId
from bson.int64 import Int64
from pymongo.collation import Collation
from pymongo.errors import PyMongoError

from stockpilot.application.ports.tenant_authorization import (
    ResourceOwnershipUnavailable,
)
from stockpilot.application.ports.tenant_catalogue import TenantCatalogueUnavailable
from stockpilot.application.services.identity_authorization import AuthorizationDenied
from stockpilot.domain.entities.tenant_authorization import (
    OrganizationResource,
    ResourceReference,
)
from stockpilot.domain.entities.tenant_product import (
    TenantProduct,
    TenantProductPage,
    TenantProductQuery,
    TenantStock,
    TenantProductStock,
    TenantStockPage,
)
from stockpilot.domain.enums.tenant_authorization import AuthorizationDecision as D
from stockpilot.domain.enums.tenant_authorization import ResourceKind as K
from stockpilot.domain.enums.tenant_authorization import TenantOperation as O
from stockpilot.domain.identity_validation import require_identifier
from stockpilot.infrastructure.persistence.mongodb.schema_migrations import (
    MigrationError,
    MigrationRunner,
    _database,
)

COLLECTION = "b2b_products"


def _identifier(value):
    require_identifier(value)
    if len(value) > 128:
        raise ValueError("Identifier too long.")
    return value


def _integer(value):
    if type(value) not in (int, Int64):
        raise ValueError("Persisted integer required.")
    return int(value)


def _product(row, organization_id):
    if (
        row["organization_id"] != organization_id
        or not isinstance(row["_id"], ObjectId)
        or not isinstance(row["attributes"], list)
    ):
        raise ValueError("Inconsistent B2B ownership or facts.")
    product = TenantProduct(
        str(row["_id"]),
        organization_id,
        row["sku"],
        row["name"],
        row["description"],
        row["category"],
        _integer(row["price_minor"]),
        row["currency"],
        row["status"],
        tuple(row["attributes"]),
        _integer(row["revision"]),
    )
    _stock(row, organization_id)
    return product


def _stock(row, organization_id):
    if row["organization_id"] != organization_id or not isinstance(
        row["_id"], ObjectId
    ):
        raise ValueError("Inconsistent B2B ownership.")
    return TenantStock(
        str(row["_id"]),
        organization_id,
        _integer(row["quantity"]),
        _integer(row["reserved"]),
        row["status"],
        _integer(row["revision"]),
    )


def _exact(collection, organization_id, **identity):
    # Scalar identities only, built by trusted code. Limit two detects ambiguity.
    rows = list(
        collection.find(
            {"organization_id": _identifier(organization_id), **identity},
            collation=Collation("simple"),
        ).limit(2)
    )
    if len(rows) > 1:
        raise ValueError("Ambiguous B2B identity.")
    return rows[0] if rows else None


class MongoCatalogueOwnership:
    """V13.7.3-A ownership port, scoped even for foreign native IDs."""

    def __init__(self, database):
        self.db = _database(database)

    def find(self, organization_id, reference):
        try:
            _identifier(organization_id)
            if not isinstance(reference, ResourceReference) or reference.kind not in (
                K.CATALOGUE_ITEM,
                K.STOCK_ITEM,
            ):
                return ()
            if not ObjectId.is_valid(reference.resource_id):
                return ()
            MigrationRunner(self.db).require_version(1)
            row = _exact(
                self.db[COLLECTION],
                organization_id,
                _id=ObjectId(reference.resource_id),
            )
            if row is None:
                return ()
            # Reject corrupted facts before claiming authoritative ownership.
            _product(row, organization_id)
            return (
                OrganizationResource(reference, organization_id, str(row["revision"])),
            )
        except (PyMongoError, MigrationError, KeyError, ValueError, TypeError):
            raise ResourceOwnershipUnavailable("B2B ownership unavailable.") from None


class MongoTenantCatalogue:
    """Implements TenantCataloguePort and TenantStockPort; read-only in B1.

    Trusted composition injects TenantAuthorizationService with fresh context
    resolution and MongoCatalogueOwnership. No caller-supplied org argument.
    """

    def __init__(self, database, authorization):
        self.db = _database(database)
        self.authorization = authorization
        self.collection = self.db[COLLECTION]

    def _scope(self, request, context, operation):
        if self.authorization is None:
            raise TenantCatalogueUnavailable("B2B authorization unavailable.")
        decision = self.authorization.decide_scope(request, context, operation)
        if decision is D.INDETERMINATE:
            raise TenantCatalogueUnavailable("B2B authorization unavailable.")
        if decision is not D.ALLOW:
            raise AuthorizationDenied("Current authorized tenant context required.")
        try:
            MigrationRunner(self.db).require_version(1)
            return _identifier(context.organization_id)
        except (MigrationError, PyMongoError, ValueError):
            raise TenantCatalogueUnavailable("B2B schema unavailable.") from None

    def export_rows(self, request, context, mode, category=None):
        from stockpilot.domain.entities.tenant_stock_operation import ImportMode

        if not isinstance(mode, ImportMode):
            raise ValueError("Explicit export mode required.")
        org = self._scope(request, context, O.CATALOGUE_READ)
        if mode is not ImportMode.METADATA_ONLY:
            self._scope(request, context, O.STOCK_READ)
        filters = {"organization_id": org, "status": "active"}
        if category is not None:
            if type(category) is not str or not 1 <= len(category) <= 128:
                raise ValueError("Bounded category required.")
            filters["category"] = category
        try:
            rows = list(self.collection.find(filters).sort("sku", 1).limit(1001).max_time_ms(2000))
            if len(rows) > 1000:
                raise ValueError("Export limited to 1000 products; select a category.")
            headers = ["sku", "quantity"] if mode is ImportMode.STOCK_ONLY else [
                "sku", "name", "description", "category", "price_minor", "currency"
            ] + (["quantity"] if mode is ImportMode.CREATE_ONLY else [])
            exported = []
            size = 0
            for row in rows:
                _product(row, org)
                if mode is not ImportMode.METADATA_ONLY:
                    _stock(row, org)
                values = [str(row[field]) for field in headers]
                size += sum(len(value.encode("utf-8")) for value in values)
                if size > 4 * 1024 * 1024:
                    raise ValueError("Export size limit exceeded; select a category.")
                exported.append(values)
            self._scope(request, context, O.CATALOGUE_READ)
            if mode is not ImportMode.METADATA_ONLY:
                self._scope(request, context, O.STOCK_READ)
            return {"headers": headers, "rows": exported}
        except (PyMongoError, KeyError, TypeError):
            raise TenantCatalogueUnavailable("Scoped catalogue export unavailable.") from None

    def categories(self, request, context):
        org = self._scope(request, context, O.CATALOGUE_READ)
        try:
            rows = list(self.collection.aggregate([
                {"$match": {"organization_id": org, "status": "active"}},
                {"$group": {"_id": "$category"}},
                {"$sort": {"_id": 1}}, {"$limit": 1001},
            ], collation=Collation("simple"), maxTimeMS=2000))
            if len(rows) > 1000:
                raise ValueError("Category discovery bound exceeded.")
            values = tuple(_identifier(row["_id"]) for row in rows)
            self._scope(request, context, O.CATALOGUE_READ)
            return values
        except (PyMongoError, KeyError, TypeError, ValueError):
            raise TenantCatalogueUnavailable("Scoped categories unavailable.") from None

    def suggest_skus(self, request, context, prefix, limit=10):
        org = self._scope(request, context, O.CATALOGUE_READ)
        if type(prefix) is not str or not 2 <= len(prefix) <= 128 or type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError("Bounded SKU prefix required.")
        try:
            # Literal case-sensitive anchored prefix: existing org/status/SKU index.
            rows = self.collection.find({
                "organization_id": org, "status": "active",
                "sku": {"$regex": "^" + re.escape(prefix)},
            }, collation=Collation("simple")).hint("b2b_org_status_sku").sort("sku", 1).limit(limit).max_time_ms(2000)
            result = tuple(_product(row, org) for row in rows)
            self._scope(request, context, O.CATALOGUE_READ)
            return result
        except (PyMongoError, KeyError, TypeError, ValueError):
            raise TenantCatalogueUnavailable("Scoped SKU suggestions unavailable.") from None

    def resolution_candidates(
        self, request, context, reference, *, exact=False, limit=21
    ):
        """Literal bounded name evidence; org filter is in Mongo before retrieval."""
        org = self._scope(request, context, O.CATALOGUE_READ)
        if (
            type(reference) is not str
            or not reference
            or len(reference) > 256
            or type(limit) is not int
            or not 1 <= limit <= 21
        ):
            raise ValueError("Bounded literal reference required.")
        from stockpilot.application.use_cases.resolve_product import normalize_name

        normalized = normalize_name(reference)
        filters = {"organization_id": org, "status": "active"}
        if exact:
            filters["name"] = reference
            collation = Collation("fr", strength=1)
        else:
            accents = {
                "a": "[aàâäá]",
                "e": "[eéèêë]",
                "i": "[iîïí]",
                "o": "[oôöó]",
                "u": "[uùûüú]",
                "c": "[cç]",
            }
            terms = re.findall(r"\w+", normalized)
            if not terms or len(terms) > 32:
                return ()
            filters["$and"] = [
                {
                    "name": {
                        "$regex": r"(?<![\p{L}\p{N}_])"
                        + "".join(accents.get(ch, re.escape(ch)) for ch in term)
                        + r"(?![\p{L}\p{N}_])",
                        "$options": "i",
                    }
                }
                for term in terms
            ]
            collation = Collation("simple")
        try:
            rows = (
                self.collection.find(filters, collation=collation)
                .sort("sku", 1)
                .limit(limit)
                .max_time_ms(2000)
            )
            return tuple(_product(row, org) for row in rows)
        except (PyMongoError, KeyError, TypeError, ValueError):
            raise TenantCatalogueUnavailable(
                "Scoped product resolution unavailable."
            ) from None

    def _resource(self, request, context, operation, kind, product_id):
        decision = self.authorization.decide(
            request, context, operation, ResourceReference(kind, product_id)
        )
        if decision is D.INDETERMINATE:
            raise TenantCatalogueUnavailable("B2B resource authorization unavailable.")
        if decision is D.DENY:
            raise AuthorizationDenied("Resource access denied.")
        return decision is D.ALLOW

    def search(self, request, context, query):
        return self._search(request, context, query, with_stock=False)

    def search_stock(self, request, context, query):
        return self._search(request, context, query, with_stock=True)

    def _search(self, request, context, query, *, with_stock):
        org = self._scope(request, context, O.CATALOGUE_READ)
        if not isinstance(query, TenantProductQuery):
            raise TypeError("Typed catalogue query required.")
        stock_read = with_stock or query.available is not None or query.maximum_available is not None or query.sort_by_available
        if stock_read:
            self._scope(request, context, O.STOCK_READ)
        filters = {"organization_id": org}
        if query.categories:
            filters["category"] = {"$in": list(query.categories)}
        if query.exclude_skus:
            filters["sku"] = {"$nin": list(query.exclude_skus)}
        for key in ("category", "currency", "status"):
            if getattr(query, key) is not None:
                filters[key] = getattr(query, key)
        if query.attribute is not None:
            filters["attributes"] = query.attribute
        if query.available is not None:
            availability = {
                "$and": [
                    {"$eq": ["$status", "active"]},
                    {"$gt": [{"$subtract": ["$quantity", "$reserved"]}, 0]},
                ]
            }
            filters["$expr"] = (
                availability if query.available else {"$not": [availability]}
            )
        if query.maximum_available is not None:
            threshold = {"$lte": [{"$subtract": ["$quantity", "$reserved"]}, query.maximum_available]}
            filters["$expr"] = {"$and": [filters["$expr"], threshold]} if "$expr" in filters else threshold
        prices = {}
        if query.minimum_price_minor is not None:
            prices["$gte"] = query.minimum_price_minor
        if query.maximum_price_minor is not None:
            prices["$lte"] = query.maximum_price_minor
        if prices:
            filters["price_minor"] = prices
        try:
            # A single scoped aggregate gives coherent page/count without a
            # read-then-count race. No global read followed by Python filtering.
            pipeline = [
                {"$match": filters},
                *([{"$set": {"_available": {"$subtract": ["$quantity", "$reserved"]}}}] if query.sort_by_available else []),
                {
                    "$sort": {"_available": 1, "sku": 1} if query.sort_by_available else {"price_minor": 1, "sku": 1}
                    if query.sort_by_price
                    else {"sku": 1}
                },
                {
                    "$facet": {
                        "items": [{"$skip": query.offset}, {"$limit": query.limit}],
                        "total": [{"$count": "value"}],
                    }
                },
            ]
            result = next(
                self.collection.aggregate(
                    pipeline, collation=Collation("simple"), maxTimeMS=5000
                )
            )
            items = tuple(_product(row, org) for row in result["items"])
            if len({p.sku for p in items}) != len(items):
                raise ValueError("Duplicate B2B SKU.")
            total = result["total"][0]["value"] if result["total"] else 0
            self._scope(request, context, O.CATALOGUE_READ)
            if stock_read:
                self._scope(request, context, O.STOCK_READ)
            more = query.offset + len(items) < total
            if with_stock:
                pairs = tuple(TenantProductStock(product, _stock(row, org)) for product, row in zip(items, result["items"], strict=True))
                return TenantStockPage(pairs, total, more)
            return TenantProductPage(items, total, more)
        except (PyMongoError, KeyError, ValueError, TypeError, StopIteration):
            raise TenantCatalogueUnavailable("B2B catalogue read failed.") from None

    def by_sku(self, request, context, sku):
        org = self._scope(request, context, O.CATALOGUE_READ)
        _identifier(sku)
        try:
            row = _exact(self.collection, org, sku=sku)
            if row is None:
                return None
            product = _product(row, org)
            if not self._resource(
                request, context, O.CATALOGUE_READ, K.CATALOGUE_ITEM, product.product_id
            ):
                return None
            return product
        except (PyMongoError, KeyError, ValueError, TypeError):
            raise TenantCatalogueUnavailable("B2B catalogue read failed.") from None

    def _by_id(self, request, context, product_id, operation, kind):
        org = self._scope(request, context, operation)
        _identifier(product_id)
        if not ObjectId.is_valid(product_id):
            return None
        if not self._resource(request, context, operation, kind, product_id):
            return None
        try:
            row = _exact(self.collection, org, _id=ObjectId(product_id))
            if row is None:
                return None
            return _stock(row, org) if kind is K.STOCK_ITEM else _product(row, org)
        except (PyMongoError, KeyError, ValueError, TypeError):
            raise TenantCatalogueUnavailable("B2B resource read failed.") from None

    def by_id(self, request, context, product_id):
        return self._by_id(
            request, context, product_id, O.CATALOGUE_READ, K.CATALOGUE_ITEM
        )

    def stock(self, request, context, product_id):
        return self._by_id(request, context, product_id, O.STOCK_READ, K.STOCK_ITEM)

    def available(self, request, context, product_id, quantity):
        if type(quantity) is not int or quantity <= 0:
            raise ValueError("Positive integer quantity required.")
        stock = self.stock(request, context, product_id)
        return stock is not None and stock.available >= quantity
