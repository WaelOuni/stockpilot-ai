"""Execute pinned private recovery operators against a fresh synthetic installation.

Only target literals are relocated into a new temporary directory/project.
Never reads a VM configuration or defaults to an existing installation.
"""
import argparse
import base64
import ast
import copy
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.ops.prepare_recovery_patch import prepare
from scripts.ops.recovery_contract import file_sha, require, source_provenance, verify_backup
from scripts.ci.qualify_schema6_rollback import local_engine

REPO = Path(__file__).resolve().parents[2]
INSTALLATION = "cce6d56f-3202-4008-bad2-37b01d70e130"
MONGO = "mongo:8@sha256:d731d77bfd7afd66bd487bdf627b5bf7ce4c3602ec461d635977021db529ebbc"
PG = "pgvector/pgvector:pg16@sha256:7b822b0aac60967beb1ea5e576b8602c94c300a157d187f385ae3e0da199b90a"
KC_PG = "postgres:15-alpine@sha256:dfcf0459185089e88a43197975780f5a3078acd5ece84824a14c9d6fbbab02d0"


def call(args, *, input=None, timeout=300, check=True):
    result = subprocess.run(args, input=input, capture_output=True, timeout=timeout)
    if check:
        require(result.returncode == 0, "Owned laboratory command failed: " + args[0])
    return result


def docker(*args, **kwargs):
    return call(["docker", *args], **kwargs)


def rows(*args):
    return json.loads(docker(*args).stdout)


