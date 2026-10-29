"""Real provider revocation, owned-store expiry injection; never a demo target."""
import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256

import httpx

from stockpilot.infrastructure.identity.token_protector import AesTokenProtector
from scripts.tests.b2b_test_ports import ORIGIN


def run_revocation(browser, auth_code, db, env, passed):
    with browser() as client:
        assert client.get(auth_code(client)).status_code == 303
        sid = sha256(client.cookies.get("__Host-stockpilot_bff").encode()).hexdigest()
        doc = db.bff_sessions.find_one({"_id": sid})
        binding = json.dumps([sid, doc["configuration"], doc["user_id"], doc["organization_id"],
            doc["external_identity"]["issuer"], doc["external_identity"]["subject"]], separators=(",", ":"))
        token, _ = AesTokenProtector(env["BFF_REFRESH_ENCRYPTION_KEY"]).open(doc["refresh_ciphertext"], binding)
        issuer = env["BFF_ISSUER"]
        metadata = httpx.get(issuer + "/.well-known/openid-configuration", trust_env=False).json()
        assert metadata["revocation_endpoint"].startswith(issuer + "/")
        response = httpx.post(metadata["revocation_endpoint"],
            auth=httpx.BasicAuth(env["BFF_CLIENT_ID"], env["BFF_CLIENT_SECRET"]),
            data={"token": token, "token_type_hint": "refresh_token"}, trust_env=False)
        del token
        assert response.status_code == 200
        db.bff_sessions.update_one({"_id": sid}, {"$set": {"provider_expires_at": datetime.now(UTC)+timedelta(seconds=1)}})
        assert client.get(ORIGIN + "/b2b/auth/session").status_code == 401
        assert db.bff_sessions.find_one({"_id": sid}) is None
        passed("real Keycloak revocation rejects refresh and removes the BFF session; no relogin")
    with browser() as client:
        assert client.get(auth_code(client)).status_code == 303
        sid = sha256(client.cookies.get("__Host-stockpilot_bff").encode()).hexdigest()
        db.bff_sessions.update_one({"_id": sid}, {"$set": {"idle_expires_at": datetime.now(UTC)-timedelta(seconds=1)}})
        assert client.get(ORIGIN + "/b2b/auth/session").status_code == 401
        passed("owned Mongo idle-expiry injection is refused before provider refresh")
