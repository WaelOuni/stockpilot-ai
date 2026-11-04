"""Build local-only immutable v0.2.3 Web and a pinned Chromium test driver."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.ops.schema6_rollback import BASE, SOURCE, source_files, git, fingerprint, command, require
from scripts.ci.qualify_schema6_rollback import local_engine

PLAYWRIGHT = "mcr.microsoft.com/playwright:v1.64.0-noble@sha256:06a9939e57531807f8d5fd76ce44b53165ffb7d7501d87ab10e285c20b1e971f"
ROOT = Path(__file__).resolve().parents[2]


def web_files(repo):
    source_files(repo)  # independently attest the annotated v0.2.3 tag first
    files = {}
    tree = git(repo, "ls-tree", "-rz", BASE, "frontend/src", "frontend/Dockerfile",
               "frontend/package.json", "frontend/package-lock.json", "frontend/index.html",
               "frontend/tsconfig.json", "frontend/tsconfig.node.json", "frontend/tsconfig.production.json",
               "frontend/vite.config.ts", "docker/web.conf.template", ".dockerignore")
    for entry in tree.split(b"\0"):
        if not entry:
            continue
        meta, raw_path = entry.split(b"\t", 1)
        mode, kind, oid = meta.decode().split()
        path = raw_path.decode()
        require(kind == "blob" and mode in {"100644", "100755"}
                and not any(p.startswith(".env") or p in {"..", "node_modules", "__pycache__"} for p in Path(path).parts)
                and (path.startswith("frontend/") or path in {"docker/web.conf.template", ".dockerignore"}),
                "Unsafe historical Web source.")
        files[path] = git(repo, "cat-file", "blob", oid)
    require("frontend/Dockerfile" in files, "Historical Web Dockerfile missing.")
    require(all("@sha256:" in line for line in files["frontend/Dockerfile"].decode().splitlines()
                if line.startswith("FROM ")), "Historical Web base must stay pinned.")
    return files


def build_image(name, files, dockerfile, revision, user=None):
    hashes, context = fingerprint(files)
    tag = "stockpilot-schema6-" + name + ":" + context[:16]
    require(subprocess.run(["docker", "image", "inspect", tag], capture_output=True).returncode != 0,
            "Test image already exists; no overwrite.")
    with tempfile.TemporaryDirectory(prefix="stockpilot-schema6-web-") as directory:
        for path, content in files.items():
            target = Path(directory) / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        command(["docker", "build", "--platform", "linux/amd64", "-f", str(Path(directory) / dockerfile),
                 "--label", "org.opencontainers.image.source=" + SOURCE,
                 "--label", "org.opencontainers.image.revision=" + revision,
                 "--label", "io.stockpilot.qualification.context=" + context, "-t", tag, directory])
    info = json.loads(command(["docker", "image", "inspect", tag]))[0]
    require(info["Os"] == "linux" and info["Architecture"] == "amd64", "Wrong test platform.")
    require(user is None or info["Config"]["User"] == user, "Historical Web runtime user changed.")
    require(info["Config"]["Labels"]["org.opencontainers.image.revision"] == revision,
            "Test image provenance mismatch.")
    return {"image_id": info["Id"], "context_sha256": context, "files": hashes, "revision": revision}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    require(not args.output.exists(), "Existing browser proof refused.")
    local_engine()
    revision = git(ROOT, "rev-parse", "HEAD").decode().strip()
    web = build_image("old-web", web_files(ROOT), "frontend/Dockerfile", BASE, "101:101")
    lock = json.loads((ROOT / "frontend/package-lock.json").read_text())
    require(lock["packages"]["node_modules/playwright"]["version"] == "1.64.0", "Browser/package version mismatch.")
    driver = {
        "package.json": (ROOT / "frontend/package.json").read_bytes(),
        "package-lock.json": (ROOT / "frontend/package-lock.json").read_bytes(),
        "browser.cjs": (ROOT / "scripts/tests/schema6_browser.cjs").read_bytes(),
        "Dockerfile": ("FROM " + PLAYWRIGHT + "\n"
            "RUN apt-get update && apt-get install -y --no-install-recommends libnss3-tools && rm -rf /var/lib/apt/lists/*\n"
            "WORKDIR /qualification\n"
            "COPY package.json package-lock.json ./\n"
            "RUN npm ci --ignore-scripts\n"
            "COPY browser.cjs ./\n"
            "ENTRYPOINT [\"node\", \"/qualification/browser.cjs\"]\n").encode(),
    }
    browser = build_image("browser", driver, "Dockerfile", revision)
    args.output.write_text(json.dumps({"qualification_commit": revision, "base_commit": BASE,
        "web": web, "browser": browser, "driver_base": PLAYWRIGHT}, indent=2) + "\n")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        raise SystemExit("Historical Web/browser build failed; no publication.") from None