def write(path, value, mode=0o600):
    require(not path.exists(), "Fixture destination already exists.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value if isinstance(value, str) else json.dumps(value, indent=2))
    path.chmod(mode)
    return path


def relocate(source, root, project):
    """Change target string literals only; preserve every operator instruction."""
    class Targets(ast.NodeTransformer):
        def visit_Constant(self, node):
            if isinstance(node.value, str):
                node.value = node.value.replace("/opt/stockpilot", str(root)).replace(
                    "/etc/stockpilot-installation-id", str(root / "installation-id")).replace(
                    "stockpilot-v021", project)
            return node
    tree = Targets().visit(ast.parse(source))
    ast.fix_missing_locations(tree)
    result = ast.unparse(tree) + "\n"
    require(ast.dump(ast.parse(result)) == ast.dump(tree), "Relocation changed operator logic.")
    return result


def immutable(image):
    row = rows("image", "inspect", image)[0]
    require(row["Os"] == "linux" and row["Architecture"] == "amd64", "Wrong image platform.")
    candidates = row.get("RepoDigests", [])
    require(candidates, "Local manifest-addressable image required; config ID is insufficient.")
    return candidates[0]


class Fixture:
    def __init__(self, root, api, web, keycloak, prepared, migration_image=None):
        self.root = root
        self.project = "stockpilot-oplab-" + uuid4().hex[:16]
        self.images = {"api": immutable(api), "web": immutable(web),
                       "keycloak": immutable(keycloak), "mongo": MONGO,
                       "postgres": PG, "keycloak-db": KC_PG}
        self.migration_image = immutable(migration_image) if migration_image else None
        self.removed = []
        self.before = {kind: set(docker(*args).stdout.decode().splitlines()) for kind, args in {
            "containers": ("ps", "-aq", "--no-trunc"), "volumes": ("volume", "ls", "-q"),
            "networks": ("network", "ls", "-q", "--no-trunc")}.items()}
        self.cfg = {"purpose": "fresh-demo-v021", "installation": INSTALLATION.replace("-", ""),
                    "vm_installation_id": INSTALLATION,
                    "database": "stockpilot_b2b_test_" + uuid4().hex,
                    "mongo_password": secrets.token_urlsafe(32),
                    "pg_password": secrets.token_urlsafe(32),
                    "keycloak_db_password": secrets.token_urlsafe(32)}
        self.production_hashes = {}
        for name in ("backup_fresh.py", "restore_isolated.py", "recovery_facts.py", "recovery_contract.py"):
            source = (prepared / name).read_text()
            self.production_hashes[name] = file_sha(prepared / name)
            write(root / "operations" / name, relocate(source, root, self.project))
        if self.migration_image:
            for name in ("execute_v024_migration.py", "v024_installation_migration.py"):
                write(root / "operations" / name, (REPO / "scripts/ops" / name).read_text())
        write(root / "installation-id", INSTALLATION)
        write(root / "private/operator.json", self.cfg)
        write(root / "inputs/images.json", self.images)
        self.secrets = {"app_mongo_password": secrets.token_urlsafe(32)}
        for name, value in {"secrets.json": self.secrets, "credentials.json": {},
                            "bff.json": {"fixture": "synthetic-only"}, "ca.key": "synthetic unused CA key"}.items():
            write(root / "private" / name, value)
        self.root.joinpath("backups").mkdir(mode=0o700)
        self.root.joinpath("evidence").mkdir(mode=0o700)

    @property
    def compose(self):
        return ["docker", "compose", "-f", str(self.root / "private/compose.json")]

    def services(self):
        ids = call(self.compose + ["ps", "-aq"]).stdout.decode().split()
        return {r["Config"]["Labels"]["com.docker.compose.service"]: r for r in rows("inspect", *ids)}

    def exec_python(self, code):
        cid = self.services()["bff"]["Id"]
        return docker("exec", "-i", cid, "python", "-B", "-", input=code.encode())

    def configure(self, version):
        p = self.root / "private"
        call(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2",
              "-keyout", str(p / "tls.key"), "-out", str(p / "tls.crt"),
              "-subj", "/CN=keycloak", "-addext", "subjectAltName=DNS:keycloak,DNS:proxy"])
        for n in ("tls.key", "tls.crt"):
            (p / n).chmod(0o600)
        write(p / "ca.crt", (p / "tls.crt").read_text())
        write(p / "realm.json", {"realm": "operator-lab", "enabled": True, "sslRequired": "all",
                                 "registrationAllowed": False})
        write(p / "proxy.conf", """server {
 listen 8443 ssl; server_name proxy;
 ssl_certificate /inputs/tls.crt; ssl_certificate_key /inputs/tls.key;
 access_log off;
 location / { proxy_pass http://bff:8000; }
}
""")
        writer = r'''import json,threading,time
from pathlib import Path
from http.server import HTTPServer,BaseHTTPRequestHandler
from pymongo import MongoClient
c=json.loads(Path("/run/stockpilot/operator.json").read_text())
db=MongoClient("mongodb://operator:"+c["mongo_password"]+"@mongo:27017/?authSource=admin&replicaSet=rs0&directConnection=true")[c["database"]]
def write():
 while True:
  try: db.operator_lifecycle.update_one({"_id":"writer"},{"$inc":{"ticks":1}},upsert=True)
  except Exception: pass
  time.sleep(.3)
class Handler(BaseHTTPRequestHandler):
 def do_GET(self): self.send_response(200);self.end_headers();self.wfile.write(b"synthetic writer ready")
 def log_message(self,*args): pass
threading.Thread(target=write,daemon=True).start()
HTTPServer(("0.0.0.0",8000),Handler).serve_forever()
'''
        write(self.root / "operations/fixture_writer.py", writer)
        labels = {"stockpilot.installation": self.cfg["installation"], "stockpilot.qualification": self.project}
        health = lambda cmd: {"test": ["CMD-SHELL", cmd], "interval": "2s", "timeout": "3s", "retries": 90}
        services = {
            "mongo": {"image": self.images["mongo"], "environment": {
                "MONGO_INITDB_ROOT_USERNAME": "operator", "MONGO_INITDB_ROOT_PASSWORD": self.cfg["mongo_password"],
                "REPLICA_KEY": base64.b64encode(secrets.token_bytes(512)).decode()},
                "entrypoint": ["/bin/bash", "-ec"],
                "command": ['printf %s "$$REPLICA_KEY" >/tmp/replica-key; chown 999:999 /tmp/replica-key; chmod 600 /tmp/replica-key; exec docker-entrypoint.sh mongod --replSet rs0 --keyFile /tmp/replica-key --bind_ip_all'],
                "volumes": ["mongo-data:/data/db", "mongo-config:/data/configdb"],
                "networks": ["application"], "healthcheck": health("mongosh --quiet --eval 'if (!db.hello().isWritablePrimary) quit(1)'")},
            "test-postgres": {"image": self.images["postgres"], "command": ["postgres", "-p", "55432"],
                "environment": {"POSTGRES_USER": "ci", "POSTGRES_PASSWORD": self.cfg["pg_password"], "POSTGRES_DB": "stockpilot_ci"},
                "volumes": ["postgres-data:/var/lib/postgresql/data"], "networks": ["application"],
                "healthcheck": health("pg_isready -p 55432 -U ci -d stockpilot_ci")},
            "keycloak-db": {"image": self.images["keycloak-db"],
                "environment": {"POSTGRES_USER": "keycloak", "POSTGRES_PASSWORD": self.cfg["keycloak_db_password"], "POSTGRES_DB": "keycloak"},
                "volumes": ["keycloak-data:/var/lib/postgresql/data"], "networks": ["identity"],
                "healthcheck": health("pg_isready -U keycloak -d keycloak")},
            "keycloak": {"image": self.images["keycloak"], "user": "0", "command": ["start", "--import-realm"],
                "environment": {"KC_DB": "postgres", "KC_DB_URL": "jdbc:postgresql://keycloak-db:5432/keycloak",
                    "KC_DB_USERNAME": "keycloak", "KC_DB_PASSWORD": self.cfg["keycloak_db_password"],
                    "KC_HOSTNAME": "https://keycloak:8443", "KC_HTTP_ENABLED": "false",
                    "KC_HTTPS_CERTIFICATE_FILE": "/inputs/tls.crt", "KC_HTTPS_CERTIFICATE_KEY_FILE": "/inputs/tls.key",
                    "KC_HEALTH_ENABLED": "true"},
                "volumes": [str(p / "realm.json") + ":/opt/keycloak/data/import/lab.json:ro",
                    str(p / "tls.crt") + ":/inputs/tls.crt:ro", str(p / "tls.key") + ":/inputs/tls.key:ro"],
                "networks": ["identity"], "healthcheck": health("bash -c 'exec 3<>/dev/tcp/127.0.0.1/8443'")},
            "bff": {"image": self.images["api"], "user": "0",
                "command": ["python", "-B", "/operations/fixture_writer.py"],
                "volumes": [str(p / "operator.json") + ":/run/stockpilot/operator.json:ro",
                            str(self.root / "operations") + ":/operations:ro", str(p / "ca.crt") + ":/inputs/ca.crt:ro"],
                "networks": ["application", "identity"],
                "healthcheck": health("python -c \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/',timeout=2)\"")},
            "proxy": {"image": self.images["web"], "user": "0", "entrypoint": ["nginx"], "command": ["-g", "daemon off;"],
                "volumes": [str(p / "proxy.conf") + ":/etc/nginx/conf.d/default.conf:ro",
                            str(p / "tls.crt") + ":/inputs/tls.crt:ro", str(p / "tls.key") + ":/inputs/tls.key:ro"],
                "networks": ["application"], "healthcheck": health("nginx -t")}}
        for service in services.values():
            service.update({"labels": labels, "restart": "no", "mem_limit": "768m", "cpus": 1})
        services["keycloak"]["mem_limit"] = "1200m"
        cfg = {"name": self.project, "services": services,
               "networks": {"application": {"internal": True}, "identity": {"internal": True}},
               "volumes": {n: {"labels": labels} for n in ("mongo-data", "mongo-config", "postgres-data", "keycloak-data")}}
        write(p / "compose.json", cfg)
        call(self.compose + ["up", "-d", "--pull", "never", "mongo", "test-postgres", "keycloak-db"])
        mongo = self.services()["mongo"]["Id"]
        auth = "const a=db.getSiblingDB('admin');if(!a.auth(process.env.MONGO_INITDB_ROOT_USERNAME,process.env.MONGO_INITDB_ROOT_PASSWORD))throw Error('auth');"
        for _ in range(90):
            if docker("exec", mongo, "mongosh", "--quiet", "--eval", auth + "print(1)", check=False).returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError("Synthetic Mongo bootstrap deadline.")
        docker("exec", mongo, "mongosh", "--quiet", "--eval",
               auth + "rs.initiate({_id:'rs0',members:[{_id:0,host:'mongo:27017'}]})")
        call(self.compose + ["up", "-d", "--pull", "never", "--wait", "--wait-timeout", "180"], timeout=240)
        self.seed(version)
        self.healthy()

    def seed(self, version):
        source = r'''import json,datetime,re
from pathlib import Path
from pymongo import MongoClient
from bson import ObjectId,Int64
import psycopg
from stockpilot.infrastructure.persistence.mongodb.schema_migrations import registry,MigrationRunner
cfg=json.loads(Path("/run/stockpilot/operator.json").read_text())
client=MongoClient("mongodb://operator:"+cfg["mongo_password"]+"@mongo:27017/?authSource=admin&replicaSet=rs0&directConnection=true")
db=client[cfg["database"]]
migrations=registry()[:VERSION]
MigrationRunner(db,migrations=migrations).apply(confirmation=cfg["database"])
def value(spec):
 if "enum" in spec:return spec["enum"][0]
 kind=spec.get("bsonType","object")
 if isinstance(kind,list):kind=next(k for k in kind if k!="null")
 if kind=="object":return {k:value(spec["properties"][k]) for k in spec.get("required",[])}
 if kind=="array":return [value(spec["items"]) for _ in range(max(1,spec.get("minItems",0)))]
 if kind=="objectId":return ObjectId()
 if kind=="date":return datetime.datetime.now(datetime.UTC)
 if kind=="bool":return True
 if kind in ("int","long","double","decimal"):return max(1,spec.get("minimum",0))
 if kind=="string":
  pattern=spec.get("pattern","")
  if "{64}" in pattern:return "a"*64
  if "{32}" in pattern:return "a"*32
  return "synthetic"
 raise RuntimeError("Unhandled synthetic BSON kind")
for m in migrations:
 if m.version in (5,6):
  for d in m.definition["collections"]:
   validator=d["options"]["validator"]["$jsonSchema"]
   db[d["collection"]].insert_one(value(validator))
db.operator_probe.insert_one({"_id":"typed","int32":1,"int64":Int64(1),"double":1.0,"binary":b"synthetic"})
db.command("createUser","stockpilot_bff",pwd="APP_PASSWORD",roles=[{"role":"readWrite","db":cfg["database"]}])
with psycopg.connect(host="test-postgres",port=55432,user="ci",password=cfg["pg_password"],dbname="stockpilot_ci") as c:
 c.execute("CREATE SCHEMA stockpilot_b2b")
 c.execute("CREATE TABLE stockpilot_b2b.assistant_turns (id integer primary key,status text not null)")
 c.execute("INSERT INTO stockpilot_b2b.assistant_turns VALUES (1,'completed')")
 c.execute("CREATE TABLE public.operator_probe (id integer primary key, dropped text, exact numeric(38,12), payload bytea, CHECK(id>0))")
 c.execute("INSERT INTO public.operator_probe VALUES (1,'gone',12345678901234567890123456.123456789123,decode('0123abcd','hex'))")
 c.execute("ALTER TABLE public.operator_probe DROP COLUMN dropped")
 c.execute("CREATE SEQUENCE public.operator_sequence START 53")
 c.execute("CREATE ROLE operator_reader NOLOGIN")
 c.execute("GRANT SELECT ON public.operator_probe TO operator_reader")
print(json.dumps({"schema":VERSION,"synthetic":True}))
'''.replace("VERSION", str(version)).replace("APP_PASSWORD", self.secrets["app_mongo_password"])
        self.exec_python(source)

    def healthy(self):
        for _ in range(120):
            services = self.services()
            if len(services) == 6 and all(r["State"].get("Health", {}).get("Status") == "healthy"
                                        for r in services.values()):
                probe = r'''import ssl,urllib.request,json
ctx=ssl.create_default_context(cafile="/inputs/ca.crt")
assert urllib.request.urlopen("https://proxy:8443/",context=ctx,timeout=5).status==200
d=json.load(urllib.request.urlopen("https://keycloak:8443/realms/operator-lab/.well-known/openid-configuration",context=ctx,timeout=5))
assert d["issuer"]=="https://keycloak:8443/realms/operator-lab"
print("HTTPS_DISCOVERY_VALIDATED")
'''
                try:
                    self.exec_python(probe)
                    return
                except Exception:
                    pass
            time.sleep(1)
        raise RuntimeError("Source operational return deadline.")

    def operator(self, name, *args, fail=False, env=None):
        result = subprocess.run(["/usr/bin/python3", "-B", str(self.root / "operations" / name), *args],
                                capture_output=True, timeout=600, env=env)
        # Preserve diagnostics privately; never print driver errors or credentials.
        log = self.root / "evidence" / (name + "-" + uuid4().hex + ".log")
        log.write_bytes(result.stdout + result.stderr); log.chmod(0o600)
        require((result.returncode != 0) if fail else (result.returncode == 0),
                "Unexpected private operator result: " + name)
        return result.stdout.decode()

    def backup(self, *, fail=False, env=None):
        before = set((self.root / "backups").iterdir())
        time.sleep(1.1)  # The pinned operator uses a one-second directory identity.
        output = self.operator("backup_fresh.py", fail=fail, env=env)
        added = set((self.root / "backups").iterdir()) - before
        self.healthy()
        if fail:
            return {"expected_failure": True, "partial_directories": len(added)}
        require(len(added) == 1, "Ambiguous fresh backup.")
        backup = added.pop()
        require("BACKUP_CREATED " + str(backup) in output, "Backup path not attested.")
        sha = file_sha(backup / "manifest.json")
        require("MANIFEST_SHA256 " + sha in output, "Independent manifest SHA absent.")
        m = verify_backup(backup, sha, installation=self.cfg["installation"], active_root=self.root)
        return backup, sha, m

    def test(self, version):
        self.configure(version)
        # The host entrypoint is the system interpreter, not the application's venv.
        host = call(["/usr/bin/python3", "-B", "-c",
                     "import sys;sys.path.insert(0," + repr(str(self.root / "operations")) + ");import recovery_contract"])
        require(host.returncode == 0, "Host standard-library contract import failed.")
        f = self.root / "operations/recovery_facts.py"
        import_probe = f.read_text().split("cfg =", 1)[0]  # relocated AST output
        self.exec_python("import sys;sys.path.insert(0,'/operations')\n" + import_probe + "\nprint('CONTAINER_IMPORTS_OK')")
        import fcntl
        lock = self.root / "private/recovery-operation.lock"
        with lock.open("w") as stream:
            lock.chmod(0o600)
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            before = {n: r["State"]["StartedAt"] for n, r in self.services().items()}
            self.operator("backup_fresh.py", fail=True)
            require(before == {n: r["State"]["StartedAt"] for n, r in self.services().items()},
                    "Lock refusal interrupted a source service.")
        backup, sha, manifest = self.backup()
        # Provenance and archive corruption fail before restoring any data.
        cfg = self.root / "private/proxy.conf"
        saved = cfg.read_bytes()
        cfg.write_bytes(saved + b"\n# synthetic drift\n")
        try:
            self.operator("restore_isolated.py", str(backup), sha, fail=True)
        finally:
            cfg.write_bytes(saved)
        payload = backup / "mongo.archive.gz"
        saved = payload.read_bytes()
        payload.write_bytes(saved + b"corrupt")
        try:
            self.operator("restore_isolated.py", str(backup), sha, fail=True)
        finally:
            payload.write_bytes(saved)
        self.operator("restore_isolated.py", str(backup), sha)
        evidence = sorted(backup.glob("restore-verification-*.json"))
        require(len(evidence) == 1, "Restore proof missing.")
        proof = json.loads(evidence[0].read_text())
        require(proof["all_counts_and_hashes_equal"] and proof["all_structures_equal"]
                and proof["schema_version"] == version and proof["no_published_ports"],
                "Incomplete private restore qualification.")
        migration_proof = self.migration_probe(backup, proof) if version == 4 and self.migration_image else None
        self.healthy()
        # Fail an actual dump after Mongo capture and prove the finally resumes writers.
        shim = self.root / "shim"
        shim.mkdir(mode=0o700)
        cid = self.services()["test-postgres"]["Id"]
        executable = shutil.which("docker")
        shim_source = ("#!/usr/bin/python3\nimport os,sys\n"
                       "if 'exec' in sys.argv and " + repr(cid) + " in sys.argv and 'pg_dump' in sys.argv:sys.exit(73)\n"
                       "os.execv(" + repr(executable) + ",[" + repr(executable) + "]+sys.argv[1:])\n")
        write(shim / "docker", shim_source, 0o700)
        failed = self.backup(fail=True, env={**os.environ, "PATH": str(shim) + os.pathsep + os.environ["PATH"]})
        return {"success": True, "schema": version, "manifest_sha256": sha,
                "archives": manifest["archives"], "restore": {k: proof[k] for k in (
                    "mongo_collections", "application_tables", "keycloak_tables",
                    "all_counts_and_hashes_equal", "all_structures_equal", "no_published_ports")},
                "checks": ["real host/container imports", "exclusive lock before interruption",
                           "actual paired backup", "provenance drift refused", "corrupt archive refused",
                           "actual independent three-database restore", "PostgreSQL Keycloak 15",
                           "partial backup failure and safe service recovery", "HTTPS and discovery valid"],
                "partial_backup": failed, "migration_runtime": migration_proof,
                "production_candidate_hashes": self.production_hashes,
                "test_target_only_relocation": source_provenance(self.root)}

    def migration_probe(self, backup, proof):
        """Exercise the installed container entrypoint on the restored synthetic DBs."""
        from datetime import UTC, datetime, timedelta
        from scripts.ops.execute_v024_migration import AUTHORIZATION, CODE, SSH_FINGERPRINT
        network = proof["project"] + "_restore"
        require(network.startswith(self.project + "-restore-"), "Foreign restore network.")
        for cid in proof["restore_ids"].values():
            row = rows("inspect", cid)[0]
            require(cid not in self.before["containers"] and set(row["NetworkSettings"]["Networks"]) == {network},
                    "Migration laboratory target not independently restored.")
        # The sole extra restore_admin role has already been explicitly compared
        # by the recovery contract. Include it in this target's pre-transition facts.
        facts = json.loads((backup / "facts.json").read_text())
        from scripts.ops.recovery_contract import BOOTSTRAP_ROLE_ATTRIBUTES
        for scope in ("application", "keycloak"):
            require("restore_admin" not in facts[scope]["_roles"], "Ambiguous bootstrap role.")
            facts[scope]["_roles"]["restore_admin"] = BOOTSTRAP_ROLE_ATTRIBUTES
        expected = write(self.root / "private/migration-facts.json", facts)
        now = datetime.now(UTC)
        plan = {"format": "stockpilot-v024-migration-plan-v1", "installation": INSTALLATION,
                "ssh_fingerprint": SSH_FINGERPRINT, "authorization": AUTHORIZATION,
                "approved_sha": "0" * 40, "authorized_at": now.isoformat(),
                "expires_at": (now + timedelta(minutes=30)).isoformat(),
                "external_writers_checked_at": now.isoformat(),
                "compose_sha256": file_sha(self.root / "private/compose.json"),
                "operator_config_sha256": file_sha(self.root / "private/operator.json"),
                "code_sha256": {n: file_sha(self.root / "operations" / n) for n in CODE},
                "backup": str(backup), "manifest_sha256": file_sha(backup / "manifest.json"),
                "restore_proof": "synthetic-host-admission-tested-separately", "restore_sha256": "0" * 64,
                "runtime_image": rows("image", "inspect", self.migration_image)[0]["Id"],
                "rollback_context_sha256": "0" * 64, "docker_engine": "synthetic-only"}
        plan_file = write(self.root / "private/migration-plan.json", plan)
        probe = r'''import json,sys
from pathlib import Path
from pymongo import MongoClient
sys.path.insert(0,"/operations")
import execute_v024_migration as host
cfg=json.loads(Path("/run/stockpilot/operator.json").read_text())
assert cfg["database"] == EXPECTED_DATABASE
assert cfg["database"].startswith("stockpilot_b2b_test_") and cfg["database"] != host.migration.DATABASE
client=MongoClient("mongodb://operator:"+cfg["mongo_password"]+"@mongo:27017/?authSource=admin&replicaSet=rs0&directConnection=true",tz_aware=True)
assert not client[cfg["database"]].list_collection_names(filter={"name":"demo_instance"})
r=client.admin.command("replSetGetConfig")["config"]
# Relocate only fixed identity constants in this test process. The production
# operator exposes no such options and retains its hard-coded real server.
host.migration.DATABASE=cfg["database"]
host.migration.REPLICA_ID=str(r["settings"]["replicaSetId"])
host.migration.MEMBERS=tuple((m["_id"],m["host"]) for m in r["members"])
result=host.runtime(json.loads(Path("/run/stockpilot/plan.json").read_text()))
assert result["status"]=="verified" and result["result"]["applied"]==[5,6]
print(json.dumps({"real_container_entrypoint":True,"schema":6,"postgres_unchanged":result["postgres_unchanged"],
                  "business_data_unchanged":result["result"]["existing_business_data_equal"]}))
'''.replace("EXPECTED_DATABASE", repr(self.cfg["database"]))
        script = write(self.root / "operations/fixture_migration_probe.py", probe)
        args = ["run", "--name", self.project + "-migration-probe", "--network", network,
                "--read-only", "--tmpfs", "/tmp", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                "--user", "0", "--memory", "512m"]
        for source, destination in (
            (self.root / "operations", "/operations"), (self.root / "private/operator.json", "/run/stockpilot/operator.json"),
            (self.root / "installation-id", "/run/stockpilot/vm-id"),
            (expected, "/run/stockpilot/backup-facts.json"), (plan_file, "/run/stockpilot/plan.json"),
        ):
            args += ["-v", str(source) + ":" + destination + ":ro"]
        result = docker(*args, self.migration_image, "python", "-B", "/operations/fixture_migration_probe.py", check=False)
        log = self.root / "evidence/migration-runtime.log"
        log.write_bytes(result.stdout + result.stderr); log.chmod(0o600)
        require(result.returncode == 0, "Real migration container entrypoint failed.")
        result = json.loads(result.stdout)
        require(result["schema"] == 6 and result["postgres_unchanged"] and result["business_data_unchanged"],
                "Migration runtime qualification incomplete.")
        return result


    def cleanup(self):
        # Snapshot difference is necessary but not sufficient: also require the unique
        # project/name, image identity, networks and owned bind paths before removal.
        all_ids = docker("ps", "-aq", "--no-trunc").stdout.decode().splitlines()
        for cid in reversed(all_ids):
            if cid in self.before["containers"]:
                continue
            row = rows("inspect", cid)[0]
            name = row["Name"].lstrip("/")
            if not name.startswith(self.project + "-"):
                continue
            require(not row["HostConfig"].get("PortBindings"), "Unexpected laboratory port.")
            require(all(n.startswith(self.project + "_") or n.startswith(self.project + "-restore-")
                        for n in row["NetworkSettings"]["Networks"]), "Foreign network on laboratory resource.")
            for mount in row["Mounts"]:
                if mount["Type"] == "bind":
                    require(Path(mount["Source"]).resolve().is_relative_to(self.root), "Foreign laboratory mount.")
                elif mount["Type"] == "volume":
                    require(mount["Name"] not in self.before["volumes"]
                            and mount["Name"].startswith(self.project), "Foreign laboratory volume.")
            require(row["Config"]["Image"] in {*self.images.values(), self.migration_image}, "Foreign laboratory image.")
            docker("rm", "-f", cid)
            self.removed.append({"container": cid})
        for kind, listing in (("network", ("network", "ls", "-q", "--no-trunc")), ("volume", ("volume", "ls", "-q"))):
            previous = self.before["networks" if kind == "network" else "volumes"]
            for identifier in docker(*listing).stdout.decode().splitlines():
                if identifier in previous:
                    continue
                row = rows(kind, "inspect", identifier)[0]
                if not row["Name"].startswith(self.project):
                    continue
                labels = row.get("Labels", {}) or {}
                require(labels.get("com.docker.compose.project", "").startswith(self.project),
                        "Resource ownership label missing.")
                if kind == "network":
                    require(row.get("Internal") and not row.get("Containers"), "Nonempty or external laboratory network.")
                docker(kind, "rm", identifier)
                self.removed.append({kind: identifier})
        for kind, args in {"containers": ("ps", "-aq", "--no-trunc"), "volumes": ("volume", "ls", "-q"),
                           "networks": ("network", "ls", "-q", "--no-trunc")}.items():
            require(self.before[kind] <= set(docker(*args).stdout.decode().splitlines()),
                    "Preexisting resource was removed.")
        return {"removed": self.removed, "preexisting_resources_removed": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-directory", type=Path, required=True)
    parser.add_argument("--api-image", required=True)
    parser.add_argument("--web-image", required=True)
    parser.add_argument("--keycloak-image", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--migration-image", help="Qualified local candidate for the restored v4 transition test")
    parser.add_argument("--schema", type=int, choices=(4, 6), action="append")
    args = parser.parse_args()
    require(not args.output.exists(), "Existing qualification proof.")
    require(os.geteuid() == 0, "Run the disposable fixture as root, matching the VM operator and 0600 root-owned mounts.")
    local_engine()
    prepared, _ = prepare(args.source_directory)
    proof = {"success": False, "source": "synthetic local fixture only", "phases": []}
    for version in (args.schema or (4, 6)):
        # Retain private diagnostics on failure. No VM configurations are used.
        root = Path(tempfile.mkdtemp(prefix="stockpilot-private-oplab-"))
        lab = Fixture(root, args.api_image, args.web_image, args.keycloak_image, prepared, args.migration_image)
        phase = {"schema": version, "private_directory": str(root), "success": False}
        try:
            phase.update(lab.test(version))
        except Exception as error:
            phase["failure"] = {"type": type(error).__name__, "message": str(error)[:240]}
            for cid in docker("ps", "-aq", "--no-trunc").stdout.decode().splitlines():
                if cid in lab.before["containers"]:
                    continue
                record = rows("inspect", cid)[0]
                if record["Name"].lstrip("/").startswith(lab.project + "-"):
                    diagnostic = docker("logs", "--tail", "120", cid, check=False)
                    path = root / "evidence" / (cid[:12] + ".log")
                    path.write_bytes(diagnostic.stdout + diagnostic.stderr); path.chmod(0o600)

        finally:
            try:
                phase["cleanup"] = lab.cleanup()
            except Exception as error:
                phase["success"] = False
                phase["cleanup"] = {"success": False, "type": type(error).__name__, "message": str(error)[:240]}
            proof["phases"].append(phase)
            args.output.write_text(json.dumps(proof, indent=2) + "\n"); args.output.chmod(0o600)
        print(json.dumps({"schema": version, "success": phase["success"], "private_directory": str(root),
                          "failure": phase.get("failure"), "cleanup_count": len(lab.removed)}), flush=True)
        require(phase["success"], "Private operator qualification failed; no VM installation.")
    proof["success"] = True
    args.output.write_text(json.dumps(proof, indent=2) + "\n")
    print("PRIVATE_OPERATORS_QUALIFIED_SYNTHETICALLY", flush=True)


if __name__ == "__main__":
    main()
