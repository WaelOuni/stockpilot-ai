"""Bounded, tenant-scoped analytics reads. This adapter has no mutation method."""
from datetime import date, timedelta

from bson import ObjectId
from pymongo import timeout
from pymongo.errors import PyMongoError
from pymongo.read_concern import ReadConcern

from stockpilot.application.ports.tenant_catalogue import TenantCatalogueUnavailable
from stockpilot.domain.services.demand_forecast import DemandDay
from stockpilot.infrastructure.persistence.mongodb.schema_migrations import MigrationError, MigrationRunner, _database
from stockpilot.infrastructure.persistence.mongodb.tenant_catalogue import _identifier, _integer, _product, _stock


def _day(row, organization_id, ids):
    if row["organization_id"] != organization_id or row["product_id"] not in ids:
        raise ValueError("Unscoped daily fact.")
    return DemandDay(day=date.fromisoformat(row["day"]),
        **{key: _integer(row[key]) for key in ("sold", "returned", "cancelled", "reserved", "received", "adjusted", "transferred")},
        complete=row["complete"], stockout=row["stockout"],
        source_references=tuple(row["source_references"]), dataset=row["dataset"])


def _profile(row, organization_id, ids):
    if row["organization_id"] != organization_id or row["product_id"] not in ids:
        raise ValueError("Unscoped supply profile.")
    values = {key: row.get(key) for key in ("currency", "supplier", "incoming_as_of", "source_references")}
    if type(values["currency"]) is not str or len(values["currency"]) != 3 or not values["currency"].isascii() or not values["currency"].isupper():
        raise ValueError("Invalid cost currency.")
    if values["supplier"] is not None and (type(values["supplier"]) is not str or not 1 <= len(values["supplier"]) <= 128):
        raise ValueError("Invalid supplier.")
    if values["incoming_as_of"] is not None:
        date.fromisoformat(values["incoming_as_of"])
    refs = values["source_references"]
    if not isinstance(refs, list) or not 1 <= len(refs) <= 20 or any(type(ref) is not str or not 1 <= len(ref) <= 128 for ref in refs):
        raise ValueError("Traceable profile required.")
    for key, maximum in (("unit_cost_minor", 2**63 - 1), ("lead_time_days", 90), ("safety_stock", 10**9), ("minimum_order_quantity", 10**9), ("incoming_confirmed", 10**9)):
        value = row.get(key)
        if value is not None:
            value = _integer(value)
            if not (1 if key == "minimum_order_quantity" else 0) <= value <= maximum:
                raise ValueError("Invalid supply parameter.")
        values[key] = value
    return values


