"""Two adapter instances sharing the dedicated guarded MongoDB, never a demo."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta, datetime, UTC

import pytest
from test_bff_mongo_integration import owned_database as owned_database
from test_bff_renewal import renewal_env
from stockpilot.application.ports.bff_security import BffUnauthenticated, BffUnavailable
from stockpilot.infrastructure.persistence.mongodb.bff_store import MongoBffStore

pytestmark = pytest.mark.integration


def test_shared_workers_claim_once_and_store_only_authenticated_ciphertext(owned_database):
    first = MongoBffStore(owned_database)
    service, token, clock, provider = renewal_env(first, start=datetime.now(UTC))
    second, _, _, _ = renewal_env(MongoBffStore(owned_database), start=datetime.now(UTC))
    second.clock = service.clock
    second.provider = provider
    clock[0] += timedelta(seconds=275)
    with ThreadPoolExecutor(max_workers=8) as executor:
        outcomes = list(executor.map(lambda index: (service if index % 2 else second).current(token)[0], range(8)))
    assert provider.refresh_calls == 1
    assert {s.refresh_version for s in outcomes} == {1}
    assert all(s.absolute_expires_at == outcomes[0].absolute_expires_at for s in outcomes)
    document = owned_database.bff_sessions.find_one({"_id": outcomes[0].session_hash})
    assert "test-initial-refresh" not in str(document)
    assert "test-rotated-refresh" not in str(document)
    assert "refresh_owner" not in document


def test_revocation_and_expiry_win_over_a_claimed_refresh(owned_database):
    store = MongoBffStore(owned_database)
    service, token, clock, _ = renewal_env(store, start=datetime.now(UTC))
    snapshot, _ = service.current(token)
    assert store.claim_refresh(snapshot, "owner", clock[0])
    store.revoke_session(snapshot.session_hash, snapshot.configuration)
    assert not store.finish_refresh(snapshot, "owner", "ciphertext", clock[0]+timedelta(minutes=5), clock[0])
    assert owned_database.bff_sessions.find_one({"_id": snapshot.session_hash}) is None


def test_stuck_or_ambiguous_grant_is_never_taken_over(owned_database):
    service, token, clock, provider = renewal_env(MongoBffStore(owned_database), start=datetime.now(UTC))
    snapshot, _ = service.current(token)
    assert service.store.claim_refresh(snapshot, "crashed-worker", clock[0])
    clock[0] += timedelta(seconds=275)
    with pytest.raises(BffUnavailable):
        service.current(token)
    assert provider.refresh_calls == 0
    service.logout(token, snapshot.csrf_token)
    with pytest.raises(BffUnauthenticated):
        service.current(token)
