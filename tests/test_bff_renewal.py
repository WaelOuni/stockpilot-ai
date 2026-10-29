"""Renewal security with a simulated clock; no timing or real-provider claims."""
import base64
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from threading import Event

import pytest

from test_bff_auth import MemoryStore, Provider, IdentityRepositories, NOW, IDENTITY, settings
from stockpilot.application.ports.bff_security import BffUnauthenticated, BffUnavailable
from stockpilot.application.services.bff_sessions import BffSessionService, BffPolicy
from stockpilot.infrastructure.identity.token_protector import AesTokenProtector


class RenewalStore(MemoryStore):
    def __init__(self):
        super().__init__()
        self.claims = {}

    def claim_refresh(self, session, owner, now):
        with self.lock:
            item = self.sessions.get(session.session_hash)
            if not item or item.refresh_version != session.refresh_version or session.session_hash in self.claims:
                return False
            self.claims[session.session_hash] = owner
            return True

    def finish_refresh(self, session, owner, ciphertext, expires, now):
        with self.lock:
            item = self.sessions.get(session.session_hash)
            if not item or self.claims.get(session.session_hash) != owner or min(item.absolute_expires_at, item.idle_expires_at) <= now:
                return False
            self.sessions[session.session_hash] = replace(item, refresh_version=item.refresh_version + 1,
                refresh_ciphertext=ciphertext, provider_expires_at=expires)
            del self.claims[session.session_hash]
            return True


class RenewalProvider(Provider):
    def __init__(self, clock):
        super().__init__()
        self.clock = clock
        self.refresh_calls = 0
        self.error = None
        self.started, self.release = Event(), Event()
        self.block = False
        self.rotate = True

    def exchange(self, *args):
        return replace(super().exchange(*args), expires_at=self.clock[0] + timedelta(seconds=300), refresh_token="test-initial-refresh")

    def refresh(self, token, nonce):
        self.refresh_calls += 1
        self.started.set()
        if self.block:
            assert self.release.wait(5)
        if self.error:
            raise self.error
        assert token.startswith("test-") and nonce == self.nonce
        return replace(super().exchange("one-code", self.nonce, self.verifier),
            expires_at=self.clock[0] + timedelta(seconds=300),
            refresh_token="test-rotated-refresh" if self.rotate else None)


def renewal_env(store=None, start=NOW):
    clock = [start]
    provider, repos = RenewalProvider(clock), IdentityRepositories()
    store = store or RenewalStore()
    protector = AesTokenProtector(base64.b64encode(b"x" * 32).decode())
    service = BffSessionService(provider, store, repos, repos, repos,
        BffPolicy("renewal-test", absolute_seconds=1800, idle_seconds=600),
        clock=lambda: clock[0], protector=protector)
    _, browser = service.begin("o1", None)
    token = service.complete(provider.state, browser, "one-code")
    return service, token, clock, provider


def test_valid_token_does_not_refresh_and_absolute_is_not_sliding():
    s, token, clock, provider = renewal_env()
    first, _ = s.current(token)
    assert provider.refresh_calls == 0
    for seconds in range(270, 1800, 270):
        clock[0] = NOW + timedelta(seconds=seconds)
        current, _ = s.current(token)
        assert current.absolute_expires_at == first.absolute_expires_at == NOW + timedelta(seconds=1800)
    clock[0] = NOW + timedelta(seconds=1800)
    with pytest.raises(BffUnauthenticated):
        s.current(token)


@pytest.mark.parametrize("rotate", [True, False])
def test_rotation_and_absent_rotation_preserve_encrypted_credentials(rotate):
    s, token, clock, provider = renewal_env()
    provider.rotate = rotate
    first, _ = s.current(token)
    assert "test-initial-refresh" not in first.refresh_ciphertext
    assert "test-initial-refresh" not in repr(first)
    clock[0] += timedelta(seconds=275)
    second, _ = s.current(token)
    assert second.refresh_version == 1 and provider.refresh_calls == 1
    assert s.protector.open(second.refresh_ciphertext, s._binding(second))[0] == (
        "test-rotated-refresh" if rotate else "test-initial-refresh")


def test_concurrent_requests_share_one_grant():
    s, token, clock, provider = renewal_env()
    clock[0] += timedelta(seconds=275)
    with ThreadPoolExecutor(max_workers=12) as executor:
        sessions = list(executor.map(lambda _: s.current(token)[0], range(12)))
    assert provider.refresh_calls == 1
    assert {v.refresh_version for v in sessions} == {1}


@pytest.mark.parametrize("error", [BffUnauthenticated("revoked"), BffUnavailable("offline")])
def test_failed_or_ambiguous_grant_revokes_without_retry(error):
    s, token, clock, provider = renewal_env()
    provider.error = error
    clock[0] += timedelta(seconds=275)
    with pytest.raises(type(error)):
        s.current(token)
    with pytest.raises(BffUnauthenticated):
        s.current(token)
    assert provider.refresh_calls == 1


def test_logout_during_refresh_cannot_resurrect_session():
    s, token, clock, provider = renewal_env()
    session, _ = s.current(token)
    clock[0] += timedelta(seconds=275)
    provider.block = True
    with ThreadPoolExecutor() as executor:
        pending = executor.submit(s.current, token)
        assert provider.started.wait(2)
        s.logout(token, session.csrf_token)
        provider.release.set()
        with pytest.raises(BffUnauthenticated):
            pending.result()
    assert not s.store.sessions


