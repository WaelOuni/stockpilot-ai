"""Actual elapsed-time Keycloak renewal acceptance on the parent's owned services."""
import json
import time
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from datetime import datetime, UTC

from scripts.tests.b2b_test_ports import ORIGIN


def run_timed(browser, auth_code, db, directory, passed, report):
    samples = []
    progress = directory / "renewal-timed-progress.json"
    with browser() as client:
        assert client.get(auth_code(client)).status_code == 303
        cookie = client.cookies.get("__Host-stockpilot_bff")
        identity = sha256(cookie.encode()).hexdigest()
        document = db.bff_sessions.find_one({"_id": identity})
        assert document["refresh_ciphertext"] and document["provider_expires_at"] < document["absolute_expires_at"]
        absolute = document["absolute_expires_at"]
        started = time.monotonic()
        offsets = sorted(set([*range(0, 1800, 60), 275, 1790, 1802]))
        for offset in offsets:
            while time.monotonic() - started < offset:
                time.sleep(max(0, min(1, offset - (time.monotonic() - started))))
            if offset == 275:
                version = db.bff_sessions.find_one({"_id": identity})["refresh_version"]
                with ThreadPoolExecutor(max_workers=4) as pool:
                    responses = list(pool.map(lambda _: client.get(ORIGIN + "/b2b/auth/session"), range(4)))
                assert all(r.status_code == 200 for r in responses)
                assert db.bff_sessions.find_one({"_id": identity})["refresh_version"] == version + 1
                passed("real concurrent requests share exactly one durable rotating refresh")
                response = responses[0]
            else:
                response = client.get(ORIGIN + "/b2b/auth/session")
            expected = 200 if offset < 1800 else 401
            assert response.status_code == expected
            if expected == 200:
                assert datetime.fromisoformat(response.json()["expires_at"]).timestamp() == absolute.replace(tzinfo=UTC).timestamp()
            record = db.bff_sessions.find_one({"_id": identity})
            samples.append({"elapsed_seconds": round(time.monotonic() - started, 3),
                "status": response.status_code, "rotation_version": record.get("refresh_version") if record else None})
            progress.write_text(json.dumps({"state": "running" if offset < 1800 else "complete", "samples": samples}, indent=2))
        assert max(s["rotation_version"] or 0 for s in samples) >= 5
    report["renewal_timed"] = {"actual_elapsed_seconds": samples[-1]["elapsed_seconds"],
        "samples": samples, "absolute_unchanged": True, "clock_simulated": False}
    passed("actual elapsed 30-minute active session with rotating Keycloak refresh; absolute expiry refused")
