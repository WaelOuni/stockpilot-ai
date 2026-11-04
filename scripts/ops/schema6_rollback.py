"""Local-only v0.2.3 API candidate recognizing the approved Mongo v6 schema.

Only immutable Git blobs enter the temporary build context. No working files,
Git writes, database connection, registry push or production action.
"""
import argparse
import ast
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile

BASE = "aedb58fe91be092a6de1923e4c00e53c5ca089f2"
TAG_OBJECT = "11abe403588a3eaa7a27bd4e2aec56622d4b5212"
APPROVED = "b17f1e8364eaa2dc75036ddde3247bbd3bb86f4e"
SOURCE = "https://github.com/WaelOuni/stockpilot-ai"
PREFIX = "src/stockpilot/infrastructure/persistence/mongodb/"
RUNNER = PREFIX + "schema_migrations.py"
ADDITIONS = frozenset({
    "b2b_order_archives", "b2b_order_archive_operations", "b2b_contact_requests",
    "b2b_contact_replies", "b2b_contact_operations",
})
HASHES = {
    RUNNER: "d8ab260918a7165dd88ef06e723bf6346601cbf9172118244f5ca3af79236417",
    PREFIX + "migrations/0005_b2b_order_archives.json": "cef28bc9a1b42157299475168ca9268f82afa7146703d07b41622db049a253ec",
    PREFIX + "migrations/0006_b2b_contact.json": "b76e9c8a5ca86adcddea9ce4d106bc3d218e32bae015cbfd77b40e7a51146146",
}


class QualificationError(RuntimeError):
    """Bounded diagnostic, never raw command output."""


def require(condition, message):
    if not condition:
        raise QualificationError(message)


def command(args, *, cwd=None, timeout=900, input=None):
    result = subprocess.run(args, cwd=cwd, input=input, capture_output=True, timeout=timeout)
    require(result.returncode == 0, "Command failed: " + args[0])
    return result.stdout


def digest(value):
    return hashlib.sha256(value).hexdigest()


def git(repo, *args):
    return command(["git", *args], cwd=repo)


def allowed_path(path):
    p = PurePosixPath(path)
    return (
        not p.is_absolute() and ".." not in p.parts and "\\" not in path
        and not any(part.startswith(".env") or part == "__pycache__" for part in p.parts)
        and (path in {"Dockerfile", ".dockerignore", "requirements.lock"}
             or path.startswith("src/stockpilot/"))
    )


def allowlist_only(old, new):
    """Prove no executable statement changed outside the closed allowlist."""
    trees = [ast.parse(value) for value in (old, new)]
    values = []
    for tree in trees:
        assignments = [n for n in tree.body if isinstance(n, ast.Assign)
                       and any(isinstance(t, ast.Name) and t.id == "B2B_COLLECTIONS" for t in n.targets)]
        require(len(assignments) == 1, "Unique B2B allowlist required.")
        node = assignments[0].value
        require(isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "frozenset" and len(node.args) == 1
                and not node.keywords, "Closed literal allowlist required.")
        values.append(frozenset(ast.literal_eval(node.args[0])))
        assignments[0].value = ast.Constant(value="verified-allowlist")
    require(values[1] - values[0] == ADDITIONS and values[0] <= values[1],
            "Unexpected allowlist change.")
    require(ast.dump(trees[0]) == ast.dump(trees[1]), "Runner logic changed outside allowlist.")


def source_files(repo):
    require(git(repo, "rev-parse", "v0.2.3").decode().strip() == TAG_OBJECT, "Wrong tag object.")
    require(git(repo, "cat-file", "-t", TAG_OBJECT).strip() == b"tag", "Annotated tag required.")
    require(git(repo, "rev-parse", "v0.2.3^{}").decode().strip() == BASE, "Wrong v0.2.3 commit.")
    files = {}
    tree = git(repo, "ls-tree", "-rz", BASE, "Dockerfile", ".dockerignore", "requirements.lock", "src/stockpilot")
    for entry in tree.split(b"\0"):
        if not entry:
            continue
        meta, raw_path = entry.split(b"\t", 1)
        mode, kind, oid = meta.decode().split()
        path = raw_path.decode()
        require(kind == "blob" and mode in {"100644", "100755"} and allowed_path(path),
                "Unsafe build-context entry.")
        files[path] = git(repo, "cat-file", "blob", oid)
    require({"Dockerfile", ".dockerignore", "requirements.lock", RUNNER} <= files.keys(), "Incomplete source.")
    require(all("@sha256:" in line for line in files["Dockerfile"].decode().splitlines()
                if line.startswith("FROM ")), "Base image must stay digest pinned.")
    return files


