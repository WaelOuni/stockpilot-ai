"""Check the built Keycloak image offline, without a database or registry write."""
import argparse
import io
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[2]
THEME = ROOT / "docker/keycloak/themes/stockpilot"
THEME_FILES = (
    "login/footer.ftl", "login/theme.properties",
    "login/messages/messages_fr.properties", "login/messages/messages_en.properties",
)


class ImageRefused(ValueError):
    pass


def require(condition):
    if not condition:
        raise ImageRefused("Keycloak image contract refused.")


def verify_theme(archive, expected):
    """Read only: reject extra entries, links, traversal and content differing from the checkout."""
    directories = {"stockpilot"}
    for name in expected:
        directories.update(str(p) for p in (PurePosixPath("stockpilot") / name).parents if str(p) != ".")
    seen = set()
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:*") as stream:
        for member in stream:
            path = PurePosixPath(member.name)
            require(not path.is_absolute() and ".." not in path.parts)
            if member.isdir():
                require(str(path) in directories)
                continue
            require(member.isfile() and path.parts and path.parts[0] == "stockpilot")
            name = str(path.relative_to("stockpilot"))
            require(name in expected and name not in seen)
            require(member.size == len(expected[name]))
            require(stream.extractfile(member).read() == expected[name])
            seen.add(name)
    require(seen == set(expected))


def command(*args):
    return subprocess.run(args, check=True, capture_output=True, timeout=60).stdout


def check(image, revision=None, source=None):
    require(bool(revision) == bool(source))
    if revision:
        require(re.fullmatch(r"[0-9a-f]{40}", revision))
    metadata = json.loads(command("docker", "image", "inspect", image))[0]
    config = metadata["Config"]
    require(metadata["Os"] == "linux" and metadata["Architecture"] == "amd64")
    require(config["User"] == "1000" and config["Entrypoint"] == ["/opt/keycloak/bin/kc.sh"])
    require(not config.get("Volumes"))
    if revision:
        labels = config.get("Labels") or {}
        require(labels.get("org.opencontainers.image.revision") == revision)
        require(labels.get("org.opencontainers.image.source") == source)
    expected = {name: (THEME / name).read_bytes() for name in THEME_FILES}
    container = command(
        "docker", "create", "--network", "none", "--read-only", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges", image, "--version",
    ).decode().strip()
    require(re.fullmatch(r"[0-9a-f]{64}", container))
    try:
        archive = command("docker", "cp", container + ":/opt/keycloak/themes/stockpilot", "-")
        verify_theme(archive, expected)
        version = command("docker", "start", "--attach", container).decode()
        state = json.loads(command("docker", "inspect", container))[0]["State"]
        require(state["Status"] == "exited" and state["ExitCode"] == 0)
        require(re.search(r"(?m)^Keycloak 26\.8\.1\r?$", version))
    finally:
        # Only the uniquely identified test container created above; no volume deletion.
        command("docker", "rm", "--force", container)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image")
    parser.add_argument("--revision")
    parser.add_argument("--source")
    args = parser.parse_args()
    try:
        check(args.image, args.revision, args.source)
    except (ValueError, KeyError, TypeError, IndexError, OSError, tarfile.TarError, subprocess.SubprocessError):
        print("Keycloak image verification failed; no database or registry write performed.")
        return 1
    print("Keycloak 26.8.1 linux/amd64 verified: native user/entrypoint and four exact theme files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
