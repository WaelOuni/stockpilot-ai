"""Real read-only HTTP, migration and forecast checks in guarded disposable DBs."""
from copy import deepcopy
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from time import perf_counter
import pytest
from pymongo.errors import DuplicateKeyError, WriteError

from test_b2b_http_mongo_integration import http as http, db as db, orders as orders, stock as stock
from stockpilot.infrastructure.persistence.mongodb.schema_migrations import MigrationRunner, registry

pytestmark = pytest.mark.integration


def seed(h, org="org-a", days=365, sold=2):
    product = h.db.b2b_products.find_one({"organization_id": org, "sku": "DRILL-001"})
    today = datetime.now(UTC).date()
    rows = [{"organization_id": org, "product_id": str(product["_id"]), "day": (today - timedelta(days=days-i)).isoformat(),
        "sold": sold, "reserved": 50, "cancelled": 10, "returned": 1, "received": 20, "adjusted": 0, "transferred": 0,
        "complete": True, "stockout": False, "dataset": "DEMO_SYNTHETIC", "source_references": [f"DEMO-ledger-{i}"]} for i in range(days)]
    h.db.b2b_demand_days.insert_many(rows)
    h.db.b2b_analytics_profiles.insert_one({"organization_id": org, "product_id": str(product["_id"]), "currency": "EUR", "unit_cost_minor": 3000,
        "supplier": "DEMO supplier", "lead_time_days": 7, "safety_stock": 5, "minimum_order_quantity": 20, "incoming_confirmed": 0,
        "incoming_as_of": today.isoformat(), "source_references": ["DEMO-supply-confirmation"]})
    return product, rows


@pytest.mark.parametrize("actor,expected", [(None, 401), ("a1", 403), ("a3", 403), ("b1", 403), ("a2", 200), ("admin-a", 200), ("admin-b", 200)])
def test_api_roles_and_absent_history(http, actor, expected):
    response = http.browser(actor).get("/b2b/api/intelligence")
    assert response.status_code == expected, response.text
    if expected == 200:
        value = response.json()
        assert value["organization_id"] == ("org-b" if actor == "admin-b" else "org-a")
        assert value["summary"]["products"] == 2 and value["page_series"] == []
        assert all(item["forecast"]["quantity"] is None for item in value["items"])


def test_scoped_exact_metrics_forecasts_and_no_database_writes(http):
    product, _ = seed(http)
    foreign, _ = seed(http, "org-b", sold=900)
    collections = http.db.list_collection_names()
    before = {name: list(http.db[name].find({})) for name in collections if name.startswith("b2b_")}
    c = http.browser("a2")
    result = c.get("/b2b/api/intelligence?limit=1&horizon_days=30").json()
    assert result["summary"]["products"] == 2 and result["has_more"]
    item = result["items"][0]
    assert item["product_id"] == str(product["_id"])
    assert item["period_sales_units"] == 180 and item["forecast"]["quantity"] == 60
    assert item["replenishment"]["suggested_quantity"] == 55
    assert item["stock_cost_minor"] == "30000"
    assert item["promotion_scenarios"][1]["price_minor"] == "4410"
    assert c.get("/b2b/api/intelligence?product_id=" + str(foreign["_id"])).json()["items"] == []
    assert c.get("/b2b/api/intelligence?offset=1&limit=1").json()["items"][0]["sku"] == "SAW-001"
    for name, values in before.items():
        assert list(http.db[name].find({})) == values, name


@pytest.mark.parametrize("query", ["organization_id=org-b", "limit=21", "limit=1&limit=2", "horizon_days=31", "product_id[$ne]=x", "discount_bps=NaN"])
def test_api_rejects_injected_or_unbounded_query(http, query):
    assert http.browser("a2").get("/b2b/api/intelligence?" + query).status_code == 422


