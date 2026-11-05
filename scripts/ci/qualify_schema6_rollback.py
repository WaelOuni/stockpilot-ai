"""Local Docker-only schema6 roundtrip; no published ports or production targets.

Every mutable resource is freshly created, labelled, identified and inspected.
The Docker daemon must be local. Archives use dynamic database inventories.
"""
import argparse
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.ops.schema6_rollback import QualificationError, command, digest, require
from scripts.ops.recovery_contract import compare_capture

ROOT = Path(__file__).resolve().parents[2]
LABEL = "io.stockpilot.qualification.owner"


def local_engine():
    context = json.loads(command(["docker", "context", "inspect"]))[0]
    endpoint = context["Endpoints"]["docker"]["Host"]
    override = os.environ.get("DOCKER_HOST", endpoint)
    # Require BOTH configurations to be local: DOCKER_CONTEXT/DOCKER_HOST
    # precedence must never turn an apparently local validation into remote I/O.
    require(endpoint.startswith("unix:///") and override.startswith("unix:///")
            and not os.environ.get("DOCKER_TLS_VERIFY"),
            "Local Unix Docker engine required.")
    require(command(["docker", "info", "--format", "{{.OSType}}"]).strip() == b"linux", "Linux engine required.")


class Lab:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.owner = "stockpilot-schema6-" + uuid4().hex
        self.containers = {}
        self.networks = {}
        self.removed = []
        self.stage = "preflight"

    def network(self, suffix):
        name = self.owner + "-" + suffix
        cid = command(["docker", "network", "create", "--internal", "--label", LABEL + "=" + self.owner, name]).decode().strip()
        self.networks[cid] = name
        row = json.loads(command(["docker", "network", "inspect", cid]))[0]
        require(row["Internal"] and row.get("Labels", {}).get(LABEL) == self.owner, "Network ownership mismatch.")
        return cid

    def attest(self, cid):
        expected = self.containers[cid]
        row = json.loads(command(["docker", "inspect", cid]))[0]
        require(row["Id"] == cid and row["Config"].get("Labels", {}).get(LABEL) == self.owner,
                "Container ownership mismatch.")
        require(row["Image"] == expected["image"], "Container image mismatch.")
        require(not row["HostConfig"].get("PortBindings") and not row["HostConfig"].get("PublishAllPorts"),
                "Published ports forbidden.")
        require(not row["HostConfig"].get("Privileged"), "Privileged containers forbidden.")
        networks = row["NetworkSettings"]["Networks"]
        ids = {x["NetworkID"] for x in networks.values() if x.get("NetworkID")}
        require(row["HostConfig"].get("NetworkMode") == expected["network"]
                and len(networks) <= 1 and ids <= {expected["network"]}
                and (ids or row["State"]["Status"] in {"created", "exited"}),
                "Foreign network attachment.")
        for mount in row.get("Mounts", []):
            require(mount["Type"] in {"tmpfs", "bind"}, "Persistent/anonymous volumes forbidden.")
            if mount["Type"] == "bind":
                require(Path(mount["Source"]).resolve().is_relative_to(self.directory), "Foreign bind mount.")
        return row

    def create(self, service, image, network, args=(), options=()):
        image_id = json.loads(command(["docker", "image", "inspect", image]))[0]["Id"]
        name = self.owner + "-" + service
        cid = command(["docker", "create", "--name", name, "--network", network,
                       "--label", LABEL + "=" + self.owner, *options, image_id, *args]).decode().strip()
        self.containers[cid] = {"image": image_id, "network": network, "service": service}
        self.attest(cid)
        return cid

    def start(self, cid):
        self.attest(cid)
        command(["docker", "start", cid])

    def stop(self, cid):
        self.attest(cid)
        command(["docker", "stop", "--time", "45", cid])

    def exec(self, cid, args, *, input=None):
        self.attest(cid)
        return command(["docker", "exec", *(["-i"] if input is not None else []), cid, *args], input=input, timeout=120)

    def wait(self, cid, args):
        for _ in range(90):
            try:
                self.exec(cid, args)
                return
            except QualificationError:
                time.sleep(1)
        raise QualificationError("Database readiness deadline.")

    def file(self, name, text):
        path = self.directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        # Parent directory is 0700; readonly file mounts are readable by image UID 10001/1000.
        path.chmod(0o444)
        return path

    def bind(self, path, target, readonly=True):
        require(Path(path).resolve().is_relative_to(self.directory), "Non-owned bind refused.")
        return ["--mount", "type=bind,source=" + str(path) + ",target=" + target + (",readonly" if readonly else "")]

    def database_set(self, suffix, network, pg_env, *, restoring=False):
        result = {}
        result["mongo"] = self.create(suffix + "-mongo", "mongo:8", network,
            ["mongod", "--port", "27027", "--replSet", "rs0", "--bind_ip_all"],
            ["--network-alias", "mongo", "--tmpfs", "/data/db", "--tmpfs", "/data/configdb"])
        for key, port in (("application", "55432"), ("keycloak", "55433")):
            result[key] = self.create(suffix + "-pg-" + key, "pgvector/pgvector:pg16", network,
                ["postgres", "-p", port],
                ["--network-alias", "pg-" + key, "--env-file", str(pg_env[key]),
                 "--tmpfs", "/var/lib/postgresql/data"])
        for cid in result.values():
            self.start(cid)
        self.wait(result["mongo"], ["mongosh", "--port", "27027", "--quiet", "--eval", "db.adminCommand({ping:1})"])
        self.exec(result["mongo"], ["mongosh", "--port", "27027", "--quiet", "--eval",
            "rs.initiate({_id:'rs0',members:[{_id:0,host:'mongo:27027'}]})"])
        self.wait(result["mongo"], ["mongosh", "--port", "27027", "--quiet", "--eval",
            "if (!db.hello().isWritablePrimary) quit(1)"])
        for key, port in (("application", "55432"), ("keycloak", "55433")):
            self.wait(result[key], ["pg_isready", "-U", "restore_admin" if restoring else "ci", "-p", port])
        return result

    def keycloak(self, suffix, network, image, env, realm, cert, key, *, importing):
        options = ["--network-alias", "keycloak", "--env-file", str(env),
                   *self.bind(cert, "/opt/keycloak/conf/lab.crt"),
                   *self.bind(key, "/opt/keycloak/conf/lab.key")]
        if importing:
            options += self.bind(realm, "/opt/keycloak/data/import/lab.json")
        cid = self.create(suffix + "-keycloak", image, network,
                          ["start", *(["--import-realm"] if importing else [])], options)
        self.start(cid)
        return cid

    def probe(self, label, phase, image, network, state, env, config, cert, probe, guards, contract):
        self.stage = label
        print(json.dumps({"phase_started": label}), flush=True)
        cid = self.create("probe-" + label, image, network,
            ["/qualification/schema6_rollback_probe.py", phase],
            ["--read-only", "--entrypoint", "python", "--env-file", str(env),
             *self.bind(config, "/inputs/config.json"), *self.bind(cert, "/inputs/tls.crt"),
             *self.bind(state, "/state", False),
             *self.bind(probe, "/qualification/schema6_rollback_probe.py"),
             *self.bind(guards, "/qualification/integration_targets.py"),
             *self.bind(contract, "/qualification/recovery_contract.py")])
        self.attest(cid)
        result = subprocess.run(["docker", "start", "-a", cid], capture_output=True, timeout=360)
        lines = result.stdout.decode(errors="replace").splitlines()
        reports = []
        for line in lines:
            try:
                value = json.loads(line)
                if isinstance(value, dict) and ("success" in value):
                    reports.append(value)
            except ValueError:
                pass
        require(reports, "Probe returned no bounded diagnostic.")
        report = reports[-1]
        print(json.dumps({"phase": label, **report}), flush=True)
        require(result.returncode == 0 and report.get("success"), "Probe failed: " + label)
        return report

    def browser(self, build, candidate, network, state, env, config, cert, key, probe, guards, contract):
        self.stage = "historical-browser"
        server = self.create("browser-api", candidate, network,
            ["/qualification/schema6_rollback_probe.py", "serve-browser"],
            ["--read-only", "--entrypoint", "python", "--network-alias", "candidate-api",
             "--env-file", str(env), *self.bind(config, "/inputs/config.json"),
             *self.bind(cert, "/inputs/tls.crt"), *self.bind(key, "/inputs/tls.key"),
             *self.bind(state, "/state", False),
             *self.bind(probe, "/qualification/schema6_rollback_probe.py"),
             *self.bind(guards, "/qualification/integration_targets.py"),
             *self.bind(contract, "/qualification/recovery_contract.py")])
        self.start(server)
        nginx = self.file("browser-nginx.conf", """
server {
  listen 8443 ssl;
  server_name bff.example;
  ssl_certificate /inputs/tls.crt;
  ssl_certificate_key /inputs/tls.key;
  access_log off;
  root /usr/share/nginx/html;
  location /b2b/ {
    proxy_pass https://candidate-api:8000;
    proxy_set_header Host $http_host;
    proxy_set_header X-Forwarded-Proto "";
    proxy_set_header X-Forwarded-Host "";
    proxy_set_header X-Forwarded-For "";
    proxy_ssl_verify on;
    proxy_ssl_name bff.example;
    proxy_ssl_trusted_certificate /inputs/tls.crt;
  }
  location / { try_files $uri $uri/ /index.html; }
}
""")
        web = self.create("browser-web", build["web"]["image_id"], network,
            ["-g", "daemon off;"], ["--entrypoint", "nginx", "--network-alias", "bff.example",
             *self.bind(nginx, "/etc/nginx/conf.d/default.conf"),
             *self.bind(cert, "/inputs/tls.crt"), *self.bind(key, "/inputs/tls.key")])
        self.start(web)
        driver = self.create("browser-chromium", build["browser"]["image_id"], network, options=[
            "--shm-size=1g", *self.bind(config, "/inputs/config.json"),
            *self.bind(cert, "/inputs/tls.crt"), *self.bind(state, "/state")])
        try:
            result = subprocess.run(["docker", "start", "-a", driver], capture_output=True, timeout=300)
            reports = []
            for line in result.stdout.decode(errors="replace").splitlines():
                try:
                    value = json.loads(line)
                    if isinstance(value, dict) and "success" in value:
                        reports.append(value)
                except ValueError:
                    pass
            require(reports, "Browser returned no bounded diagnostic.")
            report = reports[-1]
            print(json.dumps({"phase": self.stage, **report}), flush=True)
            require(result.returncode == 0 and report.get("success"), "Historical browser qualification failed.")
            return report
        finally:
            self.stop(web)
            self.stop(server)

    def archive(self, cid, args, path, *, restore=False):
        self.attest(cid)
        require(path.resolve().is_relative_to(self.directory), "Foreign archive path.")
        with path.open("rb" if restore else "xb") as stream:
            result = subprocess.run(["docker", "exec", *(["-i"] if restore else []), cid, *args],
                stdin=stream if restore else None, stdout=subprocess.DEVNULL if restore else stream,
                stderr=subprocess.PIPE, timeout=180)
        require(result.returncode == 0, "Database archive operation failed.")
        path.chmod(0o600)
        return {"bytes": path.stat().st_size, "sha256": digest(path.read_bytes())}

    def cleanup(self):
        failures = []
        for cid in reversed(list(self.containers)):
            try:
                self.attest(cid)
                command(["docker", "rm", "-f", cid], timeout=90)
                self.removed.append(cid)
            except Exception:
                failures.append(cid)
        for nid in reversed(list(self.networks)):
            try:
                row = json.loads(command(["docker", "network", "inspect", nid]))[0]
                require(row.get("Labels", {}).get(LABEL) == self.owner and not row.get("Containers"),
                        "Network cleanup ownership mismatch.")
                command(["docker", "network", "rm", nid])
                self.removed.append(nid)
            except Exception:
                failures.append(nid)
        require(not failures, "Owned resource cleanup incomplete.")
        return {"removed_count": len(self.removed), "preexisting_resources_removed": 0}


