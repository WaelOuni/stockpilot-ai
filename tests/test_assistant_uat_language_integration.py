"""Six actual UAT formulations against guarded tenant Mongo data."""
import pytest
from test_b2b_catalogue_discovery_integration import discovery as discovery, catalogue as catalogue, db as db, answer

pytestmark = pytest.mark.integration


def facts(db):
    for index, (available, price) in enumerate(((0, 9999), (4, 10000), (5, 2000), (8, 4900))):
        db.b2b_products.update_one({"organization_id": "org-a", "sku": f"BDEMO-perceuses-{index}"}, {"$set": {"quantity": available + 3, "reserved": 3, "price_minor": price}})


@pytest.mark.parametrize("phrase,skus", [
    ("perceuse dispo avec moins de 100 euros", ["BDEMO-perceuses-2", "BDEMO-perceuses-3"]),
    ("Est-ce que vous avez des perceuses disponibles ?", ["BDEMO-perceuses-2", "BDEMO-perceuses-3", "BDEMO-perceuses-1"]),
    ("Montre-moi les produits qui sont presque en rupture de stock", ["BDEMO-perceuses-1"]),
    ("Quels produits sont complètement en rupture de stock ?", ["BDEMO-perceuses-0"]),
    ("les produits qui vont s'épuiser bientôt", ["BDEMO-perceuses-1"]),
])
def test_uat_exact_sets_with_hostile_order_classification(discovery, db, phrase, skus):
    facts(db)
    before = list(db.b2b_products.find({}).sort("_id", 1))
    result = answer(discovery, phrase)
    assert [item["sku"] for item in result["products"]] == skus
    assert all(item["organization_id"] == "org-a" for item in result["products"])
    if "bientôt" in phrase:
        assert "historique de demande qualifié" in result["text"] and "Aucune date de rupture" in result["text"]
    assert list(db.b2b_products.find({}).sort("_id", 1)) == before


def test_cheapest_ranking_is_tenant_scoped_currency_safe_and_deterministic(discovery, db):
    facts(db)
    db.b2b_products.update_many({"organization_id": "org-b"}, {"$set": {"currency": "USD", "price_minor": 1}})
    phrase = "Quels sont les 5 produits les moins chers de mon catalogue ?"
    result = answer(discovery, phrase)
    expected = sorted(db.b2b_products.find({"organization_id": "org-a", "status": "active"}), key=lambda row: (row["price_minor"], row["sku"]))[:5]
    assert [item["sku"] for item in result["products"]] == [row["sku"] for row in expected]
    assert len(result["products"]) == 5 and all(item["organization_id"] == "org-a" for item in result["products"])
    db.b2b_products.update_one({"organization_id": "org-a", "sku": "BDEMO-perceuses-0"}, {"$set": {"currency": "USD"}})
    reply = answer(discovery, phrase)
    assert reply["intent"] == "CLARIFICATION_REQUIRED" and not reply["products"]
    explicit = answer(discovery, phrase.rstrip(" ?") + " en EUR ?")
    assert len(explicit["products"]) == 5 and all(item["currency"] == "EUR" for item in explicit["products"])