def test_identity_headers_origin_csrf_and_mutation_are_denied(http):
    c = http.browser("a2")
    assert c.get("/b2b/api/intelligence", headers={"X-Organization-Id": "org-b"}).status_code == 400
    assert c.get("/b2b/api/intelligence", headers={"Origin": "https://foreign.invalid"}).status_code == 403
    assert c.get("/b2b/api/intelligence", headers={"X-BFF-CSRF": "stale"}).status_code == 403
    assert c.post("/b2b/api/intelligence", json={}).status_code == 405
    http.db.b2b_memberships.update_one({"user_id": "a2"}, {"$set": {"role": "USER"}})
    assert c.get("/b2b/api/intelligence").status_code == 403


def test_new_collections_are_idempotent_and_strict_without_historical_backfill(http):
    product, rows = seed(http)
    before = list(http.db.b2b_products.find({}))
    MigrationRunner(http.db).apply(confirmation=http.db.name)
    MigrationRunner(http.db).require_version(7)
    assert list(http.db.b2b_products.find({})) == before
    with pytest.raises(DuplicateKeyError): http.db.b2b_demand_days.insert_one({**rows[0], "_id": __import__("bson").ObjectId()})
    for fields in ({"sold": -1}, {"sold": True}, {"complete": "yes"}, {"source_references": []}, {"unqualified": 1}):
        invalid = {**rows[0], **fields, "day": "2000-01-01"}
        invalid.pop("_id", None)
        with pytest.raises(WriteError): http.db.b2b_demand_days.insert_one(invalid)
    assert http.db.b2b_demand_days.count_documents({}) == 365


def test_schema_six_remains_usable_with_explicit_analytics_unavailable(db):
    # Existing historical migration snapshots are immutable and still verify.
    from stockpilot.application.ports.tenant_catalogue import TenantCatalogueUnavailable
    from stockpilot.infrastructure.persistence.mongodb.intelligence import MongoIntelligence
    from stockpilot.domain.entities.intelligence import IntelligenceQuery
    from types import SimpleNamespace
    runner = MigrationRunner(db, registry()[:6])
    runner.apply(confirmation=db.name)
    runner.require_version(6)
    assert "b2b_demand_days" not in db.list_collection_names()
    MigrationRunner(db).require_version(1)
    with pytest.raises(TenantCatalogueUnavailable):
        MongoIntelligence(db).snapshot(None, SimpleNamespace(organization_id="org-a"), IntelligenceQuery(), datetime.now(UTC).date())


def test_twenty_products_history_read_latency_sample(http):
    product, rows = seed(http)
    for i in range(19):
        copy = deepcopy(product)
        copy.pop("_id")
        copy["sku"] = f"DEMO-{i:02d}"
        copy["name"] = f"DEMO — Perceuse atelier {i + 1}"
        copy["quantity"] = 2 if i % 5 == 0 else 25 + i * 3
        pid = http.db.b2b_products.insert_one(copy).inserted_id
        history = []
        for day_index, row in enumerate(rows):
            demand = (2, day_index // 30 + 1, 8 if day_index % 10 == 0 else 0,
                      (1, 1, 2, 2, 4, 20, 30)[day_index % 7], 0, 3)[i % 6]
            history.append({**{key: value for key, value in row.items() if key != "_id"}, "product_id": str(pid),
                "sold": demand, "returned": int(demand > 0 and day_index % 31 == 0),
                "reserved": min(demand, 4), "cancelled": int(day_index % 11 == 0),
                "received": 20 if day_index % 7 == 0 else 0, "adjusted": -1 if day_index % 61 == 0 else 0,
                "stockout": i % 6 == 5 and day_index % 50 == 0})
        http.db.b2b_demand_days.insert_many(history)
    durations = []
    for _ in range(3):
        start = perf_counter()
        response = http.browser("a2").get("/b2b/api/intelligence?horizon_days=7")
        durations.append(round((perf_counter() - start) * 1000, 2))
        assert response.status_code == 200 and len(response.json()["items"]) == 20
    Path("tmp").mkdir(exist_ok=True)
    Path("tmp/intelligence-performance.json").write_text(json.dumps({"synthetic_products": 21, "history_days": 365, "page_size": 20, "samples_ms": durations}))
    Path("tmp/intelligence-api-sample.json").write_text(json.dumps(response.json(), indent=2))


