"""Prepare pinned private-operator amendments in a NEW local temporary directory.

Never installs, executes, connects to Docker/SSH, or writes to the input folder.
The inspected private scripts are not stored in Git. The result still needs
separate operator review and explicit authorization before installation/use.
"""
import argparse
import ast
import difflib
import json
from pathlib import Path
import tempfile

from scripts.ops.recovery_contract import ContractError, require, sha

PINS = {
    "backup_fresh.py": "43d6fbaf0b87734428db561679486b39075d38d071e06ab371603fcc34933d6d",
    "restore_isolated.py": "c1cc292dfbb342ce39341e550064200490ac91c8d2011d6211f7f1041b8ddc0f",
    "recovery_facts.py": "396690a91df60da896101179b77203382bee33148821bc0473b18e509bc53672",
}


def replace_once(source, before, after):
    require(source.count(before) == 1, "Operator revision/anchor mismatch.")
    return source.replace(before, after, 1)


def amend(sources):
    """Pure source transformation. Preserve the installation and target guards."""
    result = {}
    b = sources["backup_fresh.py"]
    b = replace_once(b, "import pathlib,json,subprocess,hashlib,os,datetime",
        "import pathlib,json,subprocess,hashlib,os,datetime\n"
        "from recovery_contract import FORMAT, operation_lock, source_provenance, compare_capture, file_sha")
    anchor = 'cfg=json.loads((P/"operator.json").read_text());assert cfg["purpose"]=="fresh-demo-v021"'
    b = replace_once(b, anchor, anchor + '\n_recovery_lock=operation_lock(P/"recovery-operation.lock")\n'
                     'provenance=source_provenance(ROOT)')
    anchor = 'assert rr["bff"]["Config"]["Image"]==images["api"] and rr["proxy"]["Config"]["Image"]==images["web"]'
    b = replace_once(b, anchor, anchor + '\nfor service,image in [("keycloak","keycloak"),("mongo","mongo"),'
                     '("test-postgres","postgres"),("keycloak-db","keycloak-db")]:\n'
                     ' assert rr[service]["Config"]["Image"]==images[image]')
    b = replace_once(b, 'after=facts("after");assert before==after,"Data changed during paired capture"',
        'after=facts("after");compare_capture(before,after)\n'
        ' assert source_provenance(ROOT)==provenance,"Operator/configuration changed during capture"')
    b = replace_once(b, '"format":"stockpilot-v021-fresh-paired-v1"',
                     '"format":FORMAT,"provenance":provenance,"schema":before["schema"]')
    b = replace_once(b, 'print("BACKUP_CREATED",str(B),flush=True)',
        'print("BACKUP_CREATED",str(B),flush=True)\n'
        ' print("MANIFEST_SHA256",file_sha(B/"manifest.json"),flush=True)')
    # An explicitly requested migration window must retain the exact captured
    # Keycloak state. Default behavior and every failure still resume writers.
    b = replace_once(b, 'import pathlib,json,subprocess,hashlib,os,datetime',
                     'import pathlib,json,subprocess,hashlib,os,datetime,argparse')
    anchor = 'cfg=json.loads((P/"operator.json").read_text());assert cfg["purpose"]=="fresh-demo-v021"'
    b = replace_once(b, anchor,
        '_parser=argparse.ArgumentParser(description="Private paired recovery operator")\n'
        '_parser.add_argument("--keep-quiesced",action="store_true")\n'
        '_options=_parser.parse_args()\n' + anchor)
    b = replace_once(b, 'stopped=False', 'stopped=False;_capture_complete=False')
    b = replace_once(b, '"format":FORMAT,"provenance":provenance,',
        '"format":FORMAT,"resume_policy":("quiesced-on-success" if _options.keep_quiesced else "resume"),'
        '"provenance":provenance,')
    b = replace_once(b, ' print("MANIFEST_SHA256",file_sha(B/"manifest.json"),flush=True)',
        ' print("MANIFEST_SHA256",file_sha(B/"manifest.json"),flush=True)\n _capture_complete=True')
    b = replace_once(b, 'finally:\n if stopped:',
                     'finally:\n if stopped and (not _options.keep_quiesced or not _capture_complete):')
    b = replace_once(b, 'print("SOURCE_SIX_SERVICES_RESTARTED_HEALTHY",flush=True)',
        'print("SOURCE_WRITERS_REMAIN_QUIESCED" if _options.keep_quiesced and _capture_complete '
        'else "SOURCE_SIX_SERVICES_RESTARTED_HEALTHY",flush=True)')
    result["backup_fresh.py"] = b

    r = sources["restore_isolated.py"]
    # Refuse the official image's temporary standalone bootstrap process.
    # getCmdLineOpts exposes the CLI --replSet option as replication.replSet.
    r = replace_once(r, "auth+'print(1)'",
        "auth+\"const o=a.adminCommand({getCmdLineOpts:1});if(o.parsed?.replication?.replSet!=='rs0')quit(2);print(1)\"")
    r = replace_once(r,
        "rs.initiate({_id:'rs0',members:[{_id:0,host:'mongo:27017'}]})",
        "const r=rs.initiate({_id:'rs0',members:[{_id:0,host:'mongo:27017'}]});if(r.ok!==1)throw Error('replica initialization refused')")
    r = replace_once(r, "import json,subprocess,hashlib,os,datetime,time,secrets,copy,tarfile,sys",
        "import json,subprocess,hashlib,os,datetime,time,secrets,copy,tarfile,sys\n"
        "from recovery_contract import FORMAT, verify_backup, operation_lock, compare_capture")
    r = replace_once(r, "assert len(sys.argv)==2,'Specify one backup directory'",
        "assert len(sys.argv)==3,'Specify exact backup directory and independently recorded manifest SHA256'")
    r = replace_once(r, "m['format']=='stockpilot-v021-fresh-paired-v1'", "m['format']==FORMAT")
    line = next(x for x in r.splitlines() if x.startswith("assert m['format']"))
    r = replace_once(r, line, line + "\n_recovery_lock=operation_lock(P/'recovery-operation.lock')\n"
                     "m=verify_backup(B,sys.argv[2],installation=m['installation'],active_root=ROOT)")
    start = r.index("canonical_fallback=")
    end = r.index("\nfor n,v in m['archives'].items():assert", start)
    r = r[:start] + "compare_capture(expected,actual,restore_role='restore_admin')\n" + r[end:]
    for scope in ("application", "keycloak"):
        r = replace_once(r, "len(actual['" + scope + "'])",
                         "sum(not k.startswith('_') for k in actual['" + scope + "'])")
    r = replace_once(r, "'all_counts_and_hashes_equal':True",
                     "'all_counts_and_hashes_equal':True,'all_structures_equal':True,"
                     "'schema_version':actual['schema']['version']")
    result["restore_isolated.py"] = r

    f = sources["recovery_facts.py"]
    prefix = f[:f.index("facts={")]
    prefix = replace_once(prefix, "import json,hashlib",
        "import json,hashlib\nfrom recovery_contract import mongo_facts, postgres_facts, mongo_preconditions, EPHEMERAL")
    pg_header = next(x for x in f.splitlines() if x.startswith("for key,host,port,user,database,password"))
    pg_connect = next(x for x in f.splitlines() if x.startswith(" with psycopg.connect"))
    result["recovery_facts.py"] = prefix + (
        'facts={"mongo":mongo_facts(db),"application":{},"keycloak":{},'
        '"schema":mongo_preconditions(db),"excluded_ephemeral":sorted(EPHEMERAL)}\n'
        + pg_header + "\n" + pg_connect + "\n"
        '  facts[key]=postgres_facts(c)\n'
        '  if key=="application":assert c.execute("SELECT count(*) FROM stockpilot_b2b.assistant_turns '
        'WHERE status=\'pending\'").fetchone()[0]==0\n'
        'print(json.dumps(facts,sort_keys=True))\n'
    )
    for name, text in result.items():
        ast.parse(text, filename=name)  # compile/parse only; never import private operators
        require("/etc/stockpilot-installation-id" in text or "/run/stockpilot/vm-id" in text,
                "Installation guard lost.")
    require("'--pull','never'" in result["restore_isolated.py"]
            and "'--exit-on-error'" in result["restore_isolated.py"],
            "Restore safeguards lost.")
    return result


