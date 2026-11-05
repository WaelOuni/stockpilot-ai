"""Verify a private OCI Docker-save archive without extracting or loading it."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import tarfile

try:
    from .schema6_rollback import BASE, APPROVED, QualificationError, require
except ImportError:
    from schema6_rollback import BASE, APPROVED, QualificationError, require


def validate_members(members):
    names = [m.name for m in members]
    require(len(names) == len(set(names)), "Duplicate archive entry.")
    for m in members:
        p = PurePosixPath(m.name)
        require(not p.is_absolute() and ".." not in p.parts and "\\" not in m.name
                and (m.isdir() or m.isfile()) and not m.issym() and not m.islnk(),
                "Unsafe archive entry.")
        require(m.isdir() or m.name in {"oci-layout", "index.json", "manifest.json"}
                or re.fullmatch(r"blobs/sha256/[0-9a-f]{64}", m.name), "Unexpected archive content.")


def verify(archive, build, qualification):
    candidate = build["images"]["candidate"]
    require(build["base_commit"] == BASE and build["migration_commit"] == APPROVED
            and qualification["success"] is True
            and qualification["base_commit"] == BASE
            and qualification["images"]["candidate"] == candidate["image_id"]
            and qualification["cleanup"]["preexisting_resources_removed"] == 0
            and qualification["ports_published"] is False,
            "Successful qualification of the same candidate required.")
    required = {"upgrade", "unchanged-v023-refused", "rollback", "negative", "return-current",
                "restored-rollback", "historical-browser", "restored-current"}
    require(required <= set(qualification["phases"])
            and qualification["backup"]["logical_before_after_restore_equal"] is True,
            "Incomplete rollback/restore campaign.")
    with tarfile.open(archive, "r:") as t:
        members = t.getmembers()
        validate_members(members)
        for m in members:
            if m.isfile() and m.name.startswith("blobs/sha256/"):
                require(hashlib.file_digest(t.extractfile(m), "sha256").hexdigest() == m.name.rsplit("/", 1)[1],
                        "OCI blob checksum mismatch.")
        def read_json(name):
            require(t.getmember(name).size <= 1024 * 1024, "Unbounded metadata.")
            return json.load(t.extractfile(name))
        def descriptor(d):
            require(re.fullmatch(r"sha256:[0-9a-f]{64}", d["digest"]) is not None, "Invalid descriptor digest.")
            path = "blobs/" + d["digest"].replace(":", "/")
            require(t.getmember(path).size == d["size"], "Descriptor size mismatch.")
            return read_json(path)
        require(read_json("oci-layout") == {"imageLayoutVersion": "1.0.0"}, "OCI layout required.")
        index = read_json("index.json")
        require(index["schemaVersion"] == 2 and len(index["manifests"]) == 1, "One candidate index required.")
        root = index["manifests"][0]
        require(root["digest"] == candidate["image_id"], "Archive does not contain the qualified image index.")
        manifests = descriptor(root)["manifests"]
        runnable = [d for d in manifests if d.get("platform") == {"architecture": "amd64", "os": "linux"}]
        require(len(runnable) == 1, "Exactly one linux/amd64 manifest required.")
        image = descriptor(runnable[0])
        config = descriptor(image["config"])
        require(config["architecture"] == "amd64" and config["os"] == "linux"
                and config["config"]["User"] == "10001:10001"
                and all(config["config"].get("Labels", {}).get(k) == v for k, v in candidate["labels"].items()),
                "Image runtime identity/provenance differs.")
        for manifest in manifests:
            doc = descriptor(manifest)
            descriptor(doc["config"])
            for layer in doc["layers"]:
                path = "blobs/" + layer["digest"].replace(":", "/")
                require(t.getmember(path).size == layer["size"], "Layer descriptor differs.")
            if manifest != runnable[0]:
                require(manifest.get("platform") == {"architecture": "unknown", "os": "unknown"}
                        and manifest.get("annotations", {}).get("vnd.docker.reference.type") == "attestation-manifest"
                        and manifest["annotations"].get("vnd.docker.reference.digest") == runnable[0]["digest"],
                        "Unexpected additional image.")
        legacy = read_json("manifest.json")
        require(len(legacy) == 1 and legacy[0]["RepoTags"] == [candidate["tag"]]
                and legacy[0]["Config"] == "blobs/" + image["config"]["digest"].replace(":", "/")
                and legacy[0]["Layers"] == ["blobs/" + d["digest"].replace(":", "/") for d in image["layers"]],
                "Docker compatibility manifest differs.")
    with Path(archive).open("rb") as stream:
        archive_sha = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"archive_sha256": archive_sha, "bytes": Path(archive).stat().st_size,
            "oci_index": root["digest"], "linux_amd64_manifest": runnable[0]["digest"],
            "config_digest": image["config"]["digest"], "tag": candidate["tag"],
            "context_sha256": candidate["context_sha256"], "base_commit": BASE,
            "migration_commit": APPROVED, "qualified": True,
            "distribution": "private OCI archive; no GHCR publication claimed"}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--archive", type=Path, required=True)
    p.add_argument("--build-report", type=Path, required=True)
    p.add_argument("--qualification", type=Path, required=True)
    args = p.parse_args()
    print(json.dumps(verify(args.archive, json.loads(args.build_report.read_text()),
                            json.loads(args.qualification.read_text())), indent=2))


if __name__ == "__main__":
    try:
        main()
    except (QualificationError, KeyError, ValueError, tarfile.TarError):
        raise SystemExit("Private rollback archive refused; do not distribute/load.") from None
