"""Real scoped Mongo reads and real identity revocation, disposable services only."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from bson import ObjectId
from integration_targets import mongo_test_client
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError, OperationFailure

from stockpilot.application.ports.identity_errors import IdentityRepositoryError
from stockpilot.application.ports.tenant_authorization import TenantContextUnavailable
from stockpilot.application.ports.tenant_catalogue import TenantCatalogueUnavailable
from stockpilot.application.services.identity_authorization import (
    AuthorizationDenied,
    IdentityAuthorizationService,
)
from stockpilot.application.services.tenant_authorization import (
    TenantAuthorizationService,
)
from stockpilot.domain.entities.identity import ExternalIdentity
from stockpilot.domain.entities.tenant_authorization import (
    ResourceReference,
    TenantContext,
)
from stockpilot.domain.entities.tenant_product import TenantProductQuery as Q
from stockpilot.domain.enums.tenant_authorization import AuthorizationDecision as D
from stockpilot.domain.enums.tenant_authorization import ResourceKind as K
from stockpilot.domain.enums.tenant_authorization import TenantOperation as O
from stockpilot.infrastructure.persistence.mongodb.b2b_identity import (
    MongoIdentityRepository,
    MongoMembershipRepository,
    MongoOrganizationRepository,
    initialize_b2b_indexes,
)
from stockpilot.infrastructure.persistence.mongodb.schema_migrations import (
    MigrationRunner,
)
from stockpilot.infrastructure.persistence.mongodb.tenant_catalogue import (
    MongoCatalogueOwnership,
    MongoTenantCatalogue,
)

pytestmark = pytest.mark.integration
REQUESTS = {"org-a": object(), "org-b": object()}
ISSUER = "https://synthetic.example/realms/test"


class SyntheticAuthentication:
    """Test-only authentication for independent, explicitly provisioned humans."""

    def authenticate(self, request):
        for org, token in REQUESTS.items():
            if request is token:
                return ExternalIdentity(ISSUER, "human-" + org)
        return None


class FreshMongoSource:
    def __init__(self, db):
        self.resolver = IdentityAuthorizationService(
            SyntheticAuthentication(),
            MongoIdentityRepository(db),
            MongoOrganizationRepository(db),
            MongoMembershipRepository(db),
        )

    def current(self, request):
        org = next((org for org, token in REQUESTS.items() if request is token), None)
        if org is None:
            return None
        try:
            return TenantContext(
                self.resolver.resolve(request, org), "synthetic-session-" + org
            )
        except AuthorizationDenied:
            return None
        except IdentityRepositoryError:
            raise TenantContextUnavailable("Synthetic identity unavailable.") from None


@pytest.fixture
def db():
    client = mongo_test_client()
    name = "stockpilot_b2b_test_" + uuid4().hex
    try:
        database = client[name]
        assert client.admin.command("hello").get("setName")
        yield database
    finally:
        client.drop_database(name)
        client.close()


def document(org="org-a", sku="DRILL-001", price=4900, quantity=10):
    return {
        "_id": ObjectId(),
        "organization_id": org,
        "sku": sku,
        "name": "Synthetic tool",
        "description": "Integration fixture",
        "category": "drill" if sku.startswith("DRILL") else "saw",
        "price_minor": price,
        "currency": "EUR",
        "status": "active",
        "attributes": ["electric"],
        "quantity": quantity,
        "reserved": 0,
        "revision": 1,
    }


@pytest.fixture
def catalogue(db):
    MigrationRunner(db).apply(confirmation=db.name)
    initialize_b2b_indexes(db)
    now = datetime.now(UTC)
    for org in REQUESTS:
        user = "human-" + org
        db.b2b_users.insert_one(
            {
                "user_id": user,
                "external_identity": {"issuer": ISSUER, "subject": user},
                "status": "active",
                "created_at": now,
                "updated_at": now,
            }
        )
        db.b2b_organizations.insert_one(
            {
                "organization_id": org,
                "name": org,
                "status": "active",
                "created_at": now,
                "updated_at": now,
            }
        )
        db.b2b_memberships.insert_one(
            {
                "membership_id": "m-" + org,
                "user_id": user,
                "organization_id": org,
                "role": "USER",
                "status": "active",
                "created_at": now,
                "updated_at": now,
            }
        )
    docs = [
        document(),
        document("org-a", "SAW-001", 7900, 3),
        document("org-b", "DRILL-001", 12900, 2),
        document("org-b", "SAW-002", 5900, 7),
    ]
    db.b2b_products.insert_many(docs)
    # A decoy proves exact missing identities never fall back to legacy products.
    db.products.insert_one({"sku": "LEGACY-ONLY", "stock": 999, "price": 1})
    source = FreshMongoSource(db)
    authorization = TenantAuthorizationService(
        source, MongoCatalogueOwnership(db), None
    )
    repo = MongoTenantCatalogue(db, authorization)
    contexts = {o: authorization.resolve_context(r) for o, r in REQUESTS.items()}
    return repo, contexts, docs, authorization


@pytest.mark.parametrize(
    "org,prices,stocks,skus",
    [
        ("org-a", [4900, 7900], [10, 3], ["DRILL-001", "SAW-001"]),
        ("org-b", [12900, 5900], [2, 7], ["DRILL-001", "SAW-002"]),
    ],
)
def test_organization_reads_exact_sku_stock_and_pagination(
    catalogue, org, prices, stocks, skus
):
    repo, ctx, _, _ = catalogue
    request, context = REQUESTS[org], ctx[org]
    page = repo.search(request, context, Q())
    assert [p.sku for p in page.items] == skus
    assert [p.price_minor for p in page.items] == prices
    assert page.total == 2 and not page.has_more
    assert [
        repo.stock(request, context, p.product_id).available for p in page.items
    ] == stocks
    for i in range(2):
        part = repo.search(request, context, Q(limit=1, offset=i))
        assert part.total == 2 and part.items == (page.items[i],)
        assert part.has_more == (i == 0)
    assert repo.by_sku(request, context, "DRILL-001").price_minor == prices[0]


@pytest.mark.parametrize(
    "org,foreign_index,foreign_sku", [("org-a", 2, "SAW-002"), ("org-b", 0, "SAW-001")]
)
def test_exact_foreign_and_missing_identifiers_indistinguishable(
    catalogue, org, foreign_index, foreign_sku
):
    repo, contexts, docs, authorization = catalogue
    request, context = REQUESTS[org], contexts[org]
    foreign_id = str(docs[foreign_index]["_id"])
    for key in (foreign_id, str(ObjectId()), "malformed"):
        assert repo.by_id(request, context, key) is None
        assert repo.stock(request, context, key) is None
        assert not repo.available(request, context, key, 1)
    for sku in (foreign_sku, "LEGACY-ONLY", "missing", "drill-001", ".*", "$where"):
        assert repo.by_sku(request, context, sku) is None
    assert (
        authorization.decide(
            request,
            context,
            O.CATALOGUE_READ,
            ResourceReference(K.CATALOGUE_ITEM, foreign_id),
        )
        is D.NOT_FOUND
    )


@pytest.mark.parametrize("org,price", [("org-a", 4900), ("org-b", 5900)])
def test_price_category_attributes_status_filters(catalogue, org, price):
    repo, contexts, _, _ = catalogue
    page = repo.search(
        REQUESTS[org],
        contexts[org],
        Q(
            maximum_price_minor=6000,
            currency="EUR",
            attribute="electric",
            status="active",
        ),
    )
    assert page.total == 1 and page.items[0].price_minor == price
    page = repo.search(REQUESTS[org], contexts[org], Q(category="saw"))
    assert page.total == 1 and page.items[0].organization_id == org
    assert not repo.search(REQUESTS[org], contexts[org], Q(category=".*")).items


@pytest.mark.parametrize(
    "cause",
    [
        "missing",
        "forged",
        "unknown_org",
        "inactive_org",
        "revoked",
        "switched_session",
        "disabled_user",
    ],
)
def test_context_revalidation_denies_before_reads(db, catalogue, cause):
    repo, contexts, _, _ = catalogue
    context = contexts["org-a"]
    request = REQUESTS["org-a"]
    if cause == "missing":
        context = None
    elif cause == "forged":
        context = replace(
            context, principal=replace(context.principal, organization_id="org-b")
        )
    elif cause == "unknown_org":
        db.b2b_organizations.delete_one({"organization_id": "org-a"})
    elif cause == "inactive_org":
        db.b2b_organizations.update_one(
            {"organization_id": "org-a"}, {"$set": {"status": "suspended"}}
        )
    elif cause == "revoked":
        db.b2b_memberships.update_one(
            {"organization_id": "org-a"}, {"$set": {"status": "suspended"}}
        )
    elif cause == "switched_session":
        request = REQUESTS["org-b"]
    elif cause == "disabled_user":
        db.b2b_users.update_one(
            {"user_id": "human-org-a"}, {"$set": {"status": "disabled"}}
        )
    with pytest.raises(AuthorizationDenied):
        repo.search(request, context, Q())
    with pytest.raises(AuthorizationDenied):
        repo.by_sku(request, context, "DRILL-001")


@pytest.mark.parametrize(
    "value", [{"$ne": None}, {"$where": "true"}, ["DRILL-001"], None, True]
)
def test_injection_rejected(catalogue, value):
    repo, contexts, _, _ = catalogue
    with pytest.raises(ValueError):
        repo.by_sku(REQUESTS["org-a"], contexts["org-a"], value)
    with pytest.raises(ValueError):
        Q(category=value if value is not None else {})
    with pytest.raises(ValueError):
        Q(minimum_price_minor=value if value is not None else {})


def test_concurrent_same_tenant_duplicate_rejected_cross_tenant_allowed(db, catalogue):
    def insert(_):
        try:
            db.b2b_products.insert_one(document(sku="RACE"))
            return True
        except DuplicateKeyError:
            return False

    with ThreadPoolExecutor(max_workers=4) as executor:
        assert sum(executor.map(insert, range(4))) == 1
    db.b2b_products.insert_one(document("org-b", "RACE"))
    assert db.b2b_products.count_documents({"sku": "RACE"}) == 2


def test_stock_reservation_facts_and_no_mutation(db, catalogue):
    repo, contexts, docs, _ = catalogue
    key = docs[0]["_id"]
    db.b2b_products.update_one(
        {"organization_id": "org-a", "_id": key}, {"$set": {"reserved": 4}}
    )
    assert repo.stock(REQUESTS["org-a"], contexts["org-a"], str(key)).available == 6
    assert repo.available(REQUESTS["org-a"], contexts["org-a"], str(key), 6)
    assert not repo.available(REQUESTS["org-a"], contexts["org-a"], str(key), 7)
    assert db.b2b_products.find_one({"_id": key})["reserved"] == 4
    db.b2b_products.update_one({"_id": key}, {"$set": {"status": "inactive"}})
    assert not repo.available(REQUESTS["org-a"], contexts["org-a"], str(key), 1)


@pytest.mark.parametrize(
    "changes",
    [
        {"reserved": 11},
        {"quantity": -1},
        {"price_minor": True},
        {"organization_id": None},
        {"revision": 0},
    ],
)
def test_database_validator_rejects_invalid_facts(db, catalogue, changes):
    with pytest.raises(OperationFailure):
        db.b2b_products.insert_one({**document(sku="INVALID"), **changes})


def test_corrupt_data_and_duplicate_sku_fail_closed(db, catalogue):
    repo, contexts, docs, _ = catalogue
    db.b2b_products.update_one(
        {"_id": docs[0]["_id"]},
        {"$set": {"reserved": 100}},
        bypass_document_validation=True,
    )
    with pytest.raises(TenantCatalogueUnavailable):
        repo.by_sku(REQUESTS["org-a"], contexts["org-a"], "DRILL-001")
    with pytest.raises(TenantCatalogueUnavailable):
        repo.search(REQUESTS["org-a"], contexts["org-a"], Q())
    db.b2b_products.drop_index("b2b_org_sku_unique")
    db.b2b_products.insert_one(document())
    with pytest.raises(TenantCatalogueUnavailable):
        repo.by_sku(REQUESTS["org-a"], contexts["org-a"], "DRILL-001")


def test_real_unavailable_endpoint_no_fallback(catalogue):
    repo, contexts, _, authorization = catalogue
    with MongoClient(
        "mongodb://127.0.0.1:1", serverSelectionTimeoutMS=100, connectTimeoutMS=100
    ) as client:
        broken = MongoTenantCatalogue(client[repo.db.name], authorization)
        with pytest.raises(TenantCatalogueUnavailable):
            broken.by_sku(REQUESTS["org-a"], contexts["org-a"], "DRILL-001")
        assert MongoCatalogueOwnership(client[repo.db.name])
        broken_auth = TenantAuthorizationService(
            authorization.contexts, MongoCatalogueOwnership(client[repo.db.name]), None
        )
        assert (
            broken_auth.decide(
                REQUESTS["org-a"],
                contexts["org-a"],
                O.CATALOGUE_READ,
                ResourceReference(K.CATALOGUE_ITEM, str(ObjectId())),
            )
            is D.INDETERMINATE
        )


def test_explain_uses_tenant_compound_indexes(db, catalogue):
    query = {"organization_id": "org-a", "sku": "DRILL-001"}
    plan = db.command(
        "explain", {"find": "b2b_products", "filter": query}, verbosity="executionStats"
    )
    assert plan["executionStats"]["nReturned"] == 1
    assert plan["executionStats"]["totalDocsExamined"] == 1
    assert "b2b_org_sku_unique" in str(plan["queryPlanner"]["winningPlan"])
