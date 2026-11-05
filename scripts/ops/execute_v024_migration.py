"""Fail-closed host admission for the one approved v021 Mongo transition.

Run only after reviewed installation, fresh restore and explicit writer quiescence.
Never stops/starts services, repairs history, restores data or accepts a URI.
"""
import argparse
from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from uuid import uuid4

try:
    from . import recovery_contract as contract
    from . import v024_installation_migration as migration
except ImportError:  # private installed /operations entrypoint
    import recovery_contract as contract
    import v024_installation_migration as migration

ROOT = Path("/opt/stockpilot")
PROJECT = "stockpilot-v021"
SSH_FINGERPRINT = "SHA256:fzvqCkWH0YJQSuvXBbSH6ZFwFZP+yaGLCqBMkw/iPOo"
SERVICES = {"bff", "proxy", "mongo", "test-postgres", "keycloak", "keycloak-db"}
CODE = {"execute_v024_migration.py", "v024_installation_migration.py", "recovery_contract.py"}
AUTHORIZATION = "MIGRATE STOCKPILOT-B2B-v021 0005 0006"
PLAN_FIELDS = {
    "format", "installation", "ssh_fingerprint", "authorization", "approved_sha",
    "authorized_at", "expires_at", "external_writers_checked_at", "compose_sha256",
    "operator_config_sha256", "code_sha256", "backup", "manifest_sha256",
    "restore_proof", "restore_sha256", "runtime_image", "rollback_context_sha256", "docker_engine",
}
require = migration.require


def command(args):
    result = subprocess.run(args, capture_output=True, timeout=180)
    require(result.returncode == 0, "Host precondition command failed: " + args[0])
    return result.stdout


def private_file(path, root=ROOT):
    path = Path(path)
    require(path.is_absolute() and path.resolve() == path and path.is_relative_to(root),
            "Private path outside installation or symlink.")
    info = path.stat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1
            and stat.S_IMODE(info.st_mode) == 0o600, "Expected root-owned private file.")
    return path


def timestamp(text):
    result = datetime.fromisoformat(text)
    require(result.tzinfo is not None, "Timezone required.")
    return result


def plan_shape(plan, now):
    require(set(plan) == PLAN_FIELDS and plan["format"] == "stockpilot-v024-migration-plan-v1",
            "Incomplete migration admission plan.")
    require(plan["installation"] == migration.INSTALLATION
            and plan["ssh_fingerprint"] == SSH_FINGERPRINT
            and plan["authorization"] == AUTHORIZATION, "Installation/authorization mismatch.")
    require(re.fullmatch("[0-9a-f]{40}", plan["approved_sha"]) is not None,
            "Approved exact commit required.")
    begin, end = timestamp(plan["authorized_at"]), timestamp(plan["expires_at"])
    require(begin <= now < end <= begin + timedelta(hours=2), "Authorization outside bounded window.")
    require(begin <= timestamp(plan["external_writers_checked_at"]) <= now
            and now - timestamp(plan["external_writers_checked_at"]) <= timedelta(minutes=10),
            "External writer attestation stale.")
    for name in ("compose_sha256", "operator_config_sha256", "manifest_sha256",
                 "restore_sha256", "rollback_context_sha256"):
        require(re.fullmatch("[0-9a-f]{64}", plan[name]) is not None, "Independent SHA256 missing.")
    require(set(plan["code_sha256"]) == CODE and all(re.fullmatch("[0-9a-f]{64}", v)
            for v in plan["code_sha256"].values()), "Operator provenance incomplete.")
    require(re.fullmatch("sha256:[0-9a-f]{64}", plan["runtime_image"]) is not None,
            "Loaded immutable candidate image identity required.")