def test_extreme_valuation_never_exposes_rounded_decimal128_as_exact(http):
    http.db.b2b_products.update_one({"organization_id": "org-a", "sku": "DRILL-001"}, {"$set": {"price_minor": 2**63 - 1, "quantity": 2**63 - 1}})
    response = http.browser("a2").get("/b2b/api/intelligence")
    assert response.status_code == 200
    data = response.json()
    assert data["summary"]["retail_value_by_currency"] == [{"currency": "EUR", "amount_minor": None}]
    assert data["items"][0]["quantity"] == str(2**63 - 1)


def test_large_stock_coverage_and_signed_margins_remain_valid_api_data(http):
    product, _ = seed(http)
    http.db.b2b_products.update_one({"_id": product["_id"]}, {"$set": {"quantity": 2**63 - 1, "price_minor": 1}})
    http.db.b2b_analytics_profiles.update_one({"product_id": str(product["_id"])}, {"$set": {"unit_cost_minor": 2**63 - 1}})
    response = http.browser("a2").get("/b2b/api/intelligence?horizon_days=7")
    assert response.status_code == 200
    data = response.json()
    item = data["items"][0]
    assert item["available"] == str(2**63 - 1)
    assert item["stock_cost_minor"] == str((2**63 - 1)**2)
    assert item["replenishment"]["coverage_days"] > 2**53
    assert item["replenishment"]["suggested_quantity"] == 0
    assert item["promotion_scenarios"][0]["unit_margin_minor"] == str(1 - (2**63 - 1))
    Path("tmp").mkdir(exist_ok=True)
    Path("tmp/intelligence-large-api-sample.json").write_text(json.dumps(data, indent=2))


def test_mixed_provenance_is_separated_in_the_real_http_contract(http):
    product, rows = seed(http)
    http.db.b2b_demand_days.update_one({"_id": rows[-1]["_id"]}, {"$set": {"dataset": "observed", "sold": 7}})
    response = http.browser("a2").get("/b2b/api/intelligence?horizon_days=7")
    assert response.status_code == 200
    data = response.json()
    item = data["items"][0]
    assert item["product_id"] == str(product["_id"])
    assert item["forecast"]["status"] == "mixed_dataset"
    assert item["replenishment"]["suggested_quantity"] is None
    buckets = {row["dataset"]: row for row in data["page_series"][-1]["datasets"]}
    assert buckets["observed"]["sold"] == 7
    assert buckets["DEMO_SYNTHETIC"]["sold"] is None
    assert buckets["observed"]["reserved"] == 50
    Path("tmp").mkdir(exist_ok=True)
    Path("tmp/intelligence-mixed-api-sample.json").write_text(json.dumps(data, indent=2))


def test_schema_seven_upgrade_preserves_complete_six_snapshot(db):
    from test_tenant_catalogue_mongo_integration import document
    MigrationRunner(db, registry()[:6]).apply(confirmation=db.name)
    db.b2b_products.insert_one(document())
    names = db.list_collection_names()
    before = {name: (list(db[name].find({})), db[name].options(), list(db[name].list_indexes())) for name in names}
    runner = MigrationRunner(db)
    assert [migration.version for migration in runner.plan()] == [7]
    runner.apply(confirmation=db.name)
    for name, (rows, options, indexes) in before.items():
        assert db[name].options() == options and list(db[name].list_indexes()) == indexes
        actual = list(db[name].find({}))
        assert (actual[:6] if name == "stockpilot_schema_migrations" else actual) == rows
    assert set(db.list_collection_names()) - set(names) == {"b2b_demand_days", "b2b_analytics_profiles"}
    history = list(db.stockpilot_schema_migrations.find({}))
    runner.apply(confirmation=db.name)
    assert list(db.stockpilot_schema_migrations.find({})) == history
