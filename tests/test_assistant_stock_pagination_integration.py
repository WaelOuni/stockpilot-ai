"""Real isolated Mongo + PostgreSQL fixtures; no production or model network."""
from copy import deepcopy
import pytest
from test_tenant_assistant_integration import assistant, environment, db, orders, stock, send, http
from test_tenant_orders_mongo_integration import REQUESTS, context
from stockpilot.application.ports.tenant_knowledge import TenantDataNotFound
from stockpilot.application.services.identity_authorization import AuthorizationDenied

pytestmark = pytest.mark.integration


def page(a, turn, actor="a1", **options):
    service, conv, env = a
    return service.catalogue_results(REQUESTS[actor], context(env.orders, actor), conv, turn["turn_id"], **options)


def products(db, count=126):
    template = db.b2b_products.find_one({"organization_id": "org-a", "sku": "DRILL-001"})
    template.pop("_id")
    for i in range(count):
        db.b2b_products.insert_one({**template, "sku": f"PAGE-{i:03}", "quantity": 12, "reserved": 7})
    db.b2b_products.insert_one({**template, "sku": "INACTIVE", "quantity": 5, "reserved": 0, "status": "inactive"})


def test_over_100_results_are_scoped_stable_and_historical_cards_immutable(assistant, db):
    products(db)
    service, conv, env = assistant
    before = {n: list(db[n].find({}).sort("_id", 1)) for n in db.list_collection_names()}
    turn = send(assistant, "Quels produits ont que 5 unités disponibles ?")
    original = deepcopy(turn)
    assert len(turn["reply"]["products"]) == 5 and turn["reply"]["total"] == 126
    assert turn["reply"]["products"][0]["available"] == 5
    service.llm.classify_intent.assert_not_awaited()
    ids = []
    for offset in range(0, 140, 20):
        current = page(assistant, turn, offset=offset)
        assert current["total"] == 126
        assert current["has_more"] == (offset + 20 < 126)
        assert all(c["organization_id"] == "org-a" for c in current["items"])
        ids.extend(c["sku"] for c in current["items"])
    assert ids == [f"PAGE-{i:03}" for i in range(126)]
    assert {n: list(db[n].find({}).sort("_id", 1)) for n in db.list_collection_names()} == before
    db.b2b_products.update_one({"organization_id": "org-a", "sku": "PAGE-000"}, {"$set": {"quantity": 20}})
    current = page(assistant, turn)
    assert current["total"] == 125
    assert current["items"][0]["sku"] == "PAGE-001"
    historical = service.store.turn(REQUESTS["a1"], context(env.orders), conv, turn["turn_id"])
    assert historical["reply"] == original["reply"]
    assert historical["reply"]["products"][0]["quantity"] == 12
    assert db.b2b_orders.count_documents({}) == 0
    assert db.b2b_order_operations.count_documents({}) == 0


@pytest.mark.parametrize("phrase,expected", [
    ("produits avec exactement 5 unités disponibles", [5]),
    ("produits avec moins de 15 unités disponibles", [0, 1, 5, 14]),
    ("produits avec 15 unités disponibles ou moins", [0, 1, 5, 14, 15]),
    ("produits avec au moins 5 unités disponibles", [5, 14, 15]),
    ("produits avec plus de 5 unités disponibles", [14, 15]),
    ("produits avec entre 1 et 14 unités disponibles", [1, 5, 14]),
    ("produits en rupture", [0]),
])
def test_stock_boundaries_use_physical_minus_reserved(assistant, db, phrase, expected):
    db.b2b_products.delete_many({"organization_id": "org-a"})
    from test_tenant_catalogue_mongo_integration import document
    for value in (0, 1, 5, 14, 15):
        row = document(sku=f"BOUND-{value:02}", quantity=value + 8)
        row["reserved"] = 8
        db.b2b_products.insert_one(row)
    turn = send(assistant, phrase)
    assert [c["available"] for c in turn["reply"]["products"]] == expected
    assert [c["available"] for c in page(assistant, turn)["items"]] == expected


def test_custom_stock_filters_keep_original_budget_category_and_ownership(assistant, db):
    products(db, 5)
    turn = send(assistant, "Des perceuses disponibles à moins de 50 euros")
    data = page(assistant, turn, custom_stock_range=True, minimum_available=5, maximum_available=5,
                exclude_sold_out=True, sort="sku")
    assert len(data["items"]) == 5
    assert all(c["price_minor"] < 5000 and c["available"] == 5 for c in data["items"])
    assert page(assistant, turn, custom_stock_range=True, minimum_available=50)["total"] == 0
    with pytest.raises((AuthorizationDenied, TenantDataNotFound)):
        page(assistant, turn, actor="a2")
    with pytest.raises((AuthorizationDenied, TenantDataNotFound)):
        page(assistant, turn, actor="b1")
    with pytest.raises(ValueError):
        page(assistant, turn, minimum_available=5)
    with pytest.raises(ValueError):
        page(assistant, turn, custom_stock_range=True, minimum_available=6, maximum_available=5)
    # The fixture's persisted identifiers are resolved from its authorization context.
    user = context(assistant[2].orders).principal.user_id
    db.b2b_memberships.update_one({"user_id": user}, {"$set": {"status": "suspended"}})
    with pytest.raises(AuthorizationDenied):
        page(assistant, turn)


def test_http_current_results_validate_scope_and_query(http, environment):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from stockpilot.application.services.tenant_assistant import TenantAssistant
    from stockpilot.infrastructure.persistence.postgres.tenant_assistant import PgTenantAssistant
    from stockpilot.presentation.api.routes.b2b_assistant import router

    h, e = http, environment
    store = PgTenantAssistant(e.uri, h.bundle.authorization, e.schema)
    llm = SimpleNamespace(classify_intent=AsyncMock(side_effect=AssertionError("Deterministic stock path")))
    h.app.state.bff_assistant = TenantAssistant(store, h.bundle.catalogue, h.bundle.orders, None, llm)
    h.app.include_router(router)
    c = h.browser()
    opened = c.post("/b2b/api/assistant/conversations", json={"thread_id": "pagination"})
    assert opened.status_code == 200
    base = "/b2b/api/assistant/conversations/" + opened.json()["conversation_id"]
    turn = c.post(base + "/messages", json={"message_id": "stock", "message": "produits avec au moins 0 unités disponibles"})
    assert turn.status_code == 200, turn.text
    assert turn.json()["reply"]["can_browse_products"] is True
    assert "catalogue_search" not in turn.text
    url = base + "/turns/stock/products"
    response = c.get(url)
    assert response.status_code == 200, response.text
    assert response.json()["items"][0]["organization_id"] == "org-a"
    assert isinstance(response.json()["items"][0]["price_minor"], str)
    for actor in ("b1", "a3"):
        assert h.browser(actor).get(url).status_code == 404
    assert h.browser(None).get(url).status_code == 401
    for query in ("limit=101", "offset=10001", "sort=unknown", "organization_id=org-b", "minimum_available=-1"):
        assert c.get(url + "?" + query).status_code in (400, 422)
    assert c.get(url, headers={"Origin": "https://evil.invalid"}).status_code == 403
    assert c.get(base + "/turns/missing/products").status_code == 404
    assert h.db.b2b_orders.count_documents({}) == 0
