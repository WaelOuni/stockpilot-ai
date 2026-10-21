"""Owned, private, containerized composition adapter for the existing campaign."""

import ipaddress
import json
import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "docker/compose.b2b-demo.yml"
ISSUER = "https://auth.stockpilot.test:57915/realms/stockpilot-dev"


def local_dns():
    # Test process only, never modifies hosts/DNS/WSL. Docker has the same alias.
    original = socket.getaddrinfo

    def resolve(host, *args, **kwargs):
        return original(
            "127.0.0.1" if host == "auth.stockpilot.test" else host, *args, **kwargs
        )

    socket.getaddrinfo = resolve


def available_subnet(command):
    networks = json.loads(
        command(
            [
                "docker",
                "network",
                "inspect",
                *command(["docker", "network", "ls", "-q"]).split(),
            ]
        )
    )
    occupied = [
        ipaddress.ip_network(c["Subnet"])
        for n in networks
        for c in (n.get("IPAM", {}).get("Config") or [])
        if c.get("Subnet")
    ]
    route_rows = json.loads(
        command(["ip", "-j", "-4", "route", "show", "table", "all"])
    )
    routes = [
        r["dst"]
        for r in route_rows
        if r.get("dst") not in {None, "default", "0.0.0.0/0"}
    ]
    powershell = Path("/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe")
    if powershell.is_file():
        windows_env = os.environ.copy()
        for key in list(windows_env):
            if key.lower() == "psmodulepath":
                windows_env.pop(key)
        windows_routes = json.loads(
            command(
                [
                    str(powershell),
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    "$ErrorActionPreference='Stop'; ConvertTo-Json -Compress -InputObject @((Get-NetRoute -AddressFamily IPv4 -ErrorAction Stop).DestinationPrefix)",
                ],
                env=windows_env,
            )
        )
        routes += [value for value in windows_routes if value != "0.0.0.0/0"]
    occupied += [ipaddress.ip_network(value, strict=False) for value in routes]
    candidate = next(
        (
            ipaddress.ip_network("172.30." + str(i) + ".0/24")
            for i in range(100, 250)
            if not any(
                ipaddress.ip_network("172.30." + str(i) + ".0/24").overlaps(n)
                for n in occupied
                if n.version == 4
            )
        ),
        None,
    )
    if candidate is None:
        raise RuntimeError("No unused validation subnet.")
    return candidate


