"""Persisted B2B discovery, disposable Mongo only; model never authorizes writes."""

import asyncio
from unittest.mock import Mock
from types import SimpleNamespace
import pytest
from test_tenant_catalogue_mongo_integration import REQUESTS, document
from test_tenant_catalogue_mongo_integration import catalogue as catalogue
from test_tenant_catalogue_mongo_integration import db as db
from stockpilot.application.services.tenant_assistant import TenantAssistant

pytestmark = pytest.mark.integration


@pytest.fixture
def discovery(catalogue, db):
    repo, contexts, _, _ = catalogue
    db.b2b_products.delete_many({})  # UUID test database owned by fixture.
    for org in ("org-a", "org-b"):
        for category in ("perceuses", "visseuses", "ponceuses", "scies"):
            for i, price in enumerate((15000, 15500, 16000, 16100)):
                row = document(org, f"BDEMO-{category}-{i}", price, 20)
                row.update(category=category, name=f"{category} Norex {i}")
                db.b2b_products.insert_one(row)
    return repo, contexts


def answer(discovery, question, state=None, org="org-a"):
    repo, contexts = discovery
    writes = Mock()
    service = TenantAssistant(Mock(), repo, writes, Mock(), Mock())
    result = asyncio.run(
        service._answer(
            REQUESTS[org],
            contexts[org],
            "test",
            {
                "question": question,
                "turn_id": "test",
                "reply": {"classification": {"intent": "ORDER", "quantity": 99}},
                **(state or {}),
            },
        )
    )
    assert writes.mock_calls == []
    assert not result["can_confirm"] and result["order"] is None
    return result


@pytest.mark.parametrize(
    "question,expected",
    [
        ("je cherche 2 perceuses disponibles moins de 150 euros", []),
        ("je cherche 2 perceuses disponibles moins de 300 euros", [15000, 15500]),
        (
            "je cherche une perceuse disponible moins de 250 euros",
            [15000, 15500, 16000],
        ),
        ("je cherche une Perceuse disponible moins de 160 euros", [15000, 15500]),
        ("je cherche 2 perceuses disponibles maximum 150 euros", [15000]),
    ],
)
def test_reported_prompts(discovery, db, question, expected):
    before = list(db.b2b_products.find({}).sort("_id", 1))
    result = answer(discovery, question)
    assert result["intent"] == "PRODUCT_ADVICE"
    if not expected:
        assert "plafond de prix exclut" in result["text"]
    assert [p["price_minor"] for p in result["products"]] == expected
    assert all(
        p["organization_id"] == "org-a" and p["available"] > 0
        for p in result["products"]
    )
    assert list(db.b2b_products.find({}).sort("_id", 1)) == before


@pytest.mark.parametrize("category", ["perceuses", "visseuses", "ponceuses", "scies"])
def test_persisted_category(discovery, category):
    result = answer(discovery, f"je cherche 2 {category} disponibles maximum 160 euros")
    assert len(result["products"]) == 2
    assert all(category in p["sku"] for p in result["products"])


@pytest.mark.parametrize(
    "question,count",
    [
        ("quels sont les produits qui vont s'épuiser, stock moins de 5", 2),
        ("montre-moi les produits en rupture", 1),
        ("quelles perceuses ont 5 ou moins en stock", 3),
        ("produits avec 5 ou moins disponibles", 3),
        ("Quels produits ont moins de 5 unités disponibles ?", 2),
        ("Quels produits ont moins de 5 disponibles ?", 2),
        ("Montre-moi les stocks inférieurs à 5 unités.", 2),
        ("Quels articles sont presque épuisés ?", 2),
        ("Liste les produits avec 5 unités disponibles ou moins.", 3),
        ("Montre les produits en rupture de stock.", 1),
        ("Quels produits ont moins de 0 unités disponibles ?", 0),
    ],
)
def test_low_stock_reads(discovery, db, question, count):
    for i, available in enumerate((0, 4, 5, 8)):
        db.b2b_products.update_one(
            {"organization_id": "org-a", "sku": f"BDEMO-perceuses-{i}"},
            {"$set": {"quantity": available + 4, "reserved": 4}},
        )
    before = list(db.b2b_products.find({}).sort("_id", 1))
    result = answer(discovery, question)
    assert result["intent"] == "LOW_STOCK"
    assert len(result["products"]) == count
    assert result["total"] == count
    assert all(
        p["available"] == p["quantity"] - p["reserved"] for p in result["products"]
    )
    assert list(db.b2b_products.find({}).sort("_id", 1)) == before


