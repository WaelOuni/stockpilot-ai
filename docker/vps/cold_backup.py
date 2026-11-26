"""Capture a stopped VPS Compose project with restic. Never stops or starts services."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

RESTIC = "restic/restic:0.18.1@sha256:39d9072fb5651c80d75c7a811612eb60b4c06b32ffe87c2e9f3c7222e1797e76"
DEPLOY = Path(__file__).resolve().parent
VOLUMES = {
    "mongo": {"/data/db": "mongo", "/data/configdb": "mongo-config"},
    "postgres": {"/var/lib/postgresql/data": "postgres"},
    "keycloak-db": {"/var/lib/postgresql/data": "identity"},
}


def output(*args):
    return subprocess.check_output(args, text=True).strip()


def require(condition):
    if not condition:
        raise ValueError("Target, private files or clean shutdown do not match")


def bind(source, target):
    return ["--mount", f"type=bind,src={source},dst={target},readonly"]


def capture(args):
    require(re.fullmatch("[a-z0-9][a-z0-9_-]+", args.project))
    require(args.credentials.stat().st_mode & 0o077 == 0)
    settings = args.settings.resolve(strict=True)
    # Explicit env file is authoritative. Do not inherit shell Compose overrides.
    compose = ["env", "-i", f"HOME={Path.home()}", f"PATH={os.environ['PATH']}",
               "docker", "compose", "--env-file", str(settings), "-p", args.project,
               "-f", str(DEPLOY / "compose.yml")]
    config = json.loads(output(*compose, "config", "--format", "json"))
    hashes = dict(line.split() for line in output(*compose, "config", "--hash", "*").splitlines())
    services = {"mongo", "postgres", "keycloak-db", "keycloak", "proxy", "bff"}
    ids = output("docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={args.project}").splitlines()
    require(len(ids) == len(services))
    mounts = []
    for ident in ids:
        # Avoid collecting Docker's private environment values.
        def field(key):
            return json.loads(output("docker", "inspect", "-f", "{{json ." + key + "}}", ident))
        labels = field("Config.Labels")
        service = labels["com.docker.compose.service"]
        require(service in services)
        require(labels["com.docker.compose.config-hash"] == hashes[service])
        services.remove(service)
        state = field("State")
        require(state["Status"] == "exited" and state["ExitCode"] in ((0, 143) if service == "keycloak" else (0,)) and not state["OOMKilled"])
        require(field("Config.Image") == config["services"][service]["image"])
        actual = {m["Destination"]: m for m in field("Mounts")}
        for m in config["services"][service].get("volumes", []):
            if m["type"] == "bind":
                mount = actual[m["target"]]
                require(mount["Type"] == "bind" and not mount["RW"])
                source = Path(mount["Source"])
                desktop = Path("/run/desktop/mnt/host/wsl/docker-desktop-bind-mounts")
                if args.lab_vault and source.is_relative_to(desktop):
                    source = Path("/mnt/wsl/docker-desktop-bind-mounts") / source.relative_to(desktop)
                require(source.samefile(m["source"]))
        for dest, volume in VOLUMES.get(service, {}).items():
            name = config["volumes"][volume]["name"]
            require(actual[dest]["Type"] == "volume" and actual[dest]["Name"] == name)
            labels = json.loads(output("docker", "volume", "inspect", "-f", "{{json .Labels}}", name))
            require(labels["com.docker.compose.project"] == args.project and labels["com.docker.compose.volume"] == volume)
            require(not output("docker", "ps", "-q", "--filter", f"volume={name}"))
            mounts.extend(["--mount", f"type=volume,src={name},dst=/snapshot/data/{volume},readonly"])
    bff_mounts = config["services"]["bff"]["volumes"]
    private = Path(next(m["source"] for m in bff_mounts if m["target"] == "/run/stockpilot/bff.json")).parent
    require(private.stat().st_mode & 0o077 == 0)
    require(not args.credentials.resolve().is_relative_to(private))
    mounts += bind(private, "/snapshot/private") + bind(settings, "/snapshot/settings.env")
    for file in ("compose.yml", "proxy.conf.template", "identity-init.sh", "qualify.py", "rs-init.js"):
        mounts += bind(DEPLOY / file, "/snapshot/deploy/" + file)
    run = ["docker", "run", "--rm", "--read-only", "--tmpfs", "/tmp", "--env-file", str(args.credentials)]
    # Local storage is explicit and restricted to the synthetic recovery drill.
    if args.lab_vault:
        require(args.project.startswith("sp029-trial-"))
        run += ["--network", "none", "--mount", f"type=bind,src={args.lab_vault.resolve(strict=True)},dst=/vault",
                "-e", "RESTIC_REPOSITORY=/vault"]
    else:
        repo = next((v for v in args.credentials.read_text().splitlines() if v.startswith("RESTIC_REPOSITORY=")), "")
        require(repo.startswith(("RESTIC_REPOSITORY=s3:https://", "RESTIC_REPOSITORY=rest:https://")))
    subprocess.run([*run, *mounts, RESTIC, "--no-cache", "backup", "--host", args.project, "/snapshot"], check=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project")
    parser.add_argument("settings", type=Path)
    parser.add_argument("credentials", type=Path)
    parser.add_argument("--lab-vault", type=Path)
    try:
        capture(parser.parse_args())
    except (OSError, ValueError, KeyError, StopIteration, subprocess.SubprocessError) as error:
        sys.exit(f"Backup refused or incomplete ({type(error).__name__}). Inspect target before retry.")
