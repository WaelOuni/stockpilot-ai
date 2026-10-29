"""Short elapsed-time diagnostic using an explicitly replayed expired cookie."""
import json
import time
from scripts.tests.b2b_test_ports import ORIGIN


def run_short_expiry(browser, auth_code, directory, passed):
    with browser() as client:
        assert client.get(auth_code(client)).status_code == 303
        cookie = client.cookies.get("__Host-stockpilot_bff")
        assert client.get(ORIGIN + "/b2b/auth/session").status_code == 200
        started = time.monotonic()
        time.sleep(32)
        replay = client.get(ORIGIN + "/b2b/auth/session",
            headers={"Cookie": "__Host-stockpilot_bff=" + cookie})
        missing = client.get(ORIGIN + "/b2b/auth/session", headers={"Cookie": ""})
        result = {"elapsed_seconds": round(time.monotonic()-started,3),
            "expired_cookie_replay_status": replay.status_code,
            "missing_cookie_status": missing.status_code,
            "expired_cookie_explicitly_sent": True, "clock_simulated": False}
        (directory / "short-expiry-report.json").write_text(json.dumps(result,indent=2))
        assert replay.status_code == 401 and missing.status_code == 401
        passed("actual 32-second expiry with explicit old-cookie replay and missing cookie both refused401")