@pytest.mark.parametrize("expiry", [600, 1800])
def test_expiration_during_grant_never_resurrects(expiry):
    s, token, clock, provider = renewal_env()
    clock[0] += timedelta(seconds=275)
    original = provider.refresh
    def delayed(*args):
        result = original(*args)
        clock[0] = NOW + timedelta(seconds=expiry + 600)
        return replace(result, expires_at=clock[0] + timedelta(seconds=300))
    provider.refresh = delayed
    with pytest.raises(BffUnauthenticated):
        s.current(token)
    assert not s.store.sessions


def test_idle_expiry_does_not_contact_provider():
    s, token, clock, provider = renewal_env()
    clock[0] += timedelta(seconds=601)
    with pytest.raises(BffUnauthenticated):
        s.current(token)
    assert provider.refresh_calls == 0


def test_altered_or_cross_session_ciphertext_is_refused():
    s, token, clock, provider = renewal_env()
    first, _ = s.current(token)
    with pytest.raises(BffUnauthenticated):
        s.protector.open(first.refresh_ciphertext, s._binding(replace(first, organization_id="other")))
    with pytest.raises(BffUnauthenticated):
        s.protector.open(first.refresh_ciphertext[:-4] + "AAAA", s._binding(first))


def test_renewal_requires_explicit_key_and_changes_configuration_only_when_enabled():
    old = settings().validated_policy()
    assert settings(refresh_encryption_key="unused").validated_policy() == old
    with pytest.raises(BffUnavailable):
        settings(refresh_enabled=True).validated_policy()
    key = base64.b64encode(b"x" * 32).decode()
    assert settings(refresh_enabled=True, refresh_encryption_key=key).validated_policy().configuration != old.configuration


def test_original_identity_cannot_change_during_refresh():
    s, token, clock, provider = renewal_env()
    original = provider.refresh
    provider.refresh = lambda *args: replace(original(*args), identity=replace(IDENTITY, subject="other"))
    clock[0] += timedelta(seconds=275)
    with pytest.raises(BffUnauthenticated):
        s.current(token)
    assert not s.store.sessions


def test_original_audience_cannot_expand_even_with_the_same_client():
    s, token, clock, provider = renewal_env()
    original = provider.refresh
    provider.refresh = lambda *args: replace(original(*args), audience=("bff", "other"))
    clock[0] += timedelta(seconds=275)
    with pytest.raises(BffUnauthenticated):
        s.current(token)
    assert not s.store.sessions


def test_legacy_session_without_renewal_fields_keeps_original_token_cap():
    s, token, clock, provider = renewal_env()
    original, _ = s.current(token)
    s.store.sessions[original.session_hash] = replace(original, provider_expires_at=None,
        refresh_ciphertext=None, absolute_expires_at=NOW + timedelta(seconds=300))
    clock[0] += timedelta(seconds=275)
    assert s.current(token)[0].absolute_expires_at == NOW + timedelta(seconds=300)
    assert provider.refresh_calls == 0
    clock[0] += timedelta(seconds=26)
    with pytest.raises(BffUnauthenticated):
        s.current(token)


def test_ambiguous_claim_is_not_repeated_or_taken_over():
    s, token, clock, provider = renewal_env()
    claim = s.store.claim_refresh
    def ambiguous(*args):
        assert claim(*args)
        raise BffUnavailable("acknowledgement lost")
    s.store.claim_refresh = ambiguous
    clock[0] += timedelta(seconds=275)
    with pytest.raises(BffUnavailable):
        s.current(token)
    s.store.claim_refresh = claim
    with pytest.raises(BffUnavailable):
        s.current(token)
    assert provider.refresh_calls == 0


@pytest.mark.parametrize("revoke_unavailable", [False, True])
def test_ambiguous_commit_never_reuses_the_consumed_refresh(revoke_unavailable):
    s, token, clock, provider = renewal_env()
    finish = s.store.finish_refresh
    def ambiguous(*args):
        assert finish(*args)
        raise BffUnavailable("acknowledgement lost")
    s.store.finish_refresh = ambiguous
    if revoke_unavailable:
        def unavailable(*args):
            raise BffUnavailable("storage offline")
        s.store.revoke_session = unavailable
    clock[0] += timedelta(seconds=275)
    with pytest.raises(BffUnavailable):
        s.current(token)
    if revoke_unavailable:
        # A later read can observe the verified, atomically committed rotation.
        assert s.current(token)[0].refresh_version == 1
    else:
        with pytest.raises(BffUnauthenticated):
            s.current(token)
    assert provider.refresh_calls == 1


def test_invalid_grant_with_failed_revocation_remains_locked():
    s, token, clock, provider = renewal_env()
    provider.error = BffUnauthenticated("revoked")
    def unavailable(*args):
        raise BffUnavailable("storage offline")
    s.store.revoke_session = unavailable
    clock[0] += timedelta(seconds=275)
    with pytest.raises(BffUnavailable):
        s.current(token)
    with pytest.raises(BffUnavailable):
        s.current(token)
    assert provider.refresh_calls == 1
