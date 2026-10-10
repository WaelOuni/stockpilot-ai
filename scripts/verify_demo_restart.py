"""Read-only data verification of owned demo restarts and real session expiry."""

import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import time

import httpx
import psycopg

from stockpilot.demo_runtime import (
    preflight_demo, resolve_demo_settings, demo_connection_environment,
)
from stockpilot.presentation.api.admin_auth import password_verifier


ROOT = Path(__file__).resolve().parents[1]
ORIGIN = "http://127.0.0.1:5175"


def main():
    settings = resolve_demo_settings()
    environment = os.environ.copy()
    uri = settings.langgraph_checkpoint_db_uri or settings.postgres_url
    username, password = "demo-restart-admin", secrets.token_urlsafe(36)
    environment.update({
        **demo_connection_environment(settings), "ADMIN_USERNAME": username,
        "ADMIN_PASSWORD_VERIFIER": password_verifier(password),
        "ADMIN_SESSION_TTL_SECONDS": "2", "STOCKPILOT_DEMO_NONINTERACTIVE": "true",
    })
    before = preflight_demo(settings)
    with psycopg.connect(uri) as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        checkpoints_before = connection.execute("SELECT count(*) FROM public.checkpoints").fetchone()[0]
    assert checkpoints_before > 0
    with httpx.Client(base_url=ORIGIN, headers={"Origin": ORIGIN}, timeout=10) as client:
        for restart in range(2):
            process = subprocess.Popen(["bash", "scripts/run-demo.sh"], cwd=ROOT, env=environment,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                for _ in range(120):
                    if process.poll() is not None:
                        raise RuntimeError("Guarded restart failed.")
                    try:
                        if client.get("/api/health").status_code == 200:
                            break
                    except httpx.ConnectError:
                        pass
                    time.sleep(.25)
                else:
                    raise RuntimeError("Guarded restart readiness timed out.")
                # The prior process's cookie must not authenticate on its successor.
                assert client.get("/api/backoffice/dashboard").status_code == 401
                response = client.post("/api/backoffice/session/login",
                                       headers={"X-Admin-CSRF": "login"},
                                       json={"username": username, "password": password})
                assert response.status_code == 200
                csrf = response.json()["csrf_token"]
                assert client.get("/api/backoffice/dashboard").status_code == 200
                time.sleep(2.2)
                assert client.get("/api/backoffice/dashboard").status_code == 401
                relogin = client.post("/api/backoffice/session/login",
                                     headers={"X-Admin-CSRF": "login"},
                                     json={"username": username, "password": password})
                assert relogin.status_code == 200
                assert relogin.json()["csrf_token"] != csrf
            finally:
                process.send_signal(signal.SIGINT)
                process.wait(timeout=30)
                for port in (8003, 5175):
                    with socket.socket() as probe:
                        assert probe.connect_ex(("127.0.0.1", port)) != 0
    after = preflight_demo(settings)
    assert before.mongo_summary == after.mongo_summary
    with psycopg.connect(uri) as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        checkpoints_after = connection.execute("SELECT count(*) FROM public.checkpoints").fetchone()[0]
    assert checkpoints_before == checkpoints_after
    report = {"owned_restarts": 2, "session_expiry_relogin": "passed",
              "old_cookie_rejected": True, "checkpoint_rows_preserved": checkpoints_after,
              "mongo_summary_preserved": True, "rag_rows": after.rag_document_count,
              "owned_servers_stopped": True}
    (ROOT / "data" / "demo-restart-report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Demo restart verification failed ({type(error).__name__}); details withheld.")
        raise SystemExit(1)
