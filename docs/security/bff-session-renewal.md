# Server-side BFF renewal

Renewal is **disabled by default**. Existing ID-token-capped sessions and their
configuration fingerprint stay unchanged. No VM, realm, credential or deployment
is changed by this implementation.

## Decision and guarantees

Longer ID tokens would expand the interval in which a Keycloak-only revocation
is unknown. Instead, the opt-in BFF retains an encrypted refresh token and renews
the signed ID token on authenticated activity within 30 seconds of its expiry
(or before serving a request arriving after that expiry). There is no background
keepalive, automatic login, offline access or business-operation retry.

The access token is discarded; it never becomes application identity. Keycloak
must return a signed ID token at renewal. Issuer, subject and normalized audience
must match the original authentication. Signature, algorithm, audience/client,
authorized party and dates are verified. A returned nonce/auth_time must match
the original values; nonce omission at renewal is permitted by OIDC Core12.2.

The BFF absolute deadline is creation plus the configured duration (default
1800s); it is never advanced by renewal. Inactivity stays at 600s by default and
is checked before claiming and committing. A session without renewal fields
retains its original deadline. Activation uses a new configuration fingerprint,
so existing browser sessions must authenticate again.

## Storage and concurrency

`BFF_REFRESH_ENABLED=true` requires `BFF_REFRESH_ENCRYPTION_KEY`, a dedicated
base64-encoded 32-byte random key supplied through the existing private server
configuration. Never reuse the OIDC client secret/configuration key. Never put
the key in Vue/Vite variables, Git, a report or logs. Key generation and installation
belong to a separately authorized deployment procedure.

AES-256-GCM uses a fresh random 96-bit IV and authenticates the session hash,
configuration, user, organization, issuer and subject as associated data. The
refresh token, original nonce/auth_time/audience are encrypted together. Mongo
stores only ciphertext, provider expiry, rotation version and a transient owner.
Mongo writers and host administrators remain trusted; this is protection for
credentials at rest, not protection against an attacker controlling the server.

A conditional Mongo update assigns one owner for a version. Completion checks
owner, version, configuration and current absolute/idle dates; it never upserts
or changes absolute expiry. Concurrent requests may observe the committed result,
or return503 after a one-second monotonic waiting budget plus bounded Mongo calls.
An abandoned claim is never taken over: a rotating token may already be consumed.
The user must authenticate explicitly; TTL eventually removes abandoned sessions.
Do not clear the owner field administratively to force a retry.

Invalid grants/claims and ambiguous provider failures revoke the local session.
If storage is unavailable, the request fails closed. A later majority read may
observe an already verified/committed rotation; it must not repeat the consumed
grant. Logout checks cookie and CSRF then deletes the session **without calling
refresh**. A refresh in flight cannot recreate it. Application memberships and
permissions are revalidated before business access.

Keycloak-only revocation is detected at the next renewal, not immediately. The
cached verified identity is usable until the next activity requiring renewal.
Immediate provider revocation would require an additional agreed back-channel
or introspection policy. No such guarantee is claimed here.

## Schema, activation and rollback

The existing BFF session collection has no strict field validator. Optional
renewal fields need no index, DDL or business-schema migration. Existing migration
files/checksums are unchanged. No startup migration is added.

Before activation: preserve the current configuration/image, verify key storage
and permissions, and test the target realm's effective ID-token, refresh rotation,
SSO/client idle and maximum policies on a disposable copy. Use the same key and
settings on every worker. Enabling/disabling renewal or rotating the key changes
the fingerprint and invalidates sessions/login transactions. Plan explicit login
and resolve any uncertain business operation by its original reference.

For rollback: stop new admission in an authorized window, drain or stop all BFF
workers so no admitted callback can insert a session after the purge, revoke BFF
sessions and login transactions in the verified BFF target (never business data), then restore
the previous image/configuration. Do not rely only on reverting a fingerprint:
old rows under the previous fingerprint could still exist. No Mongo volume reset,
business backup restore or schema downgrade is required for these optional fields.

## Qualification

Use `docs/testing.md` to provision and verify disposable integration services.
`tests/test_bff_renewal.py` uses a simulated clock; Mongo integration tests exercise
claims and completion across adapter instances. They do not prove elapsed time.

From a built frontend worktree with its own locked Python environment:

```sh
B2B_TEST_PORT_SET=default PYTHONDONTWRITEBYTECODE=1 \
  .venv/bin/python scripts/tests/validate_bff_keycloak.py --renewal-timed
```

This owned tmpfs harness turns on renewal with a generated test key and enables
strict refresh rotation only in its ephemeral realm. It exercises real SSO/Vue
continuity, real refresh-token revocation, injected owned-store idle expiry,
concurrent requests, activity over five minutes through the 30-minute deadline,
and provider outage. The elapsed-time trace records statuses, rotation versions, UTC timestamps, request
IDs and cookie-presence booleans, never cookie values. The final request explicitly
replays the expired opaque cookie as well as testing the normal cookie jar.
`--renewal` without `--renewal-timed` instead includes an actual 32-second expiry
check under a disposable 30-second BFF policy; this is not the 30-minute qualification.
The idle injection is not a measured ten-minute idle wait. No inference is used.
Run integration regressions separately with all three explicit CI endpoint variables;
the standard BFF harness does not provision the authentication-only Mongo fixture.

References: [OIDC Core12.2](https://openid.net/specs/openid-connect-core-1_0.html#RefreshTokenResponse),
[Keycloak sessions and tokens](https://www.keycloak.org/docs/latest/server_admin/index.html#_timeouts).