def compatible_files(repo, baseline):
    files = dict(baseline)
    for path, expected in HASHES.items():
        content = git(repo, "show", APPROVED + ":" + path)
        require(digest(content) == expected, "Approved migration/runner fingerprint mismatch.")
        files[path] = content
    allowlist_only(baseline[RUNNER], files[RUNNER])
    require({p for p in files if files[p] != baseline.get(p)} == set(HASHES), "Unexpected adaptation.")
    return files


def fingerprint(files):
    hashes = {p: digest(v) for p, v in sorted(files.items())}
    return hashes, digest(json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode())


def inspect_image(image, files, labels):
    info = json.loads(command(["docker", "image", "inspect", image]))[0]
    require(info["Architecture"] == "amd64" and info["Os"] == "linux", "Wrong image platform.")
    require(info["Config"]["User"] == "10001:10001", "Runtime identity changed.")
    require(all(info["Config"].get("Labels", {}).get(k) == v for k, v in labels.items()), "OCI provenance mismatch.")
    code = (
        "import hashlib,json,pathlib;root=pathlib.Path('/app');"
        "print(json.dumps({str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() "
        "for p in sorted((root/'src/stockpilot').rglob('*')) if p.is_file()}))"
    )
    actual = json.loads(command(["docker", "run", "--rm", "--network", "none", "--read-only",
                                 "--entrypoint", "python", info["Id"], "-c", code]))
    expected = {p: digest(v) for p, v in files.items() if p.startswith("src/stockpilot/")}
    require(actual == expected, "Built image source tree differs from approved candidate.")
    require(re.fullmatch(r"sha256:[0-9a-f]{64}", info["Id"]), "Invalid local image identity.")
    return info["Id"]


def build(repo, *, execute=False):
    baseline = source_files(repo)
    candidate = compatible_files(repo, baseline)
    revision = git(repo, "rev-parse", "HEAD").decode().strip()
    report = {"base_commit": BASE, "tag_object": TAG_OBJECT, "migration_commit": APPROVED,
              "qualification_commit": revision, "adaptations": HASHES, "images": {}}
    for name, files in (("baseline", baseline), ("candidate", candidate)):
        hashes, context = fingerprint(files)
        entry = {"context_sha256": context, "source_files": hashes}
        report["images"][name] = entry
        if not execute:
            continue
        tag = "stockpilot-rollback-" + name + ":" + context[:16]
        exists = subprocess.run(["docker", "image", "inspect", tag], capture_output=True)
        require(exists.returncode != 0, "Local candidate name already exists; refuse overwrite.")
        labels = {"org.opencontainers.image.source": SOURCE, "org.opencontainers.image.revision": BASE,
                  "org.opencontainers.image.description": "Local schema6 rollback qualification; not an official release",
                  "io.stockpilot.rollback.migrations": APPROVED if name == "candidate" else BASE, "io.stockpilot.rollback.context": context,
                  "io.stockpilot.rollback.qualification": revision}
        with tempfile.TemporaryDirectory(prefix="stockpilot-rollback-build-") as directory:
            for path, content in files.items():
                target = Path(directory) / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
            args = ["docker", "build", "--platform", "linux/amd64", "-t", tag]
            for key, value in labels.items():
                args += ["--label", key + "=" + value]
            command([*args, directory])
        entry.update(tag=tag, image_id=inspect_image(tag, files, labels), labels=labels)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", action="store_true", help="Build locally; never push.")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    require(not args.output.exists(), "Refuse to overwrite an existing proof.")
    report = build(Path(__file__).resolve().parents[2], execute=args.build)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"base_commit": BASE, "migration_commit": APPROVED,
                      "images": {k: {x: v[x] for x in ("context_sha256", "image_id") if x in v}
                                 for k, v in report["images"].items()}}))


if __name__ == "__main__":
    try:
        main()
    except (QualificationError, subprocess.TimeoutExpired):
        raise SystemExit("Rollback build refused/failed; no publication performed.") from None
