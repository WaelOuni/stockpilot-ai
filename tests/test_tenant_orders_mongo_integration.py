"""Real transactions on guarded disposable replica set; synthetic identities only."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import Event
from uuid import uuid4

import pytest
from pymongo.client_session import ClientSession
from pymongo.errors import DuplicateKeyError, OperationFailure
from test_tenant_catalogue_mongo_integration import db as db  # noqa: PLC0414
from test_tenant_catalogue_mongo_integration import document

from stockpilot.application.ports.identity_errors import IdentityRepositoryError
from stockpilot.application.ports.tenant_authorization import TenantContextUnavailable
from stockpilot.application.ports.tenant_orders import (
    TenantOrderConflict,
    TenantOrderNotFound,
    TenantOrderOutcomeUnknown,
    TenantOrdersUnavailable,
)
from stockpilot.application.services.identity_authorization import (
    AuthorizationDenied,
    IdentityAuthorizationService,
)
from stockpilot.application.services.tenant_authorization import (
    TenantAuthorizationService,
)
from stockpilot.domain.entities.identity import ExternalIdentity
from stockpilot.domain.entities.tenant_authorization import TenantContext
from stockpilot.domain.entities.tenant_order import (
    CancelTenantOrder,
    ConfirmTenantOrder,
    PrepareTenantOrder,
    RequestedLine,
)
from stockpilot.domain.entities.tenant_order import (
    TenantOrderStatus as S,
)
from stockpilot.infrastructure.persistence.mongodb.b2b_identity import (
    MongoIdentityRepository,
    MongoMembershipRepository,
    MongoOrganizationRepository,
    initialize_b2b_indexes,
)
from stockpilot.infrastructure.persistence.mongodb.schema_migrations import (
    MigrationRunner,
    registry,
)
from stockpilot.infrastructure.persistence.mongodb.tenant_orders import (
    MongoBuyerAssociations,
    MongoTenantOrders,
)

pytestmark = pytest.mark.integration
ISSUER = "https://synthetic.example/realms/b2"
ACTORS = {
    "a1": ("org-a", "USER"),
    "a2": ("org-a", "MANAGER"),
    "a3": ("org-a", "USER"),
    "b1": ("org-b", "USER"),
}
REQUESTS = {user: object() for user in ACTORS}


class Authentication:
    def authenticate(self, request):
        return next(
            (
                ExternalIdentity(ISSUER, u)
                for u, token in REQUESTS.items()
                if request is token
            ),
            None,
        )


class Source:
    def __init__(self, db):
        self.resolver = IdentityAuthorizationService(
            Authentication(),
            MongoIdentityRepository(db),
            MongoOrganizationRepository(db),
            MongoMembershipRepository(db),
        )

    def current(self, request):
        user = next((u for u, token in REQUESTS.items() if request is token), None)
        if user is None:
            return None
        try:
            return TenantContext(
                self.resolver.resolve(request, ACTORS[user][0]), "session-" + user
            )
        except AuthorizationDenied:
            return None
        except IdentityRepositoryError:
            raise TenantContextUnavailable("Synthetic source unavailable.") from None


@pytest.fixture
def orders(db):
    MigrationRunner(db).apply(confirmation=db.name)
    initialize_b2b_indexes(db)
    now = datetime.now(UTC)
    for org in ("org-a", "org-b"):
        db.b2b_organizations.insert_one(
            {
                "organization_id": org,
                "name": org,
                "status": "active",
                "created_at": now,
                "updated_at": now,
            }
        )
    for user, (org, role) in ACTORS.items():
        db.b2b_users.insert_one(
            {
                "user_id": user,
                "external_identity": {"issuer": ISSUER, "subject": user},
                "status": "active",
                "created_at": now,
                "updated_at": now,
            }
        )
        db.b2b_memberships.insert_one(
            {
                "membership_id": "m-" + user,
                "user_id": user,
                "organization_id": org,
                "role": role,
                "status": "active",
                "created_at": now,
                "updated_at": now,
            }
        )
        db.b2b_buyers.insert_one(
            {
                "association_id": "buyer-" + user,
                "organization_id": org,
                "user_id": user,
                "customer_id": "customer-" + org,
                "status": "active",
                "revision": 1,
            }
        )
    db.b2b_products.insert_many(
        [
            document("org-a", "DRILL-001", quantity=10),
            document("org-a", "SAW-001", quantity=3),
            document("org-b", "DRILL-001", quantity=2),
            document("org-b", "SAW-002", quantity=7),
        ]
    )
    source = Source(db)
    auth = TenantAuthorizationService(source, None, MongoBuyerAssociations(db))
    return MongoTenantOrders(db, auth), source


def context(orders, user="a1"):
    return orders[1].current(REQUESTS[user])


def prepare(orders, user="a1", operation=None, quantity=1, lines=None, customer=None):
    service = orders[0]
    command = PrepareTenantOrder(
        operation or uuid4().hex,
        customer or "customer-" + ACTORS[user][0],
        lines or (RequestedLine("DRILL-001", quantity),),
    )
    return service.prepare(REQUESTS[user], context(orders, user), command)


def confirm_command(order, operation=None):
    return ConfirmTenantOrder(
        operation or uuid4().hex, order.order_id, order.preparation_digest, True
    )


def confirm(orders, order, user="a1", operation=None):
    return orders[0].confirm(
        REQUESTS[user], context(orders, user), confirm_command(order, operation)
    )


def cancel(orders, order, user="a1", operation=None):
    return orders[0].cancel(
        REQUESTS[user],
        context(orders, user),
        CancelTenantOrder(operation or uuid4().hex, order.order_id),
    )


def facts(db, org="org-a", sku="DRILL-001"):
    row = db.b2b_products.find_one({"organization_id": org, "sku": sku})
    return row["quantity"], row["reserved"], row["revision"]


def test_prepare_confirm_cancel_and_other_org_unchanged(db, orders):
    foreign = list(db.b2b_products.find({"organization_id": "org-b"}))
    order = prepare(orders, quantity=3)
    assert order.status is S.PREPARED and facts(db) == (10, 0, 1)
    assert confirm(orders, order).status is S.RESERVED
    assert facts(db) == (10, 3, 2)
    assert cancel(orders, order).status is S.CANCELLED
    assert facts(db) == (10, 0, 3)
    assert list(db.b2b_products.find({"organization_id": "org-b"})) == foreign


def test_same_sku_and_operation_in_two_orgs_independent(db, orders):
    a = prepare(orders, operation="shared")
    b = prepare(orders, user="b1", operation="shared", quantity=2)
    confirm(orders, a, operation="shared")
    confirm(orders, b, user="b1", operation="shared")
    assert facts(db) == (10, 1, 2)
    assert facts(db, "org-b") == (2, 2, 2)
    assert db.b2b_order_operations.count_documents({"operation_id": "shared"}) == 4


def test_canonical_prepare_and_payload_conflict(db, orders):
    lines = (RequestedLine("SAW-001", 1), RequestedLine("DRILL-001", 2))
    a = prepare(orders, operation="op", lines=lines)
    assert (
        prepare(orders, operation="op", lines=tuple(reversed(lines))).order_id
        == a.order_id
    )
    with pytest.raises(TenantOrderConflict):
        prepare(orders, operation="op", quantity=3)
    assert facts(db) == (10, 0, 1)
    assert db.b2b_orders.count_documents({}) == 1


def test_replay_after_adapter_restart_and_cancel(db, orders):
    draft = prepare(orders)
    command = confirm_command(draft, "confirm-op")
    first = orders[0].confirm(REQUESTS["a1"], context(orders), command)
    restarted = MongoTenantOrders(db, orders[0].authorization)
    assert restarted.confirm(REQUESTS["a1"], context(orders), command) == first
    cancelled = cancel(orders, draft, operation="cancel-op")
    assert restarted.confirm(REQUESTS["a1"], context(orders), command) == cancelled
    assert cancel(orders, draft, operation="cancel-op") == cancelled
    assert cancel(orders, draft, operation="another-cancel").status is S.CANCELLED
    assert facts(db) == (10, 0, 3)


@pytest.mark.parametrize("method", ["get", "cancel", "confirm"])
def test_foreign_order_same_as_missing(db, orders, method):
    draft = prepare(orders)
    for order_id in (draft.order_id, "not-found"):
        with pytest.raises(TenantOrderNotFound):
            if method == "get":
                orders[0].get(REQUESTS["b1"], context(orders, "b1"), order_id)
            elif method == "cancel":
                cancel(orders, replace(draft, order_id=order_id), user="b1")
            else:
                confirm(orders, replace(draft, order_id=order_id), user="b1")
    assert facts(db) == (10, 0, 1)


def test_shared_buyer_does_not_grant_own_access_manager_can_cancel(db, orders):
    draft = prepare(orders)
    confirm(orders, draft)
    with pytest.raises(TenantOrderNotFound):
        orders[0].get(REQUESTS["a3"], context(orders, "a3"), draft.order_id)
    with pytest.raises(TenantOrderNotFound):
        cancel(orders, draft, user="a3")
    assert (
        orders[0].get(REQUESTS["a2"], context(orders, "a2"), draft.order_id).created_by
        == "a1"
    )
    assert cancel(orders, draft, user="a2").status is S.CANCELLED
    assert facts(db) == (10, 0, 3)


def test_foreign_buyer_no_mapping_no_implicit_creation(db, orders):
    before = db.b2b_buyers.count_documents({})
    with pytest.raises(TenantOrderNotFound):
        prepare(orders, customer="customer-org-b")
    with pytest.raises(TenantOrderNotFound):
        prepare(orders, customer="a1")
    assert db.b2b_buyers.count_documents({}) == before
    assert db.b2b_orders.count_documents({}) == 0


@pytest.mark.parametrize(
    "target,query,change",
    [
        ("b2b_users", {"user_id": "a1"}, {"status": "disabled"}),
        ("b2b_organizations", {"organization_id": "org-a"}, {"status": "suspended"}),
        ("b2b_memberships", {"user_id": "a1"}, {"status": "suspended"}),
        ("b2b_memberships", {"user_id": "a1"}, {"role": "READ_ONLY"}),
    ],
)
def test_stale_context_and_revoked_identity(db, orders, target, query, change):
    ctx = context(orders)
    draft = prepare(orders)
    db[target].update_one(query, {"$set": change})
    with pytest.raises(AuthorizationDenied):
        orders[0].confirm(REQUESTS["a1"], ctx, confirm_command(draft))
    assert facts(db) == (10, 0, 1)


def test_disabled_mapping_blocks_confirmation_replay_and_manager(db, orders):
    draft = prepare(orders)
    command = confirm_command(draft)
    orders[0].confirm(REQUESTS["a1"], context(orders), command)
    db.b2b_buyers.update_one(
        {"user_id": "a1"}, {"$set": {"status": "disabled"}, "$inc": {"revision": 1}}
    )
    with pytest.raises(TenantOrderNotFound):
        orders[0].confirm(REQUESTS["a1"], context(orders), command)
    with pytest.raises(TenantOrderNotFound):
        cancel(orders, draft, user="a2")
    assert facts(db) == (10, 1, 2)


@pytest.mark.parametrize(
    "change", ["price", "currency", "inactive", "replace", "stock"]
)
def test_changes_between_prepare_confirm(db, orders, change):
    draft = prepare(orders, quantity=2)
    query = {"organization_id": "org-a", "sku": "DRILL-001"}
    if change == "replace":
        db.b2b_products.delete_one(query)
        db.b2b_products.insert_one(document(quantity=10))
    else:
        fields = {
            "price": {"price_minor": 5000},
            "currency": {"currency": "USD"},
            "inactive": {"status": "inactive"},
            "stock": {"quantity": 1},
        }[change]
        db.b2b_products.update_one(query, {"$set": fields, "$inc": {"revision": 1}})
    with pytest.raises(TenantOrderConflict):
        confirm(orders, draft)
    assert db.b2b_products.find_one(query)["reserved"] == 0
    assert db.b2b_order_operations.count_documents({"namespace": "confirm"}) == 0


def test_multiline_abort_restores_all_stock_and_operation_ledger(db, orders):
    draft = prepare(
        orders, lines=(RequestedLine("DRILL-001", 2), RequestedLine("SAW-001", 3))
    )
    db.b2b_products.update_one(
        {"organization_id": "org-a", "sku": "SAW-001"},
        {"$set": {"quantity": 2}, "$inc": {"revision": 1}},
    )
    with pytest.raises(TenantOrderConflict):
        confirm(orders, draft)
    assert facts(db) == (10, 0, 1)
    assert facts(db, sku="SAW-001")[1] == 0
    assert db.b2b_orders.find_one({"order_id": draft.order_id})["status"] == "prepared"
    assert db.b2b_order_operations.count_documents({"namespace": "confirm"}) == 0


@pytest.mark.parametrize("kind", ["digest", "session", "expired", "actor"])
def test_preparation_bound_to_digest_actor_session_and_expiry(db, orders, kind):
    draft = prepare(orders)
    cmd = confirm_command(draft)
    service, ctx, actor = orders[0], context(orders), "a1"
    if kind == "digest":
        cmd = replace(cmd, preparation_digest="0" * 64)
    elif kind == "session":
        source = orders[1]
        original = source.current
        source.current = lambda request: replace(
            original(request), authorization_context_id="new-session"
        )
        ctx = context(orders)
    elif kind == "expired":
        service.clock = lambda: datetime.now(UTC) + timedelta(hours=1)
    else:
        actor, ctx = "a2", context(orders, "a2")
    with pytest.raises((TenantOrderConflict, AuthorizationDenied)):
        service.confirm(REQUESTS[actor], ctx, cmd)
    assert facts(db) == (10, 0, 1)


def test_concurrent_same_confirmation_and_cancellation_at_most_once(db, orders):
    draft = prepare(orders, quantity=2)
    cmd = confirm_command(draft)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _: orders[0].confirm(REQUESTS["a1"], context(orders), cmd),
                range(2),
            )
        )
    assert all(r.order_id == draft.order_id for r in results)
    assert facts(db) == (10, 2, 2)
    cancel_cmd = CancelTenantOrder("cancel-op", draft.order_id)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _: orders[0].cancel(REQUESTS["a1"], context(orders), cancel_cmd),
                range(2),
            )
        )
    assert all(r.status is S.CANCELLED for r in results)
    assert facts(db) == (10, 0, 3)


def test_two_confirm_keys_same_preparation_only_one_reservation(db, orders):
    draft = prepare(orders, quantity=3)

    def run(_):
        try:
            return confirm(orders, draft).status
        except TenantOrderConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, range(2)))
    assert results.count(S.RESERVED) == 1 and results.count("conflict") == 1
    assert facts(db) == (10, 3, 2)


def test_two_orders_compete_for_last_stock(db, orders):
    a, b = prepare(orders, quantity=6), prepare(orders, quantity=6)

    def run(draft):
        try:
            return confirm(orders, draft).status
        except TenantOrderConflict:
            return "insufficient"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, (a, b)))
    assert results.count(S.RESERVED) == 1
    assert facts(db) == (10, 6, 2)


def test_confirm_cancel_race_never_double_release(db, orders):
    draft = prepare(orders, quantity=4)

    def reserve():
        try:
            return confirm(orders, draft)
        except TenantOrderConflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = pool.submit(reserve), pool.submit(cancel, orders, draft)
        a.result(timeout=15)
        assert b.result(timeout=15).status is S.CANCELLED
    assert facts(db)[1] == 0
    assert (
        orders[0].get(REQUESTS["a1"], context(orders), draft.order_id).status
        is S.CANCELLED
    )


def test_real_revocation_between_snapshot_and_membership_write(db, orders, monkeypatch):
    draft = prepare(orders)
    ctx = context(orders)
    entered, finish = Event(), Event()
    original = orders[0]._fence
    first = [True]

    def pause(collection, query, session):
        if collection.name == "b2b_memberships" and first[0]:
            first[0] = False
            entered.set()
            assert finish.wait(10)
        return original(collection, query, session)

    monkeypatch.setattr(orders[0], "_fence", pause)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            orders[0].confirm, REQUESTS["a1"], ctx, confirm_command(draft)
        )
        assert entered.wait(10)
        try:
            db.b2b_memberships.update_one(
                {"user_id": "a1"}, {"$set": {"status": "suspended"}}
            )
        finally:
            finish.set()
        with pytest.raises(AuthorizationDenied):
            future.result(timeout=15)
    assert facts(db) == (10, 0, 1)


@pytest.mark.parametrize("action", ["confirm", "cancel"])
def test_unknown_real_commit_reconciles_and_replay_never_repeats_stock(
    db, orders, monkeypatch, action
):
    draft = prepare(orders)
    if action == "cancel":
        confirm(orders, draft)
    command = (
        confirm_command(draft)
        if action == "confirm"
        else CancelTenantOrder("cancel", draft.order_id)
    )
    original = ClientSession.commit_transaction
    count = [0]

    def lost_response(session):
        result = original(session)  # Real commit has happened.
        count[0] += 1
        if count[0] <= 4:
            raise OperationFailure(
                "synthetic lost response",
                details={"errorLabels": ["UnknownTransactionCommitResult"]},
            )
        return result

    monkeypatch.setattr(ClientSession, "commit_transaction", lost_response)
    method = getattr(orders[0], action)
    result = method(REQUESTS["a1"], context(orders), command)
    assert result.status is (S.RESERVED if action == "confirm" else S.CANCELLED)
    assert count[0] == 5
    before = facts(db)
    assert method(REQUESTS["a1"], context(orders), command) == result
    assert facts(db) == before


def test_unknown_result_not_reported_as_abort_then_restart_replay(
    db, orders, monkeypatch
):
    draft = prepare(orders)
    command = confirm_command(draft)
    original = ClientSession.commit_transaction

    def lost(session):
        original(session)
        raise OperationFailure(
            "lost", details={"errorLabels": ["UnknownTransactionCommitResult"]}
        )

    with monkeypatch.context() as scoped:
        scoped.setattr(ClientSession, "commit_transaction", lost)
        with pytest.raises(TenantOrderOutcomeUnknown):
            orders[0].confirm(REQUESTS["a1"], context(orders), command)
    assert facts(db) == (10, 1, 2)
    restarted = MongoTenantOrders(db, orders[0].authorization)
    assert (
        restarted.confirm(REQUESTS["a1"], context(orders), command).status is S.RESERVED
    )
    assert facts(db) == (10, 1, 2)


def test_transient_write_conflict_retries_body_once_without_double_stock(
    db, orders, monkeypatch
):
    draft = prepare(orders)
    original = orders[0]._identity
    calls = [0]

    def conflict(principal, session):
        original(principal, session)
        calls[0] += 1
        if calls[0] == 1:
            raise OperationFailure(
                "conflict",
                code=112,
                details={"errorLabels": ["TransientTransactionError"]},
            )

    monkeypatch.setattr(orders[0], "_identity", conflict)
    assert confirm(orders, draft).status is S.RESERVED
    assert calls[0] == 2 and facts(db) == (10, 1, 2)


def test_inconsistent_reservation_refuses_cancellation(db, orders):
    draft = prepare(orders, quantity=3)
    confirm(orders, draft)
    db.b2b_products.update_one(
        {"organization_id": "org-a", "sku": "DRILL-001"}, {"$set": {"reserved": 1}}
    )
    with pytest.raises(TenantOrdersUnavailable):
        cancel(orders, draft)
    assert facts(db)[1] == 1
    assert db.b2b_orders.find_one({"order_id": draft.order_id})["status"] == "reserved"


def test_buyer_and_operation_indexes_unique_without_ttl(db, orders):
    grant = db.b2b_buyers.find_one({"user_id": "a1"})
    grant.pop("_id")
    with pytest.raises(DuplicateKeyError):
        db.b2b_buyers.insert_one(grant)
    for name in ("b2b_buyers", "b2b_orders", "b2b_order_operations"):
        assert all("expireAfterSeconds" not in i for i in db[name].list_indexes())


def test_0001_upgrade_to_0002_keeps_products_and_history(db):
    first = registry()[0]
    MigrationRunner(db, [first]).apply(confirmation=db.name)
    product = document()
    db.b2b_products.insert_one(product)
    checksum = db.stockpilot_schema_migrations.find_one({"_id": 1})["checksum"]
    # This test pins the B2 upgrade; B3 separately covers 0002 -> 0003.
    runner = MigrationRunner(db, migrations=registry()[:2])
    assert [m.version for m in runner.plan()] == [2]
    runner.apply(confirmation=db.name)
    runner.require_version(1)
    runner.require_version(2)
    assert db.b2b_products.find_one({"_id": product["_id"]}) == product
    assert db.stockpilot_schema_migrations.find_one({"_id": 1})["checksum"] == checksum
    assert [r["_id"] for r in runner.validate()] == [1, 2]