def restore_admission(plan, manifest, proof, now):
    captured = datetime.strptime(manifest["captured_at"], "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    require(timestamp(plan["authorized_at"]) <= captured <= now
            and now - captured < timedelta(hours=2), "Fresh paired backup from this window required.")
    require(manifest["database"] == migration.DATABASE
            and manifest["vm_installation_id"] == migration.INSTALLATION
            and manifest["schema"]["version"] == 4, "Expected v4 source backup.")
    require(proof.get("status") == "logical_restore_verified"
            and proof.get("backup") == plan["backup"]
            and proof.get("schema_version") == 4
            and all(proof.get(k) is True for k in ("all_counts_and_hashes_equal",
                 "all_structures_equal", "source_archives_unchanged", "no_published_ports")),
            "Successful isolated restoration proof required.")
    require(captured <= timestamp(proof["restored_at"]) <= now, "Invalid restoration date.")
    restored = proof.get("restore_ids", {})
    require(set(restored) == {"mongo", "test-postgres", "keycloak-db"}
            and len(set(restored.values())) == 3
            and set(restored.values()).isdisjoint(manifest["source_ids"].values()),
            "Restoration source/target overlap.")


def inspect_sources(manifest):
    ids = command(["docker", "compose", "-p", PROJECT, "-f", str(ROOT / "private/compose.json"),
                   "ps", "-aq"]).decode().split()
    require(6 <= len(ids) <= 20, "Unexpected installation container inventory.")
    records = json.loads(command(["docker", "inspect", *ids]))
    oneoffs = [r for r in records if r["Config"]["Labels"].get("com.docker.compose.oneoff") == "True"]
    require(all(r["State"]["Status"] == "exited" and not r["State"]["Running"]
                and r["Config"]["Labels"].get("com.docker.compose.project") == PROJECT
                and r["Config"]["Labels"].get("stockpilot.installation")
                    == migration.INSTALLATION.replace("-", "") for r in oneoffs),
            "Historical administrative container is active or has foreign ownership.")
    records = [r for r in records if r not in oneoffs]
    require(len(records) == 6, "Expected exactly six regular installation services.")
    sources = {r["Config"]["Labels"].get("com.docker.compose.service"): r for r in records}
    require(set(sources) == SERVICES, "Unexpected source inventory.")
    for name, row in sources.items():
        labels = row["Config"]["Labels"]
        require(row["Id"] == manifest["source_ids"][name]
                and labels.get("com.docker.compose.project") == PROJECT
                and labels.get("stockpilot.installation") == migration.INSTALLATION.replace("-", ""),
                "Source identity drift.")
        volumes = sorted(m["Name"] for m in row["Mounts"] if m["Type"] == "volume")
        require(volumes == sorted(manifest["source_volumes"][name]), "Source volume drift.")
        if name in {"bff", "keycloak"}:
            require(row["State"]["Status"] == "exited" and not row["State"]["Running"],
                    "All application and Keycloak writers must already be stopped.")
        else:
            require(row["State"]["Running"] and row["State"].get("Health", {}).get("Status") == "healthy",
                    "Database/proxy health precondition failed.")
        if name in {"mongo", "test-postgres", "keycloak-db"}:
            require(not row["HostConfig"].get("PortBindings"), "Database published ports refused.")
    expected = {r["Id"] for r in sources.values()}
    for name in ("application", "identity"):
        network = json.loads(command(["docker", "network", "inspect", PROJECT + "_" + name]))[0]
        require(set(network.get("Containers", {})) <= expected, "Unknown network client; external writer not excluded.")
    return sources


