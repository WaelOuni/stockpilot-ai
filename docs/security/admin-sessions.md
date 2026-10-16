# Durable local operator sessions

Status: V13.2 bounded design and implementation. This extends the existing
local operator login; it does not define SaaS organizations, identities or roles.
SaaS activation remains blocked until the authentication and tenant design is
implemented and the external identity provider is selected.

## Existing contract and evidence

`presentation/api/admin_auth.py` validates a configured PBKDF2 verifier, rotates
opaque random cookies on login, uses fixed expiry, and checks the exact Origin
and a session-bound CSRF token on mutations. Secure HttpOnly SameSite=Strict
`__Host-` cookies and sanitized authentication errors already exist. There are no
default credentials. The current dictionary, monotonic clock and login-attempt
queues belong to one process: restart drops sessions, workers cannot share login
or logout, and throttling is multiplied by the number of workers.

## Minimal change

Keep the HTTP contract and password verification unchanged. Add a session-store
port with the existing in-memory implementation for explicit development/test
use and a MongoDB implementation using the already configured database. Select
`ADMIN_SESSION_STORE=memory|mongo`; memory is refused by admin authentication
outside development. A failed or unavailable persistent store returns a generic
503 and never falls back to memory.

MongoDB stores only SHA-256 hashes of random 256-bit session identifiers. Session
records include username, CSRF token, a configuration fingerprint and UTC expiry.
The fingerprint binds sessions to the configured verifier, origin and security
settings without storing their values. Each request explicitly rejects expiry;
the Mongo TTL index only removes expired records eventually. Recreating a worker
does not revoke valid sessions. All workers must use identical configuration;
mixed-configuration rolling deployments are unsupported. A fingerprint mismatch
denies access under the current configuration; it is not permanent revocation.
Restoring exactly an earlier configuration can restore its still-live sessions.
Before restoring old admin configuration, revoke the fleet's sessions explicitly
as a separate authorized operation. Logout permanently deletes its token record
and configuration rollback cannot restore it. All Mongo operations have a
five-second client deadline and majority durability; an uncertain login result
sets no cookie, and any unobserved session expires normally.

Login rotation and capacity admission use a Mongo replica-set transaction and a
shared guard document so concurrent workers cannot over-admit sessions or leave
both old and new tokens valid. Logout deletes the shared record. Login attempts
use a separate bounded shared guard: at most 50 global and 5 per observed address
in a sliding 60-second window, preserving the existing policy. Observed addresses
are hashed, forwarded headers are never read directly, and a trusted proxy must
be configured outside the application. A proxy presenting one shared address can
reduce availability; this is a conservative local operator control, not SaaS
tenant quotas. Distributed throttles do not reset on successful authentication.

Mongo operations remain synchronous in FastAPI's synchronous dependencies and
handlers. No ORDER, catalogue, checkpoint, prompt or model behavior changes.

## Provisioning and rollback

Selecting Mongo explicitly creates two dedicated security collections and an
expiry index when the application starts. No existing document, checkpoint or
DEMO deployment is migrated. Use the existing Mongo replica set with restricted
database credentials. Backups contain CSRF values and username metadata and must
be protected; raw session cookies and password verifiers are absent from records.
Rolling back to memory revokes access to persistent sessions and requires login;
do not remove the collections until their retention period has elapsed. Never
automatically switch stores during an outage.

## Validation and limitations

Retain all existing session/CSRF/cookie regressions. Add real Mongo tests using
the V13.1 dedicated-target guard and fresh UUID databases: cross-instance reads,
restart, logout, rotation, explicit expiry, configuration changes, shared login
throttling, concurrent capacity and concurrent rotation. Test storage errors
without exposing connection details.

Import preview signatures currently use a per-process random key in
`application/use_cases/product_csv_import.py`. Durable login does not make an
import preview transferable between workers or across restart. Keep import
requests on one worker, or close multi-worker import until a separate shared
preview design is implemented. This PR does not change import behavior.

Validation on the dedicated V13.2 test services: all 46 original admin session
regressions remain unchanged and pass; 10 new real-Mongo scenarios pass; all 189
required integration cases pass; all 88 deterministic evaluations pass. Full
unit/frontend results are recorded in the V13.2 report.

Cookie theft permits reads until expiry or revocation; CSRF prevents cross-site
mutations, not XSS or replay of a stolen cookie and CSRF token. Logout cannot
cancel a mutation that was already authorized and started. SaaS administrator
permissions, MFA, recovery, invitations and audited organizational revocation
remain product/deployment decisions. This change is not production readiness.