def run(build, current, keycloak, output, browser_build):
    local_engine()
    require(browser_build["qualification_commit"] == build["qualification_commit"]
            and browser_build["base_commit"] == build["base_commit"], "Browser/source commit mismatch.")
    report = {"base_commit": build["base_commit"], "qualification_commit": build["qualification_commit"],
              "images": {k: v["image_id"] for k, v in build["images"].items()}, "phases": {},
              "ports_published": False, "success": False}
    with tempfile.TemporaryDirectory(prefix="stockpilot-schema6-lab-") as directory:
        lab = Lab(directory)
        try:
            cert, key = Path(directory) / "tls.crt", Path(directory) / "tls.key"
            command(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                     "-keyout", str(key), "-out", str(cert), "-days", "2",
                     "-subj", "/CN=keycloak", "-addext", "subjectAltName=DNS:keycloak,DNS:bff.example"])
            cert.chmod(0o444); key.chmod(0o444)
            password, client_secret = secrets.token_urlsafe(32), secrets.token_urlsafe(40)
            pg_password = secrets.token_urlsafe(32)
            actors = {name: {"org": org, "role": role, "subject": str(uuid4())} for name, org, role in (
                ("a1", "org-a", "USER"), ("a2", "org-a", "MANAGER"), ("a3", "org-a", "USER"),
                ("b1", "org-b", "USER"), ("admin-a", "org-a", "ORGANIZATION_ADMIN"),
                ("admin-b", "org-b", "ORGANIZATION_ADMIN"), ("readonly", "org-a", "READ_ONLY"))}
            database = "stockpilot_b2b_test_" + uuid4().hex
            config = lab.file("config.json", json.dumps({
                "issuer": "https://keycloak:8443/realms/rollback-lab", "database": database,
                "password": password, "actors": actors, "client_secret": client_secret,
                "configuration_key": secrets.token_urlsafe(40),
                "keycloak_pg_uri": "postgresql://ci:" + pg_password + "@pg-keycloak:55433/stockpilot_test_keycloak",
            }))
            realm = lab.file("realm.json", json.dumps({
                "realm": "rollback-lab", "enabled": True, "sslRequired": "all",
                "registrationAllowed": False, "resetPasswordAllowed": False,
                "clients": [{"clientId": "rollback-lab", "secret": client_secret, "enabled": True,
                    "publicClient": False, "standardFlowEnabled": True, "directAccessGrantsEnabled": False,
                    "redirectUris": ["https://bff.example:8443/b2b/auth/callback"],
                    "attributes": {"pkce.code.challenge.method": "S256"}}],
                "users": [{"id": value["subject"], "username": name, "enabled": True,
                    "firstName": "Synthetic", "lastName": "Qualification", "email": name + "@example.invalid",
                    "emailVerified": True, "requiredActions": [],
                    "credentials": [{"type": "password", "value": password, "temporary": False}]}
                    for name, value in actors.items()],
            }))
            pg_env = {name: lab.file("pg-" + name + ".env",
                "POSTGRES_USER=ci\nPOSTGRES_PASSWORD=" + pg_password + "\nPOSTGRES_DB=" + db + "\n")
                for name, db in (("application", "stockpilot_ci"), ("keycloak", "stockpilot_test_keycloak"))}
            restore_pg_env = {name: lab.file("restore-pg-" + name + ".env",
                "POSTGRES_USER=restore_admin\nPOSTGRES_PASSWORD=" + secrets.token_urlsafe(40)
                + "\nPOSTGRES_DB=postgres\n") for name in ("application", "keycloak")}
            kc_env = lab.file("keycloak.env", "\n".join([
                "KC_DB=postgres", "KC_DB_URL=jdbc:postgresql://pg-keycloak:55433/stockpilot_test_keycloak",
                "KC_DB_USERNAME=ci", "KC_DB_PASSWORD=" + pg_password,
                "KC_HOSTNAME=https://keycloak:8443", "KC_HTTP_ENABLED=false",
                "KC_HTTPS_CERTIFICATE_FILE=/opt/keycloak/conf/lab.crt",
                "KC_HTTPS_CERTIFICATE_KEY_FILE=/opt/keycloak/conf/lab.key",
            ]) + "\n")
            probe_env = lab.file("probe.env", "\n".join([
                "MONGO_TEST_URI=mongodb://mongo:27027/?replicaSet=rs0&directConnection=true",
                "POSTGRES_TEST_URI=postgresql://ci:" + pg_password + "@pg-application:55432/stockpilot_ci",
                "DEPLOYMENT_ENVIRONMENT=development", "LOG_FILE=", "PYTHONDONTWRITEBYTECODE=1",
            ]) + "\n")
            restore_probe_env = lab.file("restore-probe.env", probe_env.read_text()
                                         + "SCHEMA6_RESTORE_ROLE=restore_admin\n")
            probe = lab.file("probe.py", (ROOT / "scripts/tests/schema6_rollback_probe.py").read_text())
            guards = lab.file("integration_targets.py", (ROOT / "tests/integration_targets.py").read_text())
            contract = lab.file("recovery_contract.py", (ROOT / "scripts/ops/recovery_contract.py").read_text())
            states = {}
            for suffix in ("source", "restore"):
                path = Path(directory) / ("state-" + suffix)
                path.mkdir(mode=0o777); path.chmod(0o777)  # private parent 0700, image UID writes proof only
                states[suffix] = path
            networks = {s: lab.network(s) for s in states}
            source = lab.database_set("source", networks["source"], pg_env)
            kc = lab.keycloak("source", networks["source"], keycloak, kc_env, realm, cert, key, importing=True)
            def phase(label, phase_name, image=current, side="source"):
                value = lab.probe(label, phase_name, image, networks[side], states[side],
                                  restore_probe_env if side == "restore" else probe_env, config, cert, probe, guards, contract)
                report["phases"][label] = value
                return value
            phase("wait-source", "wait-keycloak")
            phase("seed-v4", "seed-v4", build["images"]["baseline"]["image_id"])
            phase("upgrade", "upgrade")
            phase("unchanged-v023-refused", "old-refusal", build["images"]["baseline"]["image_id"])
            phase("rollback", "rollback", build["images"]["candidate"]["image_id"])
            phase("negative", "negative", build["images"]["candidate"]["image_id"])
            phase("return-current", "return")
            # All BFF probe processes have exited; stop the sole remaining application writer.
            lab.stop(kc)
            for cid in lab.containers:
                if "probe-" in lab.containers[cid]["service"] or cid == kc:
                    require(not lab.attest(cid)["State"]["Running"], "Writer remains active.")
            phase("snapshot-before", "snapshot")
            before = json.loads((states["source"] / "snapshot.json").read_text())
            archives = {}
            mongo_dump = Path(directory) / "mongo.archive.gz"
            archives["mongo"] = lab.archive(source["mongo"],
                ["mongodump", "--port=27027", "--quiet", "--db=" + database, "--archive", "--gzip"], mongo_dump)
            for name, port, db in (("application", "55432", "stockpilot_ci"), ("keycloak", "55433", "stockpilot_test_keycloak")):
                archives[name] = lab.archive(source[name], [
                    "pg_dump", "-U", "ci", "-p", port, "-d", db, "--format=custom"], Path(directory) / (name + ".dump"))
                archives[name + "-roles"] = lab.archive(source[name], [
                    "pg_dumpall", "-U", "ci", "-p", port, "--globals-only"],
                    Path(directory) / (name + "-roles.sql"))
            phase("snapshot-after", "snapshot")
            compare_capture(before, json.loads((states["source"] / "snapshot.json").read_text()))
            target = lab.database_set("restore", networks["restore"], restore_pg_env, restoring=True)
            require(set(target.values()).isdisjoint(source.values()), "Restore container overlap.")
            # Restore globals into a distinct fresh bootstrap owner, as the private operator does.
            # Passwords are synthetic and remain only in 0600 archives, never in the artifact.
            for name, port, db in (("application", "55432", "stockpilot_ci"), ("keycloak", "55433", "stockpilot_test_keycloak")):
                present = lab.exec(target[name], ["psql", "-X", "-U", "restore_admin", "-p", port,
                    "-d", "postgres", "-At", "-c", "SELECT count(*) FROM pg_roles WHERE rolname='ci'"])
                require(present.strip() == b"0", "Restore owner already exists.")
                roles_file = Path(directory) / (name + "-roles.sql")
                require(digest(roles_file.read_bytes()) == archives[name + "-roles"]["sha256"],
                        "Role archive digest mismatch.")
                lab.archive(target[name], ["psql", "-X", "-U", "restore_admin", "-p", port,
                    "-d", "postgres", "-v", "ON_ERROR_STOP=1"], roles_file, restore=True)
                lab.exec(target[name], ["createdb", "-U", "restore_admin", "-p", port, "--owner", "ci", db])
            # Prove empty effective targets before restore (no drop/reset options).
            names = lab.exec(target["mongo"], ["mongosh", "--port=27027", "--quiet", "--eval",
                "JSON.stringify(db.adminCommand({listDatabases:1}).databases.map(x=>x.name).filter(x=>!['admin','local','config'].includes(x)))"])
            require(json.loads(names) == [], "Restore Mongo instance not empty.")
            for name, port, db in (("application", "55432", "stockpilot_ci"), ("keycloak", "55433", "stockpilot_test_keycloak")):
                count = lab.exec(target[name], ["psql", "-X", "-U", "ci", "-p", port, "-d", db, "-At", "-c",
                    "SELECT count(*) FROM pg_tables WHERE schemaname NOT IN ('pg_catalog','information_schema')"])
                require(count.strip() == b"0", "Restore PostgreSQL target not empty.")
            for name, filename in (("mongo", "mongo.archive.gz"), ("application", "application.dump"), ("keycloak", "keycloak.dump")):
                require(digest((Path(directory) / filename).read_bytes()) == archives[name]["sha256"], "Archive digest mismatch.")
            lab.archive(target["mongo"], ["mongorestore", "--port=27027", "--quiet", "--archive", "--gzip",
                                         "--nsInclude=" + database + ".*"], mongo_dump, restore=True)
            for name, port, db in (("application", "55432", "stockpilot_ci"), ("keycloak", "55433", "stockpilot_test_keycloak")):
                lab.archive(target[name], ["pg_restore", "-U", "ci", "-p", port, "-d", db,
                            "--single-transaction", "--exit-on-error"], Path(directory) / (name + ".dump"), restore=True)
            for path in states["source"].iterdir():
                shutil.copyfile(path, states["restore"] / path.name)
                (states["restore"] / path.name).chmod(0o666)
            phase("restored-snapshot", "snapshot", side="restore")
            restored = json.loads((states["restore"] / "snapshot.json").read_text())
            compare_capture(before, restored, restore_role="restore_admin")
            require(json.loads((states["source"] / "physical-columns.json").read_text()) == {"dropped_column_slots": 1}
                    and json.loads((states["restore"] / "physical-columns.json").read_text()) == {"dropped_column_slots": 0},
                    "Physical dropped-column compaction not exercised.")
            report["backup"] = {
                "archives": archives, "logical_before_after_restore_equal": True,
                "mongo_collections": len(before["mongo"]),
                "application_tables": len([k for k in before["application"] if not k.startswith("_")]),
                "keycloak_tables": len([k for k in before["keycloak"] if not k.startswith("_")]),
                "facts": before, "independent_tmpfs": True, "restore_ports_published": False,
                "roles": "globals restored into a fresh owner; only the extra restore_admin role is normalized",
                "physical_column_compaction": {"source_dropped_slots": 1, "restored_dropped_slots": 0,
                                               "logical_columns_identical": True},
            }
            lab.keycloak("restored", networks["restore"], keycloak, kc_env, realm, cert, key, importing=False)
            phase("wait-restored", "wait-keycloak", side="restore")
            phase("restored-rollback", "rollback", build["images"]["candidate"]["image_id"], side="restore")
            phase("browser-before", "browser-before", side="restore")
            report["phases"]["historical-browser"] = lab.browser(
                browser_build, build["images"]["candidate"]["image_id"], networks["restore"],
                states["restore"], restore_probe_env, config, cert, key, probe, guards, contract)
            phase("browser-after", "browser-after", side="restore")
            phase("restored-current", "return", side="restore")
            report["success"] = True
        except Exception as error:
            report["failure"] = {"stage": lab.stage, "type": type(error).__name__,
                                 "detail": str(error) if isinstance(error, QualificationError) else "qualification failed"}
        finally:
            try:
                report["cleanup"] = lab.cleanup()
            except Exception:
                report["success"] = False
                report["cleanup"] = {"success": False, "detail": "Owned resource cleanup incomplete"}
            output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: report[k] for k in ("success", "failure", "cleanup") if k in report}), flush=True)
    require(report["success"], "Qualification failed; inspect bounded report.")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-report", type=Path, required=True)
    parser.add_argument("--browser-build-report", type=Path, required=True)
    parser.add_argument("--current-image", required=True)
    parser.add_argument("--keycloak-image", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    require(not args.output.exists(), "Existing report must not be overwritten.")
    run(json.loads(args.build_report.read_text()), args.current_image, args.keycloak_image, args.output,
        json.loads(args.browser_build_report.read_text()))


if __name__ == "__main__":
    try:
        main()
    except (QualificationError, subprocess.TimeoutExpired):
        raise SystemExit("Schema6 qualification NO-GO; no production action.") from None
