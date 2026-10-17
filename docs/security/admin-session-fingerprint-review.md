# Targeted residual review: operator session fingerprint

Reviewed baseline: `b503af9b2fa3c746e8be05eae562a39b66d3ddbe`, after V13.2 and V13.C1.
This review does not change authentication, persisted records or deployments.

## Evidence and impact

`presentation/api/admin_auth.py:108-144` validates the operator configuration and
computes SHA-256 over its canonical JSON tuple. The tuple contains the username,
full salted PBKDF2 verifier, trusted origin, HTTP/environment options, raw
backoffice and customer JWT secrets, TTL, capacity and store backend.
`infrastructure/persistence/mongodb/admin_sessions.py:114-117` stores that digest
as `operator_sessions.configuration`, alongside the hashed opaque session ID.

This outer hash is not a direct unsalted administrator password hash. The
password verifier uses PBKDF2-SHA256, 600,000 iterations and at least 128 bits of
random salt. Mongo session records do not contain that verifier or the other
secrets. A database-only practical password/key recovery attack was not proven.
The candidate generator creates independent random 256-bit JWT/backoffice keys
and a 192-bit random operator password; no deployed value was inspected for this
review. This is not a demonstrated candidate integrity/authentication incident.

A synthetic offline demonstration did prove a conditional oracle: when every
other tuple element is already known, guesses for a weak backoffice secret can
be checked with one fast SHA-256 each. No PBKDF2 is required per token guess.
The general backoffice contract accepts a nonempty secret without an entropy
requirement; JWT requires 32 bytes, which is a length check, not entropy proof.
This justifies a future defense against partial-configuration disclosure, rather
than a claim that an ordinary Mongo dump alone makes random keys crackable.

## Existing rotation contract

Each Mongo read filters by fingerprint and explicit expiry. A configuration
change denies sessions under the new configuration without deleting old records.
Restoring the exact former configuration can restore still-live sessions. Logout
permanently deletes its token record. All workers need identical configuration;
mixed-configuration rollouts are already unsupported. Session capacity counts
all live records across configurations, so old records can delay new logins
until TTL expiry (default 30 minutes, configured maximum 24 hours). The TTL index
does not replace explicit expiry checks.

## Proposed future change requiring human validation

Use a versioned, domain-separated HMAC-SHA256 fingerprint with an independent
random key of at least 32 bytes, durably provisioned outside Mongo and shared by
all workers. Do not derive it from JWT/backoffice credentials, store it beside
the digest, or generate a new per-worker key. Keep PBKDF2 verification, session
ID hashing, cookies, CSRF and HTTP responses unchanged.

An explicit rollout must provision the key, coordinate workers and require
operator reauthentication. Accepting the old SHA fingerprint indefinitely would
retain the oracle being removed. Legacy records can remain protected until TTL
expiry; do not migrate/delete deployment records automatically. Document key
rotation, recovery, restoration of old keys and the capacity implications. A
length check alone cannot guarantee key entropy; provide a secure generator.

This adds an authentication configuration requirement and affects existing
sessions. Any implementation PR must remain open for human approval under the
delivery-program compatibility rule. No change is activated in this phase.

Required tests: stable fingerprints across workers/restart with identical key,
key/configuration rotation, rejection of legacy fingerprints in the new mode,
missing/short/reused keys, generic errors, no secret material at rest or in
diagnostics, preserved cookie/CSRF behavior and live Mongo cross-instance
lookup/revocation/capacity. Test the intended rollback policy explicitly.

Offline regression evidence in the reviewed baseline: **52 PASS** in 6.56 s for
`test_admin_sessions.py` and `test_admin_session_store_failures.py`. No deployment,
network attack, real credential or real session was used by this review.

References: [Python HMAC](https://docs.python.org/3/library/hmac.html) defines the
keyed construction; [OWASP password storage](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html)
describes separate secret/pepper storage and its lifecycle. Neither reference
turns this configuration digest into a substitute for a password verifier.
