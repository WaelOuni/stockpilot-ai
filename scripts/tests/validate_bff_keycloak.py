"""Real HTTP OIDC campaign on uniquely owned tmpfs-only Docker resources.

Run from repository root: .venv/bin/python scripts/tests/validate_bff_keycloak.py
No protected database/service is used. Report contains outcomes, never credentials.
"""

import argparse
import ipaddress
import json
import os
import re
import secrets
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import traceback
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
COMPOSE = ROOT / "docker/compose.bff-test.yml"
from scripts.tests.b2b_test_ports import APP, MONGO, OIDC, ORIGIN, POSTGRES, preset

ISSUER = f"http://127.0.0.1:{OIDC}/realms/stockpilot-dev"
COOKIE = "__Host-stockpilot_bff"


def command(args, env=None):
    result = subprocess.run(
        args,
        check=False,
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=900 if args[1:3] == ["-m", "pytest"] else 300,
    )
    if result.returncode:
        private = ROOT / "tmp" / "owned-command-private.log"
        private.parent.mkdir(exist_ok=True)
        private.write_text(result.stdout + result.stderr)
        private.chmod(0o600)
        # Do not echo a command environment or provider/container logs.
        raise RuntimeError("Owned test command failed: " + Path(args[0]).name)
    return result.stdout


def docker_snapshot():
    ids = command(["docker", "ps", "-aq"]).split()
    records = {}
    for item in ids:
        record = json.loads(
            command(
                [
                    "docker",
                    "inspect",
                    "--format",
                    (
                        '{"id":{{json .Id}},"name":{{json .Name}},"state":{{json .State.Status}},'
                        '"started":{{json .State.StartedAt}},"restarts":{{json .RestartCount}},'
                        '"image":{{json .Image}}}'
                    ),
                    item,
                ]
            )
        )
        records[item] = record
    return records


class LoginForm(HTMLParser):
    def __init__(self):
        super().__init__()
        self.action = None
        self.hidden = {}
        self.inside = False

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if tag == "form" and attrs.get("id") == "kc-form-login":
            self.action, self.inside = attrs.get("action"), True
        if (
            tag == "input"
            and self.inside
            and attrs.get("type") == "hidden"
            and attrs.get("name")
        ):
            self.hidden[attrs["name"]] = attrs.get("value", "")

    def handle_endtag(self, tag):
        if tag == "form":
            self.inside = False