def admit(plan):
    now = datetime.now(UTC)
    plan_shape(plan, now)
    require(os.geteuid() == 0 and Path("/etc/stockpilot-installation-id").read_text().strip()
            == migration.INSTALLATION, "Host installation/root mismatch.")
    for name, expected in plan["code_sha256"].items():
        require(contract.file_sha(private_file(ROOT / "operations" / name)) == expected,
                "Installed operator provenance differs.")
    require(contract.file_sha(private_file(ROOT / "private/compose.json")) == plan["compose_sha256"]
            and contract.file_sha(private_file(ROOT / "private/operator.json")) == plan["operator_config_sha256"],
            "Effective configuration drift.")
    context = json.loads(command(["docker", "context", "inspect"]))[0]["Endpoints"]["docker"]["Host"]
    require(context == "unix:///var/run/docker.sock"
            and os.environ.get("DOCKER_HOST", context) == context and not os.environ.get("DOCKER_TLS_VERIFY"),
            "Only the local Docker engine is admitted.")
    require(command(["docker", "info", "--format", "{{.ID}}"]).decode().strip() == plan["docker_engine"],
            "Docker engine identity drift.")
    backup = Path(plan["backup"])
    require(backup.parent == ROOT / "backups" and backup.resolve() == backup,
            "Backup outside fixed installation.")
    manifest = contract.verify_backup(backup, plan["manifest_sha256"],
        installation=migration.INSTALLATION.replace("-", ""), active_root=ROOT)
    proof_file = private_file(plan["restore_proof"])
    require(proof_file.parent == backup and contract.file_sha(proof_file) == plan["restore_sha256"],
            "Independent restoration proof mismatch.")
    restore_admission(plan, manifest, json.loads(proof_file.read_text()), now)
    inspect_sources(manifest)
    image = json.loads(command(["docker", "image", "inspect", plan["runtime_image"]]))[0]
    labels = image["Config"].get("Labels", {})
    require(image["Id"] == plan["runtime_image"] and image["Os"] == "linux"
            and image["Architecture"] == "amd64"
            and labels.get("org.opencontainers.image.source") == "https://github.com/WaelOuni/stockpilot-ai"
            and labels.get("org.opencontainers.image.revision") == "aedb58fe91be092a6de1923e4c00e53c5ca089f2"
            and labels.get("io.stockpilot.rollback.migrations") == "b17f1e8364eaa2dc75036ddde3247bbd3bb86f4e"
            and labels.get("io.stockpilot.rollback.context") == plan["rollback_context_sha256"],
            "Qualified rollback runtime provenance mismatch.")
    return manifest


