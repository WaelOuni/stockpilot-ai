"""Real Mongo/PG/Keycloak restore and post-recovery browser/API proof."""

import base64
import hashlib
import json
import secrets
import shutil
import socket
import subprocess
import time
from time import perf_counter

import httpx
import psycopg
from cryptography import x509
from cryptography.hazmat.primitives import serialization

from scripts.tests.canonical_backup import (
    attest,
    paired_backup,
    restore,
)
from scripts.tests.canonical_demo import ISSUER, CanonicalDemo
from stockpilot.infrastructure.persistence.mongodb.schema_migrations import (
    MigrationRunner,
)
from stockpilot.infrastructure.persistence.postgres.tenant_migrations import (
    PostgresTenantMigrations,
)


def wait(owner, services):
    for _ in range(100):
        rows = attest(owner)
        if all(
            rows[s]["State"].get("Health", {}).get("Status") == "healthy"
            for s in services
        ):
            return
        time.sleep(0.3)
    raise RuntimeError("Restore database readiness deadline.")


def run_recovery(
    owner, db, env, directory, cert, key, browser, auth_code, passed, report
):
    from integration_targets import mongo_test_client

    started = perf_counter()

    with browser() as client:
        assert client.get(auth_code(client)).status_code == 303
        old_cookie = client.cookies.get("__Host-stockpilot_bff")
        s = client.get(env["BFF_ORIGIN"] + "/b2b/auth/session").json()
        headers = {"Origin": env["BFF_ORIGIN"], "X-BFF-CSRF": s["csrf_token"]}
        base = env["BFF_ORIGIN"] + "/b2b/api/assistant/conversations"
        result = client.post(base, headers=headers, json={"thread_id": "restore-proof"})
        assert result.status_code == 200
        conversation = result.json()["conversation_id"]
        base += "/" + conversation
        prepared = client.post(
            base + "/messages",
            headers=headers,
            timeout=90,
            json={
                "message_id": "restore-prepare",
                "message": "Je veux commander 1 unité du SKU DRILL-001",
                "customer_id": "fixture-customer",
            },
        )
        assert prepared.status_code == 200
        order = prepared.json()["reply"]["order"]
        confirm = {
            "operation_id": "restore-confirm",
            "order_id": order["order_id"],
            "preparation_digest": order["preparation_digest"],
            "confirmed": True,
        }
        # A real PostgreSQL write failure AFTER the actual Mongo commit. Reads
        # continue to work; only checkpoint INSERT/UPDATE is made to fail.
        with psycopg.connect(env["BFF_ASSISTANT_POSTGRES_URI"]) as c:
            c.execute(
                "CREATE FUNCTION stockpilot_b2b.validation_checkpoint_fault() RETURNS trigger "
                "LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'owned checkpoint fault'; END $$"
            )
            c.execute(
                "CREATE TRIGGER validation_checkpoint_fault BEFORE INSERT OR UPDATE "
                "ON stockpilot_b2b.checkpoints FOR EACH ROW "
                "EXECUTE FUNCTION stockpilot_b2b.validation_checkpoint_fault()"
            )
        try:
            outcome = client.post(
                base + "/confirm", headers=headers, json=confirm, timeout=90
            )
            assert outcome.status_code == 503
            product = db.b2b_products.find_one(
                {"organization_id": "fixture-org", "sku": "DRILL-001"}
            )
            assert product["reserved"] == 1
            assert (
                db.b2b_order_operations.count_documents(
                    {
                        "organization_id": "fixture-org",
                        "namespace": "confirm",
                        "operation_id": "restore-confirm",
                    }
                )
                == 1
            )
        finally:
            with psycopg.connect(env["BFF_ASSISTANT_POSTGRES_URI"]) as c:
                c.execute(
                    "DROP TRIGGER validation_checkpoint_fault ON stockpilot_b2b.checkpoints"
                )
                c.execute("DROP FUNCTION stockpilot_b2b.validation_checkpoint_fault()")
    passed(
        "actual Mongo reservation survives PostgreSQL checkpoint failure; original action remains pending"
    )
    # Regression: an unavailable BFF must not expose callback codes/state in
    # inherited Nginx error logging. Synthetic markers, never genuine OIDC codes.
    owner.stop()
    marker = "SYNTHETIC-CALLBACK-DO-NOT-LOG"
    response = httpx.get(
        env["BFF_ORIGIN"] + "/b2b/auth/callback?code=" + marker + "&state=" + marker,
        verify=str(cert),
        trust_env=False,
        timeout=5,
    )
    assert response.status_code in {502, 504}
    proxy_id = attest(owner)["proxy"]["Id"]
    captured = subprocess.run(
        ["docker", "logs", proxy_id], capture_output=True, text=True, check=True
    )
    assert marker not in captured.stdout + captured.stderr
    passed("proxy upstream outage never logs synthetic OIDC callback code/state")
    # Close ingress first, then graceful BFF drain, then the only other app writer.
    attest(owner)
    for service in ("proxy", "bff", "keycloak"):
        owner.command(
            [*owner.compose, "--profile", "runtime", "stop", "-t", "100", service],
            owner.env,
        )
    manifest = paired_backup(
        owner, db, directory / "backup", expected_pending=["restore-confirm"]
    )
    backup_seconds = round(perf_counter() - started, 2)
    restore_started = perf_counter()
    passed(
        "quiescent paired mongodump/pg_dump/Keycloak dump with stable logical before/after"
    )
    clone = None
    client = None
    restored = directory / "restore"
    restored.mkdir(mode=0o700)
    for name in ("tls.crt", "tls.key"):
        shutil.copyfile(directory / name, restored / name)
    clone_env = dict(owner.env)
    clone_env.update(
        BFF_TEST_PROJECT=owner.env["BFF_TEST_PROJECT"] + "-restore",
        SP_DEMO_MONGO_PORT="57926",
        SP_DEMO_POSTGRES_PORT="57928",
    )
    for port in (57926, 57928):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port))
    compose = owner.compose[:-1] + [clone_env["BFF_TEST_PROJECT"]]
    try:
        clone = CanonicalDemo(
            restored,
            clone_env,
            compose,
            owner.command,
            restored / "tls.crt",
            restored / "tls.key",
            build=False,
        )
        owner.command(
            [*compose, "up", "-d", "mongo", "test-postgres", "keycloak-db"], clone_env
        )
        wait(clone, ("mongo", "test-postgres", "keycloak-db"))
        client = mongo_test_client(
            environ={
                "MONGO_TEST_URI": "mongodb://127.0.0.1:57926/?replicaSet=rs0&directConnection=true"
            }
        )
        target = client[db.name]
        report["recovery"] = restore(clone, target, directory / "backup", manifest)
        # Validate migrated data/schema/indexes without executing any migration.
        MigrationRunner(target).require_version(3)
        pg_uri = (
            "postgresql://ci:"
            + env["BFF_TEST_DB_PASSWORD"]
            + "@127.0.0.1:57928/stockpilot_ci"
        )
        assert len(PostgresTenantMigrations(pg_uri).status()) == 2
        passed(
            "independent empty restore preserves every Mongo collection/index/options and PG/Keycloak row"
        )
        # Recreate only the restored issuer at the original logical HTTPS URL.
        owner.command([*compose, "up", "-d", "--no-deps", "keycloak"], clone_env)
        clone.start_proxy()
        context = __import__("ssl").create_default_context(cafile=str(cert))
        for _ in range(120):
            try:
                if (
                    httpx.get(
                        ISSUER + "/.well-known/openid-configuration",
                        verify=context,
                        trust_env=False,
                        timeout=2,
                    ).status_code
                    == 200
                ):
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        else:
            raise RuntimeError("Restored Keycloak discovery deadline.")
        clone_bff_env = env | {"BFF_CONFIGURATION_KEY": secrets.token_urlsafe(36)}
        clone.start(clone_bff_env)
        report["recovery"]["stage"] = "old-cookie-rejection"
        with browser() as recovered:
            recovered.cookies.set(
                "__Host-stockpilot_bff", old_cookie, domain="127.0.0.1", path="/"
            )
            assert (
                recovered.get(env["BFF_ORIGIN"] + "/b2b/auth/session").status_code
                == 401
            )
            recovered.cookies.clear()
            report["recovery"]["stage"] = "restored-keycloak-login"
            callback_response = recovered.get(auth_code(recovered))
            report["recovery"]["callback_status"] = callback_response.status_code
            assert callback_response.status_code == 303
            session = recovered.get(env["BFF_ORIGIN"] + "/b2b/auth/session").json()
            h = {"Origin": env["BFF_ORIGIN"], "X-BFF-CSRF": session["csrf_token"]}
            report["recovery"]["stage"] = "reconcile-pending"
            result = recovered.post(base + "/resume", headers=h, json={})
            report["recovery"]["resume_status"] = result.status_code
            assert result.status_code == 200
            assert result.json()["items"][-1]["reply"]["order"]["status"] == "reserved"
            assert (
                target.b2b_products.find_one({"organization_id": "fixture-org"})[
                    "reserved"
                ]
                == 1
            )
            report["recovery"]["stage"] = "replay-confirm"
            # Same action/key even after restart/restore; no compensation key.
            assert (
                recovered.post(base + "/confirm", headers=h, json=confirm).status_code
                == 200
            )
            assert (
                target.b2b_order_operations.count_documents(
                    {
                        "organization_id": "fixture-org",
                        "namespace": "confirm",
                        "operation_id": "restore-confirm",
                    }
                )
                == 1
            )
            report["recovery"]["stage"] = "cancel-twice"
            cancel = {
                "operation_id": "restore-cancel",
                "order_id": order["order_id"],
                "confirmed": True,
            }
            for _ in range(2):
                assert (
                    recovered.post(base + "/cancel", headers=h, json=cancel).status_code
                    == 200
                )
            assert (
                target.b2b_products.find_one({"organization_id": "fixture-org"})[
                    "reserved"
                ]
                == 0
            )
            assert (
                db.b2b_products.find_one({"organization_id": "fixture-org"})["reserved"]
                == 1
            )
        passed(
            "restored Keycloak credentials/subjects login; old BFF cookie refused after key rotation"
        )
        passed(
            "restored pending action reconciles same Mongo key; replay reserves once; cancellation releases once"
        )
        report["recovery"]["stage"] = "restart-services"
        # Each restored database service restarts on its own dedicated test volume.
        before = target.b2b_orders.count_documents({})
        for name in ("mongo", "test-postgres", "keycloak"):
            attest(clone)
            owner.command([*compose, "restart", "-t", "20", name], clone_env)
            time.sleep(2)
            wait(clone, ("mongo", "test-postgres"))
        assert target.b2b_orders.count_documents({}) == before
        for _ in range(120):
            try:
                if (
                    httpx.get(
                        ISSUER + "/.well-known/openid-configuration",
                        verify=context,
                        trust_env=False,
                        timeout=2,
                    ).status_code
                    == 200
                ):
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        else:
            raise RuntimeError("Restarted Keycloak readiness deadline.")
        clone.stop()
        clone.start(clone_bff_env)
        with browser() as recovered:
            assert recovered.get(auth_code(recovered)).status_code == 303
            s = recovered.get(env["BFF_ORIGIN"] + "/b2b/auth/session").json()
            h = {"Origin": env["BFF_ORIGIN"], "X-BFF-CSRF": s["csrf_token"]}
            assert recovered.get(base, headers=h).status_code == 200
        passed(
            "restored Mongo/PostgreSQL/Keycloak/BFF restarts retain data and private conversation"
        )
        report["recovery"]["stage"] = "restored-chromium"
        certificate = x509.load_pem_x509_certificate(cert.read_bytes())
        spki = certificate.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        browser_env = env | {
            "NODE_EXTRA_CA_CERTS": str(cert),
            "B2B_BROWSER_SPKI": base64.b64encode(
                hashlib.sha256(spki).digest()
            ).decode(),
            "B2B_RECOVERY_CONVERSATION": conversation,
            "B2B_RECOVERY_ORDER": order["order_id"],
            "B2B_BROWSER_REPORT": str(restored / "browser-report.json"),
        }
        with (restored / "browser-private.log").open("w") as output:
            result = subprocess.run(
                [shutil.which("node"), "b2b-recovery-browser.mjs"],
                cwd=directory.parents[1] / "frontend",
                env=browser_env,
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=240,
                check=False,
            )
        recovery_browser = json.loads((restored / "browser-report.json").read_text())
        if result.returncode or not recovery_browser["passed"]:
            raise RuntimeError("Restored real browser campaign incomplete.")
        report["recovery"]["browser"] = recovery_browser
        for check in recovery_browser["checks"]:
            passed(check)
        report["recovery"]["resource_sample"] = clone.stats()
        report["recovery"]["pending_reconciled"] = True
        report["recovery"]["backup_campaign_seconds"] = backup_seconds
        report["recovery"]["restore_validation_seconds"] = round(
            perf_counter() - restore_started, 2
        )
        report["recovery"]["source_retained_reserved_target_cancelled"] = True
    finally:
        if client:
            client.close()
        if clone:
            records = attest(clone)
            if "bff" in records:
                diagnostic = subprocess.run(
                    ["docker", "logs", records["bff"]["Id"]],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
                (restored / "bff-private.log").write_text(
                    diagnostic.stdout + diagnostic.stderr
                )
                (restored / "bff-private.log").chmod(0o600)
            attest(clone)
            owner.command(
                [*compose, "--profile", "runtime", "down", "--timeout", "100"],
                clone_env,
            )
            clone.cleanup_volumes()
    # The original owned project resumes with its untouched source data.
    owner.command([*owner.compose, "up", "-d", "--no-deps", "keycloak"], owner.env)
    owner.start(env)
    for _ in range(120):
        try:
            if (
                httpx.get(
                    ISSUER + "/.well-known/openid-configuration",
                    verify=str(cert),
                    trust_env=False,
                    timeout=2,
                ).status_code
                == 200
            ):
                break
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    else:
        raise RuntimeError("Source Keycloak readiness deadline.")