def test_discovery_categories_sku_prefix_and_revocation(discovery, db):
    from stockpilot.application.services.identity_authorization import (
        AuthorizationDenied,
    )

    repo, contexts = discovery
    assert set(repo.categories(REQUESTS["org-a"], contexts["org-a"])) == {
        "perceuses",
        "visseuses",
        "ponceuses",
        "scies",
    }
    values = repo.suggest_skus(REQUESTS["org-a"], contexts["org-a"], "BDEMO", 10)
    assert len(values) == 10 and all(p.organization_id == "org-a" for p in values)
    assert repo.suggest_skus(REQUESTS["org-a"], contexts["org-a"], ".*") == ()
    for prefix in ("", "B", "x" * 129, {"$ne": None}):
        with pytest.raises(ValueError):
            repo.suggest_skus(REQUESTS["org-a"], contexts["org-a"], prefix)
    with pytest.raises(AuthorizationDenied):
        repo.categories(REQUESTS["org-b"], contexts["org-a"])
    db.b2b_memberships.update_one(
        {"membership_id": "m-org-a"}, {"$set": {"status": "suspended"}}
    )
    with pytest.raises(AuthorizationDenied):
        repo.suggest_skus(REQUESTS["org-a"], contexts["org-a"], "BDEMO")


def test_custom_category_and_unknown_product(discovery, db):
    row = document(sku="LIGHT-01", price=1234)
    row.update(category="Éclairage", name="Lampe atelier")
    db.b2b_products.insert_one(row)
    result = answer(discovery, "je cherche Éclairage disponible maximum 50 euros")
    assert [p["sku"] for p in result["products"]] == ["LIGHT-01"]
    assert result["catalogue_categories"] == ["Éclairage"]
    followup = answer(
        discovery,
        "Et si je monte le budget à 100 euros ?",
        {k: result[k] for k in ("criteria", "catalogue_categories", "advice_skus")},
    )
    assert [p["sku"] for p in followup["products"]] == ["LIGHT-01"]
    for phrase in (
        "je cherche une Perceuse Inconnue 99V",
        "je cherche SKU FOREIGN-404",
        "quels robots ont moins de 5 en stock",
    ):
        reply = answer(discovery, phrase)
        assert not reply["products"] and reply["intent"] == "CLARIFICATION_REQUIRED"


@pytest.mark.parametrize(
    "phrase",
    [
        "produits avec moins de 5 en stock",
        "quels produits vont s'épuiser",
        "produits en stock faible",
        "quels produits ont un stock inférieur à 5",
        "liste des produits presque épuisés",
        "quelles perceuses ont moins de 5 disponibles",
        "Quels produits ont moins de 5 unités disponibles ?",
        "Quels articles sont presque épuisés ?",
        "Liste les produits avec 5 unités disponibles ou moins.",
    ],
)
def test_low_stock_bounded_sorted_and_active(discovery, db, phrase):
    for i in range(8):
        row = document(sku=f"LOW-{i:02}", quantity=12, price=90000)
        row.update(
            category="perceuses", reserved=10, status="inactive" if i == 7 else "active"
        )
        db.b2b_products.insert_one(row)
    reply = answer(discovery, phrase)
    assert reply["intent"] == "LOW_STOCK" and reply["total"] == 7
    assert [p["sku"] for p in reply["products"]] == [f"LOW-{i:02}" for i in range(5)]
    assert "partiel" in reply["text"] and "sans réservation" in reply["text"]


def test_stock_permission_is_revalidated(discovery):
    from unittest.mock import patch
    from stockpilot.domain.enums.tenant_authorization import (
        TenantOperation as O,
        AuthorizationDecision as D,
    )
    from stockpilot.application.services.identity_authorization import (
        AuthorizationDenied,
    )

    repo, _ = discovery
    decide = repo.authorization.decide_scope

    def guarded(request, context, operation):
        return (
            D.DENY if operation == O.STOCK_READ else decide(request, context, operation)
        )

    with patch.object(repo, "authorization", SimpleNamespace(decide_scope=guarded)):
        with pytest.raises(AuthorizationDenied):
            answer(discovery, "Quels produits ont moins de 5 unités disponibles ?")


@pytest.mark.parametrize("org", ["org-a", "org-b"])
def test_quantity_requests_use_only_active_tenant_and_do_not_write(discovery, db, org):
    for tenant, quantity in (("org-a", 8), ("org-b", 4)):
        db.b2b_products.update_one(
            {"organization_id": tenant, "sku": "BDEMO-perceuses-0"},
            {"$set": {"quantity": quantity, "reserved": 4}},
        )
    before = {name: list(db[name].find({}).sort("_id", 1)) for name in db.list_collection_names()}
    result = answer(discovery, "Quels produits ont moins de 5 unités disponibles ?", org=org)
    assert result["intent"] == "LOW_STOCK" and result["total"] == 1
    assert result["products"][0]["organization_id"] == org
    assert result["products"][0]["available"] == (4 if org == "org-a" else 0)
    assert {name: list(db[name].find({}).sort("_id", 1)) for name in db.list_collection_names()} == before


def test_exact_sku_and_explicit_order_keep_their_routes(discovery):
    reply = answer(discovery, "Je cherche SKU BDEMO-perceuses-0")
    assert reply["intent"] == "PRODUCT_ADVICE"
    assert [p["sku"] for p in reply["products"]] == ["BDEMO-perceuses-0"]
    reply = answer(
        discovery,
        "Je veux commander les produits avec moins de 5 unités disponibles",
        {"read_only": True},
    )
    assert reply["intent"] == "ORDER_REFUSED"