def make_certificate(directory):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "StockPilot isolated BFF test")]
    )
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                    x509.DNSName("localhost"),
                    x509.DNSName("auth.stockpilot.test"),
                ]
            ),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = directory / "tls.crt", directory / "tls.key"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    key_path.chmod(0o600)
    return cert_path, key_path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--regressions",
        action="store_true",
        help="Run guarded integration suite before cleanup",
    )
    parser.add_argument(
        "--business", action="store_true", help="Exercise opt-in V13.8.1 business mode"
    )
    parser.add_argument(
        "--frontend",
        action="store_true",
        help="Real HTTPS Vue/Chromium campaign, implies business mode",
    )
    parser.add_argument(
        "--assistant",
        action="store_true",
        help="Real Ollama + tenant PostgreSQL assistant; implies frontend",
    )
    parser.add_argument(
        "--canonical",
        action="store_true",
        help="Canonical container composition and recovery campaign",
    )
    parser.add_argument('--continuity', action='store_true', help='Real SSO/conversation continuity only; no model generation')
    parser.add_argument('--renewal', action='store_true', help='Opt-in encrypted server renewal on disposable services')
    parser.add_argument('--renewal-timed', action='store_true', help='Actual 30-minute active session and expiry (implies renewal)')
    args = parser.parse_args()
    if args.renewal_timed:
        args.renewal = True
    if args.renewal:
        args.continuity = True
    if args.continuity and (args.assistant or args.canonical):
        parser.error("Continuity does not run the model/canonical recovery campaign.")
    if args.continuity:
        args.frontend = True
    if args.canonical and preset != "default":
        parser.error(
            "Canonical harness keeps its own fixed ports; use the BFF harness for isolated ports."
        )
    if args.canonical and not args.assistant:
        parser.error("Canonical validation requires --assistant.")
    if args.assistant:
        args.frontend = True
    if args.frontend:
        args.business = True
        global ISSUER
        ISSUER = f"http://localhost:{OIDC}/realms/stockpilot-dev"
    if args.canonical:
        global COMPOSE
        from scripts.tests.canonical_demo import COMPOSE, CanonicalDemo, local_dns
        from scripts.tests.canonical_demo import ISSUER as canonical_issuer

        ISSUER = canonical_issuer
        local_dns()
    os.umask(0o077)
    for port in (
        (APP, OIDC, MONGO, POSTGRES, 57919)
        if args.canonical and args.regressions
        else (APP, OIDC, MONGO, POSTGRES)
    ):
        with socket.socket() as probe:
            # A finished owned HTTPS campaign may leave TIME_WAIT sockets.
            # REUSEADDR still refuses any live listener on the same address.
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(("127.0.0.1", port))
    endpoint = command(
        ["docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"]
    ).strip()
    if not endpoint.startswith("unix://") or (
        os.getenv("DOCKER_HOST") and not os.environ["DOCKER_HOST"].startswith("unix://")
    ):
        raise RuntimeError("Local Unix Docker engine required.")
    project = "stockpilot-bff-test-" + uuid4().hex[:12]
    env = os.environ.copy()
    env.update(
        {
            "BFF_TEST_PROJECT": project,
            "BFF_TEST_KEYCLOAK_PORT": str(OIDC),
            "BFF_TEST_MONGO_PORT": str(MONGO),
            "BFF_TEST_POSTGRES_PORT": str(POSTGRES),
            "B2B_TEST_ORIGIN": ORIGIN,
            "BFF_TEST_CLIENT_SECRET": secrets.token_urlsafe(36),
            "BFF_TEST_USER_PASSWORD": secrets.token_urlsafe(30),
            "BFF_TEST_DB_PASSWORD": secrets.token_urlsafe(30),
        }
    )
    if args.frontend:
        env["BFF_TEST_ISSUER_ORIGIN"] = f"http://localhost:{OIDC}"
        env["B2B_TEST_ISSUER_ORIGIN"] = env["BFF_TEST_ISSUER_ORIGIN"]
    baseline = docker_snapshot()
    baseline_volumes = set(command(["docker", "volume", "ls", "-q"]).split())
    if command(
        [
            "docker",
            "ps",
            "-aq",
            "--filter",
            "label=com.docker.compose.project=" + project,
        ]
    ).strip():
        raise RuntimeError("Test project already exists.")
    (ROOT / "tmp").mkdir(exist_ok=True)
    directory = Path(
        tempfile.mkdtemp(
            prefix="v1384-" if args.canonical else "v1372-", dir=ROOT / "tmp"
        )
    )
    realm = directory / "realm.json"
    realm.write_text(
        (ROOT / "docker/keycloak/stockpilot-dev-realm.json")
        .read_text()
        .replace("https://127.0.0.1:57914", ORIGIN)
    )
    if args.renewal:
        definition = json.loads(realm.read_text())
        definition['revokeRefreshToken'] = True
        definition['refreshTokenMaxReuse'] = 0
        realm.write_text(json.dumps(definition))
    env["BFF_TEST_REALM_FILE"] = str(realm)
    report = {
        "project": project,
        "keycloak_version": "26.8.1",
        "kind": "real HTTP OIDC + HTTPS Vue/Chromium"
        if args.frontend
        else "real HTTP OIDC",
        "checks": [],
        "passed": False,
        "cleanup": False,
    }
    if args.canonical:
        report["kind"] = (
            "canonical HTTPS proxy/container BFF/Vue/Keycloak and real paired backup/restore"
        )
    process, logfile, mongo = None, None, None
    owned = {}
    compose = ["docker", "compose", "-f", str(COMPOSE), "-p", project]
    if args.canonical:
        compose[4:4] = ["--env-file", "/dev/null"]
    canonical = None
    cert, key = make_certificate(directory)
    context = ssl.create_default_context(cafile=str(cert))
    if args.canonical:
        canonical = CanonicalDemo(directory, env, compose, command, cert, key)
        env["B2B_TEST_ISSUER_ORIGIN"] = f"https://auth.stockpilot.test:{OIDC}"
        env["BFF_TEST_ISSUER_ORIGIN"] = env["B2B_TEST_ISSUER_ORIGIN"]

    def passed(name):
        report["checks"].append(name)

    def validate_owned():
        ids = command([*compose, "ps", "-aq"], env).split()
        for cid in ids:
            record = json.loads(command(["docker", "inspect", cid]))[0]
            labels = record["Config"]["Labels"]
            assert cid not in baseline
            assert labels["com.docker.compose.project"] == project
            assert labels["stockpilot.audit.purpose"] == (
                "v1384-ephemeral" if canonical else "v1372-ephemeral"
            )
            assert str(COMPOSE) in labels["com.docker.compose.project.config_files"]
            for mount in record.get("Mounts", []):
                if canonical:
                    canonical.validate_mount(mount)
                    continue
                assert mount["Type"] in {"tmpfs", "bind"}
                if mount["Type"] == "bind":
                    assert Path(mount["Source"]).resolve() == realm.resolve()
                    assert mount["RW"] is False
            for bindings in (record["HostConfig"].get("PortBindings") or {}).values():
                assert all(b["HostIp"] == "127.0.0.1" for b in bindings)
            owned[cid] = labels["com.docker.compose.service"]
        return ids

    def stop_bff():
        nonlocal process, logfile
        if canonical:
            canonical.stop()
            return
        if process is not None:
            process.terminate()
            process.wait(timeout=20)
            process = None
        if logfile is not None:
            logfile.close()
            logfile = None

    def start_bff(bff_env, cert, key, context):
        nonlocal process, logfile
        if canonical:
            canonical.start(bff_env)
            return
        logfile = (directory / "bff-private.log").open("a")
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "scripts.tests.b2b_browser_app:create_app"
                if args.frontend
                else "stockpilot.bff:create_app",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                str(APP),
                "--no-access-log",
                "--no-proxy-headers",
                "--ssl-certfile",
                str(cert),
                "--ssl-keyfile",
                str(key),
            ],
            cwd=ROOT,
            env=bff_env,
            stdout=logfile,
            stderr=subprocess.STDOUT,
        )
        for _ in range(100):
            if process.poll() is not None:
                raise RuntimeError("Isolated BFF failed to start.")
            try:
                if (
                    httpx.get(ORIGIN + "/health", verify=context, timeout=1).status_code
                    == 200
                ):
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
        raise RuntimeError("Isolated BFF startup deadline.")

    try:
        if canonical:
            canonical.build_images()
        command([*compose, "up", "-d"], env)
        validate_owned()
        assert len(owned) == 4
        if canonical:
            canonical.start_proxy()
            canonical.start_sampling()
        passed("unique local project, isolated mounts, services and loopback ports")
        for _ in range(150):
            try:
                discovery = httpx.get(
                    ISSUER + "/.well-known/openid-configuration",
                    timeout=2,
                    verify=context,
                    trust_env=False,
                )
                if discovery.status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(1)
        else:
            raise RuntimeError("Keycloak discovery deadline.")
        assert discovery.json()["issuer"] == ISSUER
        passed("real Keycloak discovery")
        dbname = "stockpilot_b2b_test_" + uuid4().hex
        mongo_uri = f"mongodb://127.0.0.1:{MONGO}/?replicaSet=rs0&directConnection=true"
        sys.path.insert(0, str(ROOT / "tests"))
        from integration_targets import mongo_test_client

        from stockpilot.infrastructure.persistence.mongodb.b2b_identity import (
            initialize_b2b_indexes,
        )

        mongo = mongo_test_client(environ={"MONGO_TEST_URI": mongo_uri})
        db = mongo[dbname]
        initialize_b2b_indexes(db)
        now = datetime.now(UTC)
        db.b2b_users.insert_one(
            {
                "user_id": "fixture-user",
                "external_identity": {
                    "issuer": ISSUER,
                    "subject": "00000000-0000-4000-8000-000000000001",
                },
                "status": "active",
                "created_at": now,
                "updated_at": now,
            }
        )
        db.b2b_organizations.insert_one(
            {
                "organization_id": "fixture-org",
                "name": "Synthetic test company",
                "status": "active",
                "created_at": now,
                "updated_at": now,
            }
        )
        db.b2b_memberships.insert_one(
            {
                "membership_id": "fixture-membership",
                "user_id": "fixture-user",
                "organization_id": "fixture-org",
                "role": "USER",
                "status": "active",
                "created_at": now,
                "updated_at": now,
            }
        )
        passed("explicit provisioning in Docker-verified disposable Mongo database")
        bff_env = env | {
            "BFF_MODE": "b2b_business_test" if args.business else "b2b_auth_test",
            "BFF_ORIGIN": ORIGIN,
            "BFF_ISSUER": ISSUER,
            "BFF_CLIENT_ID": "stockpilot-bff",
            "BFF_CLIENT_SECRET": env["BFF_TEST_CLIENT_SECRET"],
            "BFF_ALLOW_LOOPBACK_HTTP_ISSUER": "true",
            "BFF_MONGO_URI": mongo_uri,
            "BFF_MONGO_DATABASE": dbname,
            "BFF_CONFIGURATION_KEY": secrets.token_urlsafe(36),
        }
        if args.business:
            from bson import ObjectId

            from stockpilot.infrastructure.persistence.mongodb.bff_store import (
                MongoBffStore,
            )
            from stockpilot.infrastructure.persistence.mongodb.schema_migrations import (
                MigrationRunner,
            )

            MigrationRunner(db).apply(confirmation=dbname)
            MongoBffStore(db).initialize()
            db.b2b_buyers.insert_one(
                {
                    "association_id": "fixture-buyer",
                    "organization_id": "fixture-org",
                    "user_id": "fixture-user",
                    "customer_id": "fixture-customer",
                    "status": "active",
                    "revision": 1,
                }
            )
            for org, quantity in (("fixture-org", 10), ("fixture-other", 2)):
                db.b2b_products.insert_one(
                    {
                        "_id": ObjectId(),
                        "organization_id": org,
                        "sku": "DRILL-001",
                        "name": "Synthetic drill",
                        "description": "Disposable HTTP fixture",
                        "category": "drill",
                        "price_minor": 4900,
                        "currency": "EUR",
                        "status": "active",
                        "attributes": [],
                        "quantity": quantity,
                        "reserved": 0,
                        "revision": 1,
                    }
                )
        if args.frontend:
            bff_env["B2B_BROWSER_TEST"] = "isolated"
        if args.assistant or args.continuity:
            import psycopg

            from stockpilot.infrastructure.persistence.postgres.tenant_migrations import (
                PostgresTenantMigrations,
            )

            pg_uri = (
                "postgresql://ci:"
                + env["BFF_TEST_DB_PASSWORD"]
                + f"@127.0.0.1:{POSTGRES}/stockpilot_ci"
            )
            runner = PostgresTenantMigrations(pg_uri)
            with psycopg.connect(pg_uri) as connection:
                target, identity = runner.target(connection)
            runner.apply(database=target, system_identifier=identity)
            # No inference is part of SSO continuity. A closed local endpoint ensures
            # this campaign cannot silently exercise a demo/model provider.
            if args.continuity:
                endpoint = 'http://127.0.0.1:9'
                report['model_generation'] = 'not_requested_no_model_calls'
            else:
                endpoint = os.environ.get("V1383_OLLAMA_URL", "")
                if not endpoint:
                    raise RuntimeError("Explicit available Ollama endpoint required.")
                tags = httpx.get(endpoint + "/api/tags", timeout=5).json()["models"]
                required = {"qwen2.5:7b", "nomic-embed-text:latest"}
                assert required <= {model["name"] for model in tags}
                report["ollama_models"] = [
                    {"name": model["name"], "digest": model["digest"]}
                    for model in tags
                    if model["name"] in required
                ]
                report["ollama_initial_loaded_count"] = len(
                    httpx.get(endpoint + "/api/ps", timeout=5).json()["models"]
                )
            bff_env.update(
                {
                    "BFF_ASSISTANT_ENABLED": "true",
                    "BFF_ASSISTANT_POSTGRES_URI": pg_uri,
                    "BFF_ASSISTANT_OLLAMA_URL": endpoint,
                    "BFF_ASSISTANT_ALLOW_HTTP": "true",
                }
            )
        if args.renewal:
            import base64
            bff_env['BFF_REFRESH_ENABLED'] = 'true'
            bff_env['BFF_REFRESH_ENCRYPTION_KEY'] = base64.b64encode(os.urandom(32)).decode()
        start_bff(bff_env, cert, key, context)
        if canonical:
            canonical.security_probes()
            passed(
                "real HTTPS proxy denies admin/diagnostics/historical APIs and forged Host"
            )

        def browser():
            return httpx.Client(
                verify=context,
                trust_env=False,
                # Must exceed the provider's own 10 s read timeout so its
                # sanitized 503 can reach this client during the outage test.
                timeout=25,
                follow_redirects=False,
                limits=httpx.Limits(max_keepalive_connections=0),
            )

        def auth_code(client, username="bff-test-user", organization="fixture-org"):
            response = client.post(
                ORIGIN + "/b2b/auth/login",
                json={"organization_id": organization},
                headers={"Origin": ORIGIN, "X-BFF-CSRF": "login"},
            )
            assert response.status_code == (200 if args.business else 303)
            auth_url = (
                response.json()["authorization_url"]
                if args.business
                else response.headers["location"]
            )
            query = parse_qs(urlsplit(auth_url).query)
            assert query["code_challenge_method"] == ["S256"]
            assert query["nonce"] and query["state"] and query["code_challenge"]
            assert "client_secret" not in query and "code_verifier" not in query
            page = client.get(auth_url)
            if page.status_code == 200:
                form = LoginForm()
                form.feed(page.text)
                assert (
                    form.action
                    and urlsplit(form.action).netloc == urlsplit(ISSUER).netloc
                )
                # Keycloak treats loopback as a browser secure context. HTTPX
                # omits its Secure cookies over HTTP, unlike that local browser
                # flow. Forward only the provider's path-scoped cookies to its
                # exact checked loopback form; never forward a BFF cookie.
                provider_cookies = "; ".join(
                    f"{item.name}={item.value}"
                    for item in client.cookies.jar
                    if item.domain
                    in {urlsplit(ISSUER).hostname, urlsplit(ISSUER).hostname + ".local"}
                    and item.path.startswith("/realms/stockpilot-dev")
                    and urlsplit(form.action).path.startswith(item.path)
                )
                page = client.post(
                    form.action,
                    headers={
                        "Cookie": provider_cookies,
                        "Origin": urlsplit(ISSUER).scheme
                        + "://"
                        + urlsplit(ISSUER).netloc,
                    },
                    data=form.hidden
                    | {
                        "username": username,
                        "password": env["BFF_TEST_USER_PASSWORD"],
                        "credentialId": "",
                    },
                )
            if page.status_code not in {302, 303}:
                report["keycloak_form_status"] = page.status_code
                report["keycloak_form_diagnostic"] = [
                    marker
                    for marker in (
                        "Invalid username or password",
                        "Cookie not found",
                        "Update Account",
                        "Update Password",
                        "Invalid Request",
                        "Unexpected error",
                        "Invalid parameter",
                        "We are sorry",
                        "Restart login",
                        "kc-update-profile-form",
                        "kc-totp-settings-form",
                    )
                    if marker.lower() in page.text.lower()
                ]
            assert page.status_code in {302, 303}
            callback = page.headers["location"]
            assert callback.startswith(ORIGIN + "/b2b/auth/callback?")
            return callback

        with browser() as client:
            callback = auth_code(client)
            assert client.get(callback).status_code == 303
            info = client.get(ORIGIN + "/b2b/auth/session")
            assert info.status_code == 200 and info.json()["user_id"] == "fixture-user"
            assert not any(
                x in info.json() for x in ("id_token", "access_token", "refresh_token")
            )
            passed(
                "confidential code exchange, S256/state/nonce, verified identity and opaque HTTPS session"
            )
            if args.business:
                before = db.b2b_products.find_one({"organization_id": "fixture-other"})
                h = {"Origin": ORIGIN, "X-BFF-CSRF": info.json()["csrf_token"]}
                page = client.get(ORIGIN + "/b2b/api/products").json()
                assert (
                    len(page["items"]) == 1
                    and page["items"][0]["organization_id"] == "fixture-org"
                )
                prepared = client.post(
                    ORIGIN + "/b2b/api/orders/prepare",
                    headers=h,
                    json={
                        "operation_id": "real-prepare",
                        "customer_id": "fixture-customer",
                        "lines": [{"sku": "DRILL-001", "quantity": 1}],
                    },
                )
                assert prepared.status_code == 200
                order = prepared.json()
                assert (
                    db.b2b_products.find_one({"organization_id": "fixture-org"})[
                        "reserved"
                    ]
                    == 0
                )
                url = ORIGIN + "/b2b/api/orders/" + order["order_id"]
                confirm_body = {
                    "operation_id": "real-confirm",
                    "preparation_digest": order["preparation_digest"],
                    "confirmed": True,
                }
                for _ in range(2):
                    assert (
                        client.post(
                            url + "/confirm", headers=h, json=confirm_body
                        ).status_code
                        == 200
                    )
                assert (
                    db.b2b_products.find_one({"organization_id": "fixture-org"})[
                        "reserved"
                    ]
                    == 1
                )
                for _ in range(2):
                    assert (
                        client.post(
                            url + "/cancel",
                            headers=h,
                            json={"operation_id": "real-cancel"},
                        ).status_code
                        == 200
                    )
                assert (
                    db.b2b_products.find_one({"organization_id": "fixture-org"})[
                        "reserved"
                    ]
                    == 0
                )
                assert (
                    db.b2b_products.find_one({"organization_id": "fixture-other"})
                    == before
                )
                assert client.get(ORIGIN + "/products").status_code == 404
                passed(
                    "business mode: JSON login navigation, scoped catalogue, real atomic order/replays/cancel; foreign Mongo unchanged"
                )
            assert client.get(callback).status_code == 401
            passed("real callback replay refused")
        with browser() as client:
            assert client.get(auth_code(client)).status_code == 303
            old_cookie = client.cookies.get(COOKIE)
            stop_bff()
            start_bff(bff_env, cert, key, context)
            assert client.get(ORIGIN + "/b2b/auth/me").status_code == 200
            passed("BFF restart preserves shared Mongo session")
            assert client.get(auth_code(client)).status_code == 303
            assert client.cookies.get(COOKIE) != old_cookie
            assert (
                db.bff_sessions.find_one(
                    {"_id": sha256(old_cookie.encode()).hexdigest()}
                )
                is None
            )
            passed("reauthentication rotates and revokes previous application session")
            info = client.get(ORIGIN + "/b2b/auth/session").json()
            assert (
                client.post(
                    ORIGIN + "/b2b/auth/logout",
                    headers={
                        "Origin": ORIGIN,
                        "X-BFF-CSRF": info["csrf_token"],
                    },
                ).status_code
                == 200
            )
            assert client.get(ORIGIN + "/b2b/auth/session").status_code == 401
            passed("logout permanently invalidates application session")
        with browser() as client:
            assert client.get(auth_code(client, "bff-unprovisioned")).status_code == 403
            assert db.b2b_users.count_documents({}) == 1
            passed("unknown user refused without automatic provisioning")
        if args.business:
            # Only after proving refusal, explicitly provision the second synthetic
            # human. No callback ever creates this association.
            db.b2b_organizations.insert_one(
                {
                    "organization_id": "fixture-other",
                    "name": "Second synthetic org",
                    "status": "active",
                    "created_at": now,
                    "updated_at": now,
                }
            )
            db.b2b_users.insert_one(
                {
                    "user_id": "fixture-other-user",
                    "external_identity": {
                        "issuer": ISSUER,
                        "subject": "00000000-0000-4000-8000-000000000002",
                    },
                    "status": "active",
                    "created_at": now,
                    "updated_at": now,
                }
            )
            db.b2b_memberships.insert_one(
                {
                    "membership_id": "fixture-other-membership",
                    "user_id": "fixture-other-user",
                    "organization_id": "fixture-other",
                    "role": "MANAGER",
                    "status": "active",
                    "created_at": now,
                    "updated_at": now,
                }
            )
            if canonical:
                db.b2b_buyers.insert_one(
                    {
                        "association_id": "fixture-other-buyer",
                        "organization_id": "fixture-other",
                        "user_id": "fixture-other-user",
                        "customer_id": "fixture-other-customer",
                        "status": "active",
                        "revision": 1,
                    }
                )
            with browser() as second:
                assert (
                    second.get(
                        auth_code(second, "bff-unprovisioned", "fixture-other")
                    ).status_code
                    == 303
                )
                page = second.get(ORIGIN + "/b2b/api/products").json()
                assert (
                    len(page["items"]) == 1
                    and page["items"][0]["organization_id"] == "fixture-other"
                )
                assert second.get(ORIGIN + "/b2b/api/orders").json()["items"] == []
                foreign = str(
                    db.b2b_products.find_one({"organization_id": "fixture-org"})["_id"]
                )
                assert (
                    second.get(ORIGIN + "/b2b/api/products/" + foreign).status_code
                    == 404
                )
            passed(
                "two explicitly provisioned organizations with real Keycloak identities and isolated HTTP reads"
            )
        if args.frontend:
            from scripts.tests.b2b_browser_campaign import run_browser

            run_browser(db, bff_env, directory, cert, passed, continuity=args.continuity)
            if args.assistant:
                from scripts.tests.b2b_assistant_campaign import run_assistant_browser

                run_assistant_browser(db, bff_env, directory, cert, passed, report)
                if canonical:
                    models = httpx.get(
                        bff_env["BFF_ASSISTANT_OLLAMA_URL"] + "/api/ps",
                        timeout=5,
                        trust_env=False,
                    ).json()["models"]
                    report["ollama_loaded_after_assistant"] = [
                        {key: model.get(key) for key in ("name", "size", "size_vram")}
                        for model in models
                    ]
                stop_bff()
                start_bff(bff_env, cert, key, context)
                with browser() as resumed:
                    assert resumed.get(auth_code(resumed)).status_code == 303
                    session_data = resumed.get(ORIGIN + "/b2b/auth/session").json()
                    headers = {
                        "Origin": ORIGIN,
                        "X-BFF-CSRF": session_data["csrf_token"],
                    }
                    conversation = report["assistant_browser"]["conversation_id"]
                    response = resumed.get(
                        ORIGIN + "/b2b/api/assistant/conversations/" + conversation,
                        headers=headers,
                    )
                    assert response.status_code == 200 and response.json()["items"]
                passed(
                    "owned BFF restart preserves private assistant transcript/checkpoints"
                )
                # A closed, owned loopback port simulates an unavailable provider.
                # Never stop or reconfigure the existing Ollama instance.
                stop_bff()
                offline_env = bff_env | {
                    "BFF_ASSISTANT_OLLAMA_URL": "http://127.0.0.1:9"
                }
                start_bff(offline_env, cert, key, context)
                with browser() as offline:
                    assert offline.get(auth_code(offline)).status_code == 303
                    session_data = offline.get(ORIGIN + "/b2b/auth/session").json()
                    headers = {
                        "Origin": ORIGIN,
                        "X-BFF-CSRF": session_data["csrf_token"],
                    }
                    response = offline.post(
                        ORIGIN + "/b2b/api/assistant/conversations",
                        headers=headers,
                        json={"thread_id": "outage"},
                    )
                    base = (
                        ORIGIN
                        + "/b2b/api/assistant/conversations/"
                        + response.json()["conversation_id"]
                    )
                    response = offline.post(
                        base + "/messages",
                        headers=headers,
                        json={
                            "message_id": "same-outage-turn",
                            "message": "Quelle est la garantie documentaire ?",
                        },
                    )
                    assert response.status_code == 503
                stop_bff()
                start_bff(bff_env, cert, key, context)
                with browser() as recovered:
                    assert recovered.get(auth_code(recovered)).status_code == 303
                    session_data = recovered.get(ORIGIN + "/b2b/auth/session").json()
                    headers = {
                        "Origin": ORIGIN,
                        "X-BFF-CSRF": session_data["csrf_token"],
                    }
                    assert (
                        recovered.get(base, headers=headers).json()["items"][-1][
                            "status"
                        ]
                        == "pending"
                    )
                    response = recovered.post(
                        base + "/turns/same-outage-turn/retry",
                        headers=headers,
                        json={},
                        timeout=90,
                    )
                    assert (
                        response.status_code == 200
                        and response.json()["status"] == "completed"
                    )
                passed(
                    "unavailable Ollama endpoint fails 503; persisted original turn retries after owned BFF restart"
                )
        if args.renewal and not args.renewal_timed:
            from scripts.tests.bff_renewal_expiry import run_short_expiry
            stop_bff()
            start_bff(bff_env | {"BFF_SESSION_ABSOLUTE_SECONDS": "30", "BFF_SESSION_IDLE_SECONDS": "30"}, cert, key, context)
            run_short_expiry(browser, auth_code, directory, passed)
            stop_bff()
            start_bff(bff_env, cert, key, context)
        if args.renewal:
            from scripts.tests.bff_renewal_faults import run_revocation
            run_revocation(browser, auth_code, db, bff_env, passed)
        if args.renewal_timed:
            from scripts.tests.bff_renewal_timed import run_timed
            run_timed(browser, auth_code, db, directory, passed, report)
        if canonical:
            from scripts.tests.canonical_recovery import run_recovery

            run_recovery(
                canonical,
                db,
                bff_env,
                directory,
                cert,
                key,
                browser,
                auth_code,
                passed,
                report,
            )
        db.b2b_organizations.update_one(
            {"organization_id": "fixture-org"}, {"$set": {"status": "suspended"}}
        )
        with browser() as client:
            assert client.get(auth_code(client)).status_code == 403
            passed("suspended organization refused")
        db.b2b_organizations.update_one(
            {"organization_id": "fixture-org"}, {"$set": {"status": "active"}}
        )
        with browser() as client:
            if args.renewal:
                assert client.get(auth_code(client)).status_code == 303
                renewal_sid = sha256(client.cookies.get(COOKIE).encode()).hexdigest()
                db.bff_sessions.update_one({"_id": renewal_sid}, {"$set": {"provider_expires_at": datetime.now(UTC) + timedelta(seconds=1)}})
            callback = auth_code(client)
            keycloak_id = next(cid for cid, name in owned.items() if name == "keycloak")
            validate_owned()
            command(["docker", "stop", "--time", "10", keycloak_id])
            if args.renewal:
                assert client.get(ORIGIN + "/b2b/auth/session").status_code == 503
                assert db.bff_sessions.find_one({"_id": renewal_sid}) is None
                passed("real provider outage during renewal fails closed and revokes the BFF session")
            response = client.get(callback)
            assert response.status_code == 503
            assert (
                "access_token" not in response.text
                and "client_secret" not in response.text
            )
            passed(
                "provider outage during token exchange fails 503 without leaking credentials"
            )
            stop_bff()
            start_bff(bff_env, cert, key, context)
            response = client.post(
                ORIGIN + "/b2b/auth/login",
                json={"organization_id": "fixture-org"},
                headers={"Origin": ORIGIN, "X-BFF-CSRF": "login"},
            )
            assert response.status_code == 503
            passed("provider discovery unavailable fails 503")
        if canonical:
            report["resource_sample"] = canonical.stats()
        if args.regressions:
            if canonical:
                command(
                    [*compose, "--profile", "regression", "up", "-d", "auth-mongo"], env
                )
                validate_owned()
                from pymongo import MongoClient
                from pymongo.errors import PyMongoError

                auth_uri = (
                    "mongodb://ci:"
                    + env["BFF_TEST_DB_PASSWORD"]
                    + "@127.0.0.1:57919/?authSource=admin"
                )
                for _ in range(60):
                    try:
                        with MongoClient(
                            auth_uri, serverSelectionTimeoutMS=500
                        ) as auth_probe:
                            auth_probe.admin.command("ping")
                        break
                    except PyMongoError:
                        time.sleep(0.5)
                else:
                    raise RuntimeError("Disposable auth Mongo readiness deadline.")
            regression_env = bff_env | {
                "MONGO_TEST_URI": mongo_uri,
                "POSTGRES_TEST_URI": "postgresql://ci:"
                + env["BFF_TEST_DB_PASSWORD"]
                + f"@127.0.0.1:{POSTGRES}/stockpilot_ci",
                "PYTHONDONTWRITEBYTECODE": "1",
            }
            if canonical:
                regression_env["MONGO_MIGRATION_AUTH_TEST_URI"] = auth_uri
            output = command(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "-q",
                    "-p",
                    "no:cacheprovider",
                    "-m",
                    "integration and not integration_optional",
                    "tests",
                ],
                regression_env,
            )
            summary = re.findall(r"\d+ passed[^\n]*", output)
            report["integration_summary"] = summary[-1] if summary else "passed"
            passed("guarded isolated integration regression suite")
        report["passed"] = True
    except Exception as error:  # noqa: BLE001 - redact test failures, then preserve evidence and clean owned resources
        report["error_type"] = type(error).__name__
        frame = traceback.extract_tb(error.__traceback__)[-1]
        report["error_location"] = f"{Path(frame.filename).name}:{frame.lineno}"
        report["error_frames"] = [
            f"{Path(f.filename).name}:{f.lineno}"
            for f in traceback.extract_tb(error.__traceback__)
            if str(ROOT) in f.filename
        ]
    finally:
        try:
            if canonical:
                canonical.stop_sampling()
                report["resource_samples"] = canonical.samples
                report["capacity_before"] = canonical.capacity_start
            stop_bff()
            if mongo is not None:
                mongo.close()
            validate_owned()
            command(
                [*compose, "--profile", "*", "down", "--timeout", "15"], env
            ) if canonical else command([*compose, "down", "--timeout", "15"], env)
            if canonical:
                canonical.cleanup_volumes()
                canonical.cleanup_images()
            if canonical:
                import shutil

                report["capacity_after"] = {
                    "ubuntu_free": shutil.disk_usage(ROOT).free,
                    "windows_c_free": shutil.disk_usage("/mnt/c").free,
                }
            report["cleanup"] = not command([*compose, "ps", "-aq"], env).strip()
            after = docker_snapshot()
            report["protected_containers_unchanged"] = all(
                after.get(cid) == record for cid, record in baseline.items()
            )
            report["volumes_unchanged"] = baseline_volumes == set(
                command(["docker", "volume", "ls", "-q"]).split()
            )
        except Exception as error:  # noqa: BLE001 - cleanup boundary must preserve the diagnostic report
            # Preserve unsafe/uncertain resources rather than broadening cleanup.
            report["cleanup_error_type"] = type(error).__name__
        (directory / "report.json").write_text(json.dumps(report, indent=2))
        print(json.dumps({"report": str(directory / "report.json"), **report}))
        for secret_file in (directory / "tls.key", directory / "tls.crt"):
            if secret_file.exists():
                secret_file.unlink()
    if not all(
        report.get(key)
        for key in (
            "passed",
            "cleanup",
            "protected_containers_unchanged",
            "volumes_unchanged",
        )
    ):
        raise SystemExit("Isolated campaign incomplete; inspect the redacted report.")


if __name__ == "__main__":
    main()