class MongoIntelligence:
    def __init__(self, database):
        self.db = _database(database)

    def snapshot(self, request, context, query, as_of):
        org = _identifier(context.organization_id)
        match = {"organization_id": org, "status": query.status}
        if query.category is not None:
            match["category"] = query.category
        if query.product_id is not None:
            if not ObjectId.is_valid(query.product_id) or str(ObjectId(query.product_id)) != query.product_id:
                raise ValueError("Canonical product identity required.")
            match["_id"] = ObjectId(query.product_id)
        try:
            # The deadline also bounds schema checks and cursor iteration.
            with timeout(8):
                MigrationRunner(self.db).require_version(7)
                with self.db.client.start_session() as session:
                    with session.start_transaction(read_concern=ReadConcern("snapshot"), max_commit_time_ms=1000):
                        return self._read(match, org, query, as_of, session)
        except (PyMongoError, MigrationError, KeyError, TypeError, ValueError, OverflowError):
            raise TenantCatalogueUnavailable("Analytics sources unavailable or invalid.") from None

    def _read(self, match, org, query, as_of, session):
        # One aggregation + three batched reads, independent of page size.
        # Decimal128 has 34 significant digits: never present an extreme rounded
        # aggregate as an exact minor-unit value. Nonnegative sums below 10**30
        # are exact; larger valuations are explicitly unavailable.
        aggregates = list(self.db.b2b_products.aggregate([
            {"$match": match},
            {"$project": {"quantity": 1, "reserved": 1, "currency": 1,
                "available": {"$subtract": ["$quantity", "$reserved"]} if query.status == "active" else {"$literal": 0},
                "retail": {"$multiply": [{"$toDecimal": "$price_minor"}, {"$toDecimal": "$quantity"}]}}},
            {"$group": {"_id": "$currency", "products": {"$sum": 1},
                "quantity": {"$sum": {"$toDecimal": "$quantity"}},
                "reserved": {"$sum": {"$toDecimal": "$reserved"}},
                "available": {"$sum": {"$toDecimal": "$available"}},
                "out_of_stock": {"$sum": {"$cond": [{"$eq": ["$available", 0]}, 1, 0]}},
                "low_stock": {"$sum": {"$cond": [{"$and": [{"$gt": ["$available", 0]}, {"$lte": ["$available", query.low_stock_threshold]}]}, 1, 0]}},
                "retail_value_minor": {"$sum": "$retail"}}}, {"$sort": {"_id": 1}},
        ], session=session, maxTimeMS=3000))
        if sum(row["products"] for row in aggregates) > 10**9 or any(row[field].to_decimal() > 10**30 for row in aggregates for field in ("quantity", "reserved", "available")):
            raise ValueError("Aggregate exactness bound exceeded.")
        summary = {"products": sum(row["products"] for row in aggregates),
            "quantity": str(sum(int(row["quantity"].to_decimal()) for row in aggregates)),
            "reserved": str(sum(int(row["reserved"].to_decimal()) for row in aggregates)),
            "available": str(sum(int(row["available"].to_decimal()) for row in aggregates)),
            "out_of_stock": sum(row["out_of_stock"] for row in aggregates) if query.status == "active" else 0,
            "low_stock": sum(row["low_stock"] for row in aggregates) if query.status == "active" else 0,
            "retail_value_by_currency": [{"currency": row["_id"], "amount_minor": str(int(row["retail_value_minor"].to_decimal())) if row["retail_value_minor"].to_decimal() <= 10**30 else None} for row in aggregates],
            "valuation_basis": "current_list_price_not_purchase_cost",
            "source": "b2b_products", "scope": "all_filtered_products", "status": query.status}
        products = []
        for row in self.db.b2b_products.find(match, session=session).sort([("sku", 1), ("_id", 1)]).skip(query.offset).limit(query.limit).max_time_ms(3000):
            product, stock = _product(row, org), _stock(row, org)
            products.append({"product_id": product.product_id, "sku": product.sku,
                "name": product.name, "category": product.category, "status": product.status,
                "price_minor": str(product.price_minor), "currency": product.currency,
                "quantity": stock.quantity, "reserved": stock.reserved, "available": stock.available})
        ids = [product["product_id"] for product in products]
        history = {pid: [] for pid in ids}
        if ids:
            # Include explicit zero-demand days; absence is never filled with zeros.
            for row in self.db.b2b_demand_days.find({"organization_id": org, "product_id": {"$in": ids},
                "day": {"$gte": (as_of - timedelta(days=365)).isoformat(), "$lt": as_of.isoformat()}}, session=session).sort([("product_id", 1), ("day", 1)]).limit(query.limit * 365 + 1).max_time_ms(3000):
                history[row["product_id"]].append(_day(row, org, ids))
            if sum(map(len, history.values())) > query.limit * 365:
                raise ValueError("Unbounded history.")
        profiles = {}
        if ids:
            for row in self.db.b2b_analytics_profiles.find({"organization_id": org, "product_id": {"$in": ids}}, session=session).limit(query.limit + 1).max_time_ms(3000):
                pid = row["product_id"]
                if pid in profiles:
                    raise ValueError("Ambiguous profile.")
                profiles[pid] = _profile(row, org, ids)
        return {"summary": summary, "products": products, "total": summary["products"],
            "days": {pid: tuple(days) for pid, days in history.items()}, "profiles": profiles}
