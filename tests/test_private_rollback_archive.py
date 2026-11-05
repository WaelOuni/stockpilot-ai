"""Reject corrupted or ambiguous private OCI rollback archives."""
from io import BytesIO
import hashlib
import json
from pathlib import Path
import tarfile
import pytest
from scripts.ops.schema6_rollback import BASE, APPROVED, QualificationError
from scripts.ops.verify_rollback_archive import validate_members, verify


def fixture_archive(tmp_path, change=None):
    blobs = {}
    def blob(value, media):
        data = json.dumps(value, sort_keys=True).encode()
        digest = "sha256:" + hashlib.sha256(data).hexdigest()
        blobs["blobs/" + digest.replace(":", "/")] = data
        return {"digest": digest, "size": len(data), "mediaType": media}
    labels = {"org.opencontainers.image.source": "https://github.com/WaelOuni/stockpilot-ai",
              "org.opencontainers.image.revision": BASE}
    config = blob({"architecture": "amd64", "os": "linux", "config": {"User": "10001:10001", "Labels": labels}},
                  "application/vnd.oci.image.config.v1+json")
    manifest = blob({"schemaVersion": 2, "config": config, "layers": []}, "application/vnd.oci.image.manifest.v1+json")
    manifest["platform"] = {"os": "linux", "architecture": "amd64"}
    index = blob({"schemaVersion": 2, "manifests": [manifest]}, "application/vnd.oci.image.index.v1+json")
    candidate = {"image_id": index["digest"], "tag": "candidate:synthetic", "labels": labels, "context_sha256": "1" * 64}
    build = {"base_commit": BASE, "migration_commit": APPROVED, "images": {"candidate": candidate}}
    qualification = {"base_commit": BASE, "success": True, "images": {"candidate": candidate["image_id"]},
        "cleanup": {"preexisting_resources_removed": 0}, "ports_published": False,
        "backup": {"logical_before_after_restore_equal": True},
        "phases": {p: {} for p in ("upgrade", "unchanged-v023-refused", "rollback", "negative",
                                   "return-current", "restored-rollback", "historical-browser", "restored-current")}}
    legacy = [{"Config": "blobs/" + config["digest"].replace(":", "/"),
               "RepoTags": [candidate["tag"]], "Layers": []}]
    if change == "extra-tag":
        legacy[0]["RepoTags"].append("candidate:latest")
    if change == "wrong-qualification":
        qualification["images"]["candidate"] = "sha256:" + "0" * 64
    if change == "incomplete-campaign":
        del qualification["phases"]["negative"]
    if change == "corrupt-blob":
        blobs[next(iter(blobs))] += b"corruption"
    blobs.update({"oci-layout": b'{"imageLayoutVersion":"1.0.0"}',
                  "index.json": json.dumps({"schemaVersion": 2, "manifests": [index]}).encode(),
                  "manifest.json": json.dumps(legacy).encode()})
    archive = tmp_path / "fixture.tar"
    with tarfile.open(archive, "w") as t:
        for name, data in blobs.items():
            item = tarfile.TarInfo(name)
            item.size = len(data)
            t.addfile(item, BytesIO(data))
    return archive, build, qualification


def test_valid_archive_identifies_manifest_config_and_independent_file_sha(tmp_path):
    archive, build, qualification = fixture_archive(tmp_path)
    proof = verify(archive, build, qualification)
    assert proof["qualified"] and proof["oci_index"] == build["images"]["candidate"]["image_id"]
    assert proof["archive_sha256"] == hashlib.sha256(archive.read_bytes()).hexdigest()


@pytest.mark.parametrize("change", ["extra-tag", "wrong-qualification", "incomplete-campaign", "corrupt-blob"])
def test_archive_failure_prevents_distribution(tmp_path, change):
    with pytest.raises(QualificationError):
        verify(*fixture_archive(tmp_path, change))


@pytest.mark.parametrize("name,kind", [("../outside", tarfile.REGTYPE), ("/absolute", tarfile.REGTYPE),
                                      ("blobs/evil", tarfile.SYMTYPE), ("blobs/evil", tarfile.LNKTYPE),
                                      ("credentials.json", tarfile.REGTYPE)])
def test_unsafe_members_refused_before_load(name, kind):
    item = tarfile.TarInfo(name)
    item.type = kind
    with pytest.raises(QualificationError):
        validate_members([item])


def test_duplicate_metadata_refused():
    with pytest.raises(QualificationError):
        validate_members([tarfile.TarInfo("index.json"), tarfile.TarInfo("index.json")])
