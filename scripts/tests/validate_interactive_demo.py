"""Disposable end-to-end validation of the canonical interactive lifecycle.

Never uses the default persistent state path. Optional Windows Node uses an
isolated Chrome profile and exact certificate SPKI, not Windows trust changes.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.b2b_interactive import (
    PURPOSE,
    ROOT,
    DemoError,
    InteractiveDemo,
    command,
    volume_identity,
)
from scripts.tests.validate_bff_keycloak import docker_snapshot

PROBE = "import json,hashlib\nfrom pathlib import Path\nfrom bson import json_util\nfrom pymongo import MongoClient\nimport psycopg\nfrom psycopg import sql\nc=json.loads(Path('/run/stockpilot/operator.json').read_text())\ndatabase='stockpilot_b2b_test_'+c['installation']\nuri='mongodb://operator:'+c['mongo_password']+'@mongo:27017/?authSource=admin&replicaSet=rs0&directConnection=true'\nwith MongoClient(uri,tz_aware=True) as client:\n db=client[database]\n data={n:sorted(json_util.dumps(d,sort_keys=True) for d in db[n].find({})) for n in db.list_collection_names() if not n.startswith('bff_')}\n stocks=[{'org':d['organization_id'],'quantity':d['quantity'],'reserved':d['reserved'],'revision':d['revision']} for d in db.b2b_products.find({}).sort('organization_id',1)]\n orders=db.b2b_orders.count_documents({})\n operations=db.b2b_order_operations.count_documents({'namespace':'confirm'})\npg='postgresql://ci:'+c['pg_password']+'@test-postgres:55432/stockpilot_ci'\nwith psycopg.connect(pg) as conn:\n names=[r[0] for r in conn.execute(\"SELECT table_name FROM information_schema.tables WHERE table_schema='stockpilot_b2b' ORDER BY table_name\")]\n for name in names:\n  rows=conn.execute(sql.SQL('SELECT row_to_json(t)::text FROM {} t ORDER BY 1').format(sql.Identifier('stockpilot_b2b',name))).fetchall()\n  data['pg.'+name]=[r[0] for r in rows]\n checkpoints=conn.execute('SELECT count(*) FROM stockpilot_b2b.checkpoints').fetchone()[0]\nprint(json.dumps({'data_sha256':hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest(),'stocks':stocks,'orders':orders,'confirm_operations':operations,'checkpoints':checkpoints}))\n"


def snapshot(owner):
    return json.loads(
        owner.compose(
            "--profile",
            "operator",
            "run",
            "--rm",
            "--no-deps",
            "--name",
            owner.manifest["project"] + "-operator",
            "operator",
            "python",
            "-c",
            PROBE,
        ).splitlines()[-1]
    )


def browser(owner, phase, windows_node=None):
    env = os.environ.copy()
    script = ROOT / "frontend/b2b-lifecycle-browser.mjs"
    if windows_node:
        executable = windows_node
        script_path = command(["wslpath", "-w", str(script)])
        state_path = command(["wslpath", "-w", str(owner.state)])
        env["WSLENV"] = env.get("WSLENV", "") + ":SP_DEMO_TEST_STATE:SP_DEMO_TEST_PHASE"
    else:
        executable = shutil.which("node")
        script_path, state_path = str(script), str(owner.state)
    env.update(SP_DEMO_TEST_STATE=state_path, SP_DEMO_TEST_PHASE=phase)
    result = subprocess.run(
        [executable, script_path],
        cwd=ROOT / "frontend",
        env=env,
        capture_output=True,
        timeout=600,
        check=False,
    )
    report = json.loads((owner.state / "browser-report.json").read_text())
    if result.returncode or not report.get(phase + "_passed"):
        raise RuntimeError(
            "Real browser failed at "
            + report.get("error_stage", "launch")
            + "; inspect private test report."
        )
    return report


def cleanup_disposable(owner, baseline_ids, baseline_volumes):
    """Only this test runner can remove its explicitly created temporary volumes."""
    state = owner.state
    if (
        not owner.ephemeral
        or state.parent != Path("/tmp")
        or not state.name.startswith("stockpilot-v139-test-")
    ):
        raise RuntimeError("Disposable cleanup target required.")
    m = json.loads((state / "manifest.json").read_text())
    project = m["project"]
    if project != "stockpilot-b2b-local-test-" + m["installation"][:12]:
        raise RuntimeError("Disposable project identity mismatch.")
    if m.get("phase") != "ready":
        raise RuntimeError(
            "Partial initialization retained; no ownership guessed for cleanup."
        )
    owner.load()
    owner.attest()
    ids = command(
        [
            "docker",
            "ps",
            "-aq",
            "--filter",
            "label=com.docker.compose.project=" + project,
        ]
    ).split()
    for cid in ids:
        row = json.loads(command(["docker", "inspect", cid]))[0]
        labels = row["Config"]["Labels"]
        if (
            cid in baseline_ids
            or labels.get("stockpilot.audit.purpose") != PURPOSE
            or labels.get("com.docker.compose.project.config_files")
            != str(state / "compose.json")
        ):
            raise RuntimeError("Foreign container cleanup refused.")
        for mount in row["Mounts"]:
            if mount["Type"] == "bind" and (
                Path(mount["Source"]).parent != state or mount["RW"]
            ):
                raise RuntimeError("Foreign bind refused.")
    # At this point only the positively identified test project can be stopped.
    command(
        [
            "docker",
            "compose",
            "--env-file",
            "/dev/null",
            "-f",
            str(state / "compose.json"),
            "-p",
            project,
            "--profile",
            "*",
            "down",
            "--timeout",
            "100",
        ]
    )
    for name in command(
        [
            "docker",
            "volume",
            "ls",
            "-q",
            "--filter",
            "label=com.docker.compose.project=" + project,
        ]
    ).split():
        row = json.loads(command(["docker", "volume", "inspect", name]))[0]
        if (
            name in baseline_volumes
            or volume_identity(row) != m["volumes"].get(name)
            or row["Labels"].get("stockpilot.audit.purpose") != PURPOSE
            or command(["docker", "ps", "-aq", "--filter", "volume=" + name])
        ):
            raise RuntimeError("Foreign/referenced volume cleanup refused.")
        command(["docker", "volume", "rm", name])
    for tag, expected in m.get("built_images", {}).items():
        row = json.loads(command(["docker", "image", "inspect", tag]))[0]
        if (
            row["Id"] != expected
            or row["Config"].get("Labels", {}).get("stockpilot.audit.purpose")
            != PURPOSE
            or command(["docker", "ps", "-aq", "--filter", "ancestor=" + expected])
        ):
            raise RuntimeError("Foreign/referenced image cleanup refused.")
        command(["docker", "image", "rm", tag])


def windows_settings():
    ps = Path("/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe")
    if not ps.exists():
        return None
    text = command(
        [
            str(ps),
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "$h=[BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash([IO.File]::ReadAllBytes('C:\\Windows\\System32\\drivers\\etc\\hosts'))).Replace('-','');$r=@();foreach($l in @('CurrentUser','LocalMachine')){$s=[Security.Cryptography.X509Certificates.X509Store]::new('Root',$l);$s.Open('ReadOnly');foreach($c in $s.Certificates){$r+=$c.Thumbprint};$s.Close()};[Array]::Sort($r);'{\"hosts\":\"'+$h+'\",\"roots\":\"'+($r -join ',')+'\"}'",
        ]
    )
    import hashlib

    return hashlib.sha256(text.encode()).hexdigest()


def run(windows_node=None):
    baseline_windows = windows_settings()
    baseline = docker_snapshot()
    baseline_volumes = set(command(["docker", "volume", "ls", "-q"]).split())
    baseline_networks = set(command(["docker", "network", "ls", "-q"]).split())
    state = Path("/tmp") / ("stockpilot-v139-test-" + uuid4().hex)
    owner = InteractiveDemo(state, ephemeral=True)
    evidence = ROOT / "tmp" / ("v139-" + uuid4().hex)
    evidence.mkdir(parents=True, mode=0o700)
    report = {"passed": False, "cleanup": False, "checks": [], "state": str(state)}

    def check(text):
        report["checks"].append(text)

    try:
        with owner.locked():
            report["init"] = owner.init(
                os.environ["STOCKPILOT_DEMO_OLLAMA_URL"],
                os.environ["STOCKPILOT_DEMO_OLLAMA_CONTAINER_URL"],
            )
            assert not report["init"]["running"]
            check(
                "init prepares authenticated stores and stable synthetic accounts, then leaves all services stopped"
            )
            assert owner.init("", "")["unchanged"]
            check("repeated init never resets or regenerates")
            # The actual Windows preflight is recorded; automation uses an exact test-only SPKI.
            report["windows_preflight"] = owner.status()["windows"]
            owner.start(require_windows=False)
            status = owner.status()
            for account in json.loads((state / "credentials.json").read_text())[
                "accounts"
            ]:
                assert account["password"] not in json.dumps(status)
            check(
                "status shows schemas, service health, Ollama, capacity and account names without credentials"
            )
            first = browser(owner, "before", windows_node)
            report["browser"] = first
            before = snapshot(owner)
            assert (
                before["stocks"][0]["reserved"] == 1
                and before["stocks"][1]["reserved"] == 0
            )
            assert before["confirm_operations"] == 1 and before["checkpoints"] > 0
            report["before_restart"] = before
            private_hashes = owner.manifest["files"].copy()
            volume_ids = owner.manifest["volumes"].copy()
            owner.stop()
            assert not any(row["State"]["Running"] for row in owner.attest().values())
            assert owner.stop()["volumes_preserved"] == 4
            check(
                "stop and repeated stop preserve four volumes, every container identity, secrets and certificates"
            )
            assert owner.status()["schema"]["live_check"].startswith("unavailable")
            owner.start(require_windows=False)
            assert snapshot(owner) == before
            assert (
                owner.manifest["files"] == private_hashes
                and owner.manifest["volumes"] == volume_ids
            )
            check(
                "resume preserves full durable Mongo/application-PG hash, reserved order, idempotence, accounts, conversation/checkpoints"
            )
            times = {n: v["State"]["StartedAt"] for n, v in owner.attest().items()}
            owner.start(require_windows=False)
            assert {
                n: v["State"]["StartedAt"] for n, v in owner.attest().items()
            } == times
            check("start on an active installation does not restart any service")
            # An externally interrupted ingress must be resumable without resetting stores.
            proxy = owner.attest()["proxy"]["Id"]
            command(["docker", "exec", proxy, "nginx", "-s", "quit"])
            for _ in range(30):
                if not json.loads(
                    command(["docker", "inspect", "--format", "{{json .State}}", proxy])
                )["Running"]:
                    break
                time.sleep(1)
            owner.start(require_windows=False)
            assert snapshot(owner) == before
            check(
                "controlled ingress interruption resumes without changing durable data"
            )
            # Refuse a missing secret without trying to create or start anything.
            secret = state / "bff.json"
            hidden = state / "bff.hidden"
            secret.rename(hidden)
            try:
                try:
                    owner.start(require_windows=False)
                except DemoError:
                    pass
                else:
                    raise AssertionError("Missing secret accepted.")
            finally:
                hidden.rename(secret)
            owner.load()
            check(
                "missing secret refuses resume; restored original secret needs no reset"
            )
            report["browser"] = browser(owner, "after", windows_node)
            after = snapshot(owner)
            assert (
                after["stocks"][0]["quantity"] == 9
                and after["stocks"][0]["reserved"] == 0
            )
            assert (
                after["stocks"][1]["quantity"] == 2
                and after["stocks"][1]["reserved"] == 0
            )
            assert after["confirm_operations"] == 1 and after["orders"] == 1
            report["after_browser"] = after
            check(
                "durable Mongo confirms single reservation/replay/cancel and A adjustment/import, B untouched"
            )
            owner.stop()
            report["passed"] = True
    except Exception as error:  # noqa: BLE001 - redact test/cleanup failures, preserve evidence
        report["error_type"] = type(error).__name__
        # Never serialize raw driver exceptions, URLs or credentials.
    finally:
        if state.exists() and (state / "manifest.json").exists():
            try:
                cleanup_disposable(owner, baseline, baseline_volumes)
                report["cleanup"] = True
            except Exception as error:  # noqa: BLE001 - redact test/cleanup failures, preserve evidence
                report["cleanup_error_type"] = type(error).__name__
        after = docker_snapshot()
        report["protected_containers_unchanged"] = all(
            after.get(cid) == value for cid, value in baseline.items()
        )
        report["volumes_unchanged"] = baseline_volumes == set(
            command(["docker", "volume", "ls", "-q"]).split()
        )
        report["windows_settings_measured"] = baseline_windows is not None
        report["windows_hosts_and_trust_unchanged"] = (
            baseline_windows == windows_settings()
            if baseline_windows is not None
            else None
        )
        report["networks_unchanged"] = baseline_networks == set(
            command(["docker", "network", "ls", "-q"]).split()
        )
        if (state / "browser-report.json").exists():
            shutil.copyfile(
                state / "browser-report.json", evidence / "browser-report.json"
            )
        (evidence / "report.json").write_text(json.dumps(report, indent=2))
        for path in evidence.iterdir():
            path.chmod(0o600)
        if report["cleanup"]:
            shutil.rmtree(state)
            lock = state.with_name(state.name + ".lock")
            if (
                lock.is_file()
                and not lock.is_symlink()
                and lock.stat().st_uid == os.getuid()
            ):
                lock.unlink()
        print(
            json.dumps(
                {
                    "report": str(evidence / "report.json"),
                    "passed": report["passed"],
                    "cleanup": report["cleanup"],
                }
            )
        )
    if not all(
        report.get(k)
        for k in (
            "passed",
            "cleanup",
            "protected_containers_unchanged",
            "volumes_unchanged",
            "networks_unchanged",
            *(
                ("windows_hosts_and_trust_unchanged",)
                if windows_node or baseline_windows is not None
                else ()
            ),
        )
    ):
        raise SystemExit(1)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--windows-node",
        help="Existing Windows Node executable via /mnt/c; no installation.",
    )
    run(parser.parse_args().windows_node)
