"""Actual elapsed-time Keycloak renewal acceptance on the parent's owned services."""
import json
import time
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from datetime import datetime, UTC, timedelta

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
        absolute = document["absolute_expires_at"].replace(tzinfo=UTC)
        started = time.monotonic()
        offsets = sorted(set([*range(0, 1800, 60), 275, 1790, 1802]))
        for offset in offsets:
            # Schedule against the same UTC deadline used by the BFF. Measure
            # actual duration independently; never modify either clock/deadline.
            target = absolute + timedelta(seconds=offset - 1800)
            while datetime.now(UTC) < target:
                assert time.monotonic()-started < 1860, "UTC schedule exceeded bounded monotonic wait"
                time.sleep(min(0.5, max(0, (target-datetime.now(UTC)).total_seconds())))
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
            observation = {"elapsed_seconds": round(time.monotonic()-started,3),
                "status": response.status_code,
                "request_id": response.headers.get("x-request-id"),
                "observed_utc": datetime.now(UTC).isoformat(),
                "session_cookie_sent": "__Host-stockpilot_bff=" in response.request.headers.get("cookie", "")}
            progress.write_text(json.dumps({"state": "observed", "samples": samples,
                "last_observation": observation}, indent=2))
            if offset >= 1800:
                replay = client.get(ORIGIN + "/b2b/auth/session",
                    headers={"Cookie": "__Host-stockpilot_bff=" + cookie})
                observation["explicit_expired_cookie_replay_status"] = replay.status_code
            progress.write_text(json.dumps({"state": "observed", "samples": samples,
                "last_observation": observation}, indent=2))
            if response.status_code != expected:
                progress.write_text(json.dumps({"state": "failed", "samples": samples,
                    "failed_elapsed_seconds": round(time.monotonic()-started,3),
                    "expected_status": expected, "observed_status": response.status_code, "last_observation": observation},indent=2))
            assert response.status_code == expected
            if offset >= 1800:
                assert replay.status_code == 401
            if expected == 200:
                assert datetime.fromisoformat(response.json()["expires_at"]).timestamp() == absolute.timestamp()
            record = db.bff_sessions.find_one({"_id": identity})
            samples.append(observation | {"rotation_version": record.get("refresh_version") if record else None})
            progress.write_text(json.dumps({"state": "running" if offset < 1800 else "complete", "samples": samples}, indent=2))
        assert max(s["rotation_version"] or 0 for s in samples) >= 5
    report["renewal_timed"] = {"actual_elapsed_seconds": samples[-1]["elapsed_seconds"],
        "samples": samples, "absolute_unchanged": True, "clock_simulated": False,
        "absolute_expires_at": absolute.isoformat(), "schedule_clock": "UTC",
        "elapsed_clock": "monotonic"}
    passed("real activity through fixed 30-minute UTC policy; elapsed measured independently; absolute expiry refused")