def prepare(source_directory):
    source_directory = Path(source_directory)
    sources = {}
    for name, expected in PINS.items():
        path = source_directory / name
        require(path.is_file() and not path.is_symlink(), "Missing/untrusted operator source.")
        raw = path.read_bytes()
        require(sha(raw) == expected, "Private operator source SHA256 mismatch.")
        sources[name] = raw.decode()
    amended = amend(sources)
    amended["recovery_contract.py"] = Path(__file__).with_name("recovery_contract.py").read_text()
    for name, source in amended.items():
        ast.parse(source, filename=name)
    output = Path(tempfile.mkdtemp(prefix="stockpilot-recovery-patch-"))
    for name, source in amended.items():
        path = output / name
        path.write_text(source)
        path.chmod(0o600)
    patch = "".join("".join(difflib.unified_diff(
        sources[name].splitlines(keepends=True), amended[name].splitlines(keepends=True),
        fromfile="before/" + name, tofile="candidate/" + name)) for name in sources)
    (output / "operators.patch").write_text(patch)
    (output / "operators.patch").chmod(0o600)
    report = {"status": "prepared-not-installed-not-executed", "source_sha256": PINS,
              "candidate_sha256": {name: sha(source.encode()) for name, source in amended.items()}}
    (output / "provenance.json").write_text(json.dumps(report, indent=2) + "\n")
    (output / "provenance.json").chmod(0o600)
    return output, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-directory", type=Path, required=True)
    args = parser.parse_args()
    path, report = prepare(args.source_directory)
    print(json.dumps({"output_directory": str(path), **report}))


if __name__ == "__main__":
    try:
        main()
    except (ContractError, OSError, SyntaxError, ValueError):
        raise SystemExit("Recovery patch refused; no installation or operator execution.") from None