def runtime(plan):
    """Inside the isolated one-shot candidate; configuration only from protected mounts."""
    import runpy
    import contextlib
    import io
    from pymongo import MongoClient
    from urllib.parse import quote
    plan_shape(plan, datetime.now(UTC))
    for name, expected in plan["code_sha256"].items():
        require(contract.file_sha(Path("/operations") / name) == expected, "Runtime code differs.")
    cfg_path = Path("/run/stockpilot/operator.json")
    require(contract.file_sha(cfg_path) == plan["operator_config_sha256"], "Runtime configuration drift.")
    cfg = json.loads(cfg_path.read_text())
    require(cfg["database"] == migration.DATABASE
            and cfg["vm_installation_id"] == migration.INSTALLATION, "Runtime target mismatch.")
    # Run the exact qualified read-only collector in its real dependency context.
    def capture():
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            runpy.run_path("/operations/recovery_facts.py")
        result = json.loads(stream.getvalue())
        contract.validate_capture(result)
        return result
    before = capture()
    expected = json.loads(Path("/run/stockpilot/backup-facts.json").read_text())
    # Strict by default, including Keycloak. A changed capture requires a new
    # reviewed paired backup, never a blanket runtime-table exclusion here.
    contract.compare_capture(expected, before)
    import psycopg
    for host, port, user, database, key in (
        ("test-postgres", 55432, "ci", "stockpilot_ci", "pg_password"),
        ("keycloak-db", 5432, "keycloak", "keycloak", "keycloak_db_password"),
    ):
        with psycopg.connect(host=host, port=port, user=user, password=cfg[key], dbname=database) as connection:
            connection.execute("SET TRANSACTION READ ONLY")
            require(connection.execute("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() "
                    "AND backend_type='client backend' AND pid<>pg_backend_pid()").fetchone()[0] == 0,
                    "Unexpected PostgreSQL writer connection.")
    uri = "mongodb://operator:" + quote(cfg["mongo_password"], safe="") + "@mongo:27017/?authSource=admin&replicaSet=rs0&directConnection=true"
    with MongoClient(uri, tz_aware=True, serverSelectionTimeoutMS=10000,
                     appname="stockpilot-v024-installation-migration") as client:
        result = migration.transition(client[cfg["database"]])
    after = capture()
    # The register changes; all other captured business state remains byte-exact.
    migration.assert_business_equal(
        {n: {"count": v["count"], "data": v["data"],
             "structure": contract.sha(json.dumps([v["options"], v["indexes"]], sort_keys=True).encode())}
         for n, v in before["mongo"].items() if n not in {migration.HISTORY, migration.LOCKS}},
        {n: {"count": v["count"], "data": v["data"],
             "structure": contract.sha(json.dumps([v["options"], v["indexes"]], sort_keys=True).encode())}
         for n, v in after["mongo"].items() if n not in {migration.HISTORY, migration.LOCKS}},
    )
    require(before["application"] == after["application"] and before["keycloak"] == after["keycloak"],
            "PostgreSQL changed during migration; keep writers stopped.")
    return {"status": "verified", "result": result, "postgres_unchanged": True,
            "approved_sha": plan["approved_sha"], "completed_at": datetime.now(UTC).isoformat()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path)
    parser.add_argument("--container", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.container:
        require(args.plan == Path("/run/stockpilot/plan.json"), "Fixed container admission mount required.")
        print(json.dumps(runtime(json.loads(args.plan.read_text()))))
        return
    path = private_file(args.plan)
    require(path.is_relative_to(ROOT / "evidence"), "Plan must reside in private evidence.")
    plan = json.loads(path.read_text())
    lock_fd = contract.operation_lock(ROOT / "private/recovery-operation.lock")
    try:
        manifest = admit(plan)
        name = PROJECT + "-v024-migration-" + uuid4().hex[:16]
        output = path.with_name(name + ".json")
        require(not output.exists(), "No proof overwrite.")
        binds = [
            (ROOT / "operations", "/operations"),
            (ROOT / "private/operator.json", "/run/stockpilot/operator.json"),
            (Path("/etc/stockpilot-installation-id"), "/run/stockpilot/vm-id"),
            (path, "/run/stockpilot/plan.json"),
            (Path(plan["backup"]) / "facts.json", "/run/stockpilot/backup-facts.json"),
        ]
        args = ["docker", "create", "--name", name, "--network", PROJECT + "_application",
                "--label", "io.stockpilot.migration.owner=" + name, "--read-only", "--tmpfs", "/tmp",
                "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--user", "0", "--memory", "512m"]
        for source, dest in binds:
            args += ["--mount", "type=bind,src=" + str(source) + ",dst=" + dest + ",readonly"]
        cid = command([*args, plan["runtime_image"], "python", "-B", "/operations/execute_v024_migration.py",
                       "/run/stockpilot/plan.json", "--container"]).decode().strip()
        command(["docker", "network", "connect", PROJECT + "_identity", cid])
        # Recheck source writers immediately before start, allowing only our new
        # stopped container which is not yet a connected writer.
        sources = {n: r for n, r in zip(manifest["source_ids"], json.loads(command(
            ["docker", "inspect", *manifest["source_ids"].values()])))}
        require(all(sources[n]["State"]["Status"] == "exited" for n in ("bff", "keycloak")),
                "A writer resumed before migration.")
        result = subprocess.run(["docker", "start", "-a", cid], capture_output=True, timeout=420)
        status = json.loads(command(["docker", "inspect", cid]))[0]
        proof = {"container": cid, "plan_sha256": contract.file_sha(path), "success": False}
        if result.returncode == 0 and status["State"]["ExitCode"] == 0:
            proof["result"] = json.loads(result.stdout)
            proof["success"] = proof["result"].get("status") == "verified"
        with output.open("x") as stream:
            os.chmod(output, 0o600)
            json.dump(proof, stream, indent=2)
        print(json.dumps({"proof": str(output), "sha256": contract.file_sha(output),
                          "success": proof["success"], "services_resumed": False}))
        require(proof["success"], "Migration incomplete/uncertain; retain container and writer quiescence.")
        # Keep the exited container as private evidence; separate verified cleanup.
    finally:
        os.close(lock_fd)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Driver errors can contain credentials or private records.
        message = str(error) if isinstance(error, (migration.TransitionRefused, contract.ContractError)) else type(error).__name__
        print(json.dumps({"status": "NO-GO", "reason": message, "services_resumed": False}), file=sys.stderr)
        raise SystemExit(1) from None