class CanonicalDemo:
    def __init__(self, directory, env, compose, command, cert, key, *, build=True):
        expected_ports = ("57916", "57918") if build else ("57926", "57928")
        for name, value in zip(
            ("SP_DEMO_MONGO_PORT", "SP_DEMO_POSTGRES_PORT"), expected_ports, strict=True
        ):
            if env.get(name, value) != value:
                raise RuntimeError("Fixed disposable management ports required.")
            env[name] = value
        env["COMPOSE_PROFILES"] = ""
        self.directory, self.env = directory, env
        self.compose, self.command = compose, command
        self.cert, self.key = cert, key
        self.volumes = set()
        self.volume_records = {}
        self.baseline_volumes = set(command(["docker", "volume", "ls", "-q"]).split())
        self.started = False
        self.built_tags = {}
        self.build_specs = []
        self.samples = []
        self.sampling_stop = threading.Event()
        self.sampler = None
        self.capacity_start = {
            "ubuntu_free": shutil.disk_usage(ROOT).free,
            "windows_c_free": shutil.disk_usage("/mnt/c").free,
        }
        candidate = available_subnet(command)
        env.update(
            SP_DEMO_RUNTIME=str(directory),
            SP_DEMO_GID=str(os.getgid()),
            SP_DEMO_SUBNET=str(candidate),
            SP_DEMO_PROXY_IP=str(candidate.network_address + 254),
        )
        # No build/download before a local physical-capacity gate.
        if (
            min(shutil.disk_usage(ROOT).free, shutil.disk_usage("/mnt/c").free)
            < 10 * 1024**3
        ):
            raise RuntimeError("At least 10 GiB local/host headroom required.")
        suffix = env["BFF_TEST_PROJECT"].removeprefix("stockpilot-bff-test-")
        for role, dockerfile in (("api", "Dockerfile"), ("web", "frontend/Dockerfile")):
            if build:
                tag = "stockpilot-b2b-demo-" + role + ":" + suffix
                env["SP_DEMO_" + role.upper() + "_IMAGE"] = tag
                self.build_specs.append((tag, dockerfile))
        realm = json.loads(
            (ROOT / "docker/keycloak/stockpilot-dev-realm.json").read_text()
        )
        realm["sslRequired"] = "all"
        (directory / "realm.json").write_text(json.dumps(realm))
        (directory / "proxy.conf").write_text(
            (ROOT / "docker/b2b/proxy.conf").read_text()
        )
        for path in (cert, key, directory / "realm.json", directory / "proxy.conf"):
            path.chmod(0o640)
        # bff.json is absent until explicit migrations/provisioning complete.

    def build_images(self):
        for tag, dockerfile in self.build_specs:
            if self.command(["docker", "image", "ls", "-q", tag]).strip():
                raise RuntimeError("Build tag already exists.")
            self.command(
                [
                    "docker",
                    "build",
                    "--label",
                    "stockpilot.audit.purpose=v1384-ephemeral",
                    "-f",
                    dockerfile,
                    "-t",
                    tag,
                    ".",
                ],
                self.env,
            )
            self.built_tags[tag] = self.command(
                ["docker", "image", "inspect", "--format", "{{.Id}}", tag]
            ).strip()

    def start_proxy(self):
        self.command(
            [*self.compose, "--profile", "runtime", "up", "-d", "--no-deps", "proxy"],
            self.env,
        )
        self.started = True
        time.sleep(0.5)
        cid = self.command([*self.compose, "ps", "-aq", "proxy"], self.env).strip()
        state = json.loads(
            self.command(["docker", "inspect", "--format", "{{json .State}}", cid])
        )
        if not state["Running"]:
            log = self.directory / "proxy-private.log"
            result = subprocess.run(
                ["docker", "logs", cid], capture_output=True, text=True, check=False
            )
            log.write_text(result.stdout + result.stderr)
            log.chmod(0o600)
            raise RuntimeError("Private HTTPS proxy failed to start.")

    def start(self, bff_env):
        config = {
            k: v
            for k, v in bff_env.items()
            if k.startswith("BFF_") and not k.startswith("BFF_TEST_")
        }
        config.update(
            BFF_ISSUER=ISSUER,
            BFF_MONGO_URI="mongodb://mongo:27017/?replicaSet=rs0&directConnection=true",
            BFF_ASSISTANT_POSTGRES_URI="postgresql://ci:"
            + self.env["BFF_TEST_DB_PASSWORD"]
            + "@test-postgres:55432/stockpilot_ci",
            BFF_TRUSTED_PROXY_IPS=json.dumps([self.env["SP_DEMO_PROXY_IP"]]),
            BFF_OIDC_CA_FILE="/run/stockpilot/ca.crt",
        )
        if bff_env.get("BFF_ASSISTANT_OLLAMA_URL") == self.env.get("V1383_OLLAMA_URL"):
            config["BFF_ASSISTANT_OLLAMA_URL"] = self.env[
                "STOCKPILOT_DEMO_OLLAMA_CONTAINER_URL"
            ]
        path = self.directory / "bff.json"
        path.write_text(json.dumps(config))
        path.chmod(0o640)
        self.command(
            [*self.compose, "--profile", "runtime", "up", "-d", "--no-deps", "bff"],
            self.env,
        )
        # A container recreate changes its IP; reload the private proxy's DNS.
        self.start_proxy()
        for _ in range(100):
            try:
                result = httpx.get(
                    "https://127.0.0.1:57914/b2b/auth/session",
                    verify=str(self.cert),
                    timeout=2,
                    trust_env=False,
                )
                if result.status_code == 401:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.3)
        raise RuntimeError("Container BFF readiness deadline.")

    def stop(self):
        if self.started:
            self.command(
                [*self.compose, "--profile", "runtime", "stop", "-t", "100", "bff"],
                self.env,
            )

    def validate_mount(self, mount):
        if mount["Type"] == "tmpfs":
            return
        if mount["Type"] == "bind":
            source = Path(mount["Source"]).resolve()
            if source.parent != self.directory.resolve():
                raise RuntimeError("Foreign bind mount.")
            if mount["RW"]:
                raise RuntimeError("Writable host bind refused.")
            return
        if mount["Type"] != "volume":
            raise RuntimeError("Unexpected storage.")
        name = mount["Name"]
        if name in self.baseline_volumes:
            raise RuntimeError("Pre-existing volume refused.")
        item = json.loads(self.command(["docker", "volume", "inspect", name]))[0]
        labels = item.get("Labels") or {}
        if (
            labels.get("com.docker.compose.project") != self.env["BFF_TEST_PROJECT"]
            or labels.get("stockpilot.audit.purpose") != "v1384-ephemeral"
        ):
            raise RuntimeError("Foreign volume refused.")
        identity = {
            k: item.get(k)
            for k in ("CreatedAt", "Driver", "Labels", "Name", "Options", "Scope")
        }
        if name in self.volume_records and self.volume_records[name] != identity:
            raise RuntimeError("Volume identity changed.")
        self.volume_records[name] = identity
        self.volumes.add(name)

    def cleanup_volumes(self):
        # Called only after container provenance validation and exact project down.
        for name in sorted(self.volumes):
            data = json.loads(self.command(["docker", "volume", "inspect", name]))[0]
            identity = {
                k: data.get(k)
                for k in ("CreatedAt", "Driver", "Labels", "Name", "Options", "Scope")
            }
            if (
                identity != self.volume_records[name]
                or (data.get("Labels") or {}).get("stockpilot.audit.purpose")
                != "v1384-ephemeral"
            ):
                raise RuntimeError("Volume ownership changed.")
            if self.command(
                ["docker", "ps", "-aq", "--filter", "volume=" + name]
            ).strip():
                raise RuntimeError("Volume still referenced.")
            self.command(["docker", "volume", "rm", name])

    def stats(self):
        ids = self.command(
            [*self.compose, "--profile", "runtime", "ps", "-q"], self.env
        ).split()
        if not ids:
            return []
        lines = self.command(
            ["docker", "stats", "--no-stream", "--format", "{{json .}}", *ids]
        ).splitlines()
        return [
            {k: value[k] for k in ("Name", "CPUPerc", "MemUsage", "PIDs", "BlockIO")}
            for value in map(json.loads, lines)
        ]

    def cleanup_images(self):
        for tag, image_id in self.built_tags.items():
            records = json.loads(self.command(["docker", "image", "inspect", tag]))
            if (
                records[0]["Id"] != image_id
                or records[0]["Config"]
                .get("Labels", {})
                .get("stockpilot.audit.purpose")
                != "v1384-ephemeral"
            ):
                raise RuntimeError("Build image ownership changed.")
            if self.command(
                ["docker", "ps", "-aq", "--filter", "ancestor=" + records[0]["Id"]]
            ).strip():
                raise RuntimeError("Image still referenced.")
            self.command(["docker", "image", "rm", tag])

    def start_sampling(self):
        def sample():
            while not self.sampling_stop.is_set():
                try:
                    records = self.stats()
                    if len(self.samples) < 300:
                        self.samples.append(
                            {
                                "monotonic": round(time.monotonic(), 2),
                                "services": records,
                            }
                        )
                except (RuntimeError, subprocess.SubprocessError):
                    pass  # Sampling failure is not a successful readiness probe.
                self.sampling_stop.wait(2)

        self.sampler = threading.Thread(
            target=sample, name="owned-demo-resource-sampler"
        )
        self.sampler.start()

    def stop_sampling(self):
        self.sampling_stop.set()
        if self.sampler is not None:
            self.sampler.join(timeout=15)
            if self.sampler.is_alive():
                raise RuntimeError("Resource sampler still active.")

    def security_probes(self):
        with httpx.Client(verify=str(self.cert), trust_env=False, timeout=5) as client:
            for path in (
                "/health",
                "/metrics",
                "/products",
                "/api/products",
                "/backoffice",
            ):
                if client.get("https://127.0.0.1:57914" + path).status_code != 404:
                    raise RuntimeError("Historical/diagnostic ingress exposed.")
            for path in (
                "/admin/",
                "/admin/realms",
                "/realms/master/.well-known/openid-configuration",
                "/health",
                "/metrics",
            ):
                if (
                    client.get("https://auth.stockpilot.test:57915" + path).status_code
                    != 404
                ):
                    raise RuntimeError("Keycloak administration/diagnostics exposed.")
            if (
                client.get(
                    "https://127.0.0.1:57914/b2b/auth/session",
                    headers={"Host": "forged.example"},
                ).status_code
                != 400
            ):
                raise RuntimeError("Forged public Host accepted.")
