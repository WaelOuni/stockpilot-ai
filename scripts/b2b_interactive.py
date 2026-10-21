"""Local-only lifecycle for the single canonical Compose topology.

No persistent installation is created by importing this module. The CLI uses one
fixed state directory; alternative roots are an internal disposable-test seam.
"""

import base64
import contextlib
import copy
import fcntl
import hashlib
import ipaddress
import json
import os
import secrets
import shutil
import socket
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "docker/compose.b2b-demo.yml"
DEFAULT_STATE = Path.home() / ".local/share/stockpilot/b2b-demo"
PROJECT = "stockpilot-b2b-local"
PURPOSE = "v139-local-synthetic"
APP = "https://127.0.0.1:57914"
ISSUER = "https://auth.stockpilot.test:57915/realms/stockpilot-dev"
SERVICES = ("mongo", "test-postgres", "keycloak-db", "keycloak", "bff", "proxy")
INFRA = SERVICES[:4]
PORTS = (57914, 57915)
from stockpilot.infrastructure.config.b2b_demo_worker import ACCOUNTS


class DemoError(RuntimeError):
    """Safe operator message; never expose a command, environment or DB error."""


def command(args, *, env=None, timeout=300):
    if str(args[0]).lower().endswith("powershell.exe"):
        env = (env or os.environ).copy()
        for key in list(env):
            if key.lower() == "psmodulepath":
                env.pop(key)
    try:
        p = subprocess.run(
            args,
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        raise DemoError(
            "Local command unavailable or timed out; inspect status before retry."
        ) from None
    if p.returncode:
        raise DemoError("Local command failed; resources and private state retained.")
    return p.stdout.strip()


def private_file(path, *, group=False):
    if path.is_symlink() or not path.is_file():
        raise DemoError("Required private file missing or symlinked.")
    s = path.stat()
    allowed = 0o640 if group else 0o600
    if s.st_uid != os.getuid() or s.st_mode & 0o777 != allowed:
        raise DemoError("Private file ownership/permissions invalid.")


def write_json(path, value, mode=0o600):
    if path.exists() and path.is_symlink():
        raise DemoError("Symlink refused.")
    temp = path.with_suffix(".new")
    with temp.open("x") as stream:
        os.chmod(temp, mode)
        json.dump(value, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def capacity():
    values = {"linux_free_bytes": shutil.disk_usage(ROOT).free}
    if Path("/mnt/c").is_dir():
        values["windows_free_bytes"] = shutil.disk_usage("/mnt/c").free
    return values


def require_capacity():
    if min(capacity().values()) < 10 * 1024**3:
        raise DemoError("At least 10 GiB free on Linux and Windows required.")


def check_ports():
    for port in PORTS:
        try:
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", port))
        except OSError:
            raise DemoError(
                "Canonical HTTPS port already occupied; no service changed."
            ) from None


def ollama_status(url):
    try:
        parsed = httpx.URL(url)
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.userinfo
        ):
            raise ValueError
        host = parsed.host
        if host != "host.docker.internal" and not ipaddress.ip_address(host).is_private:
            raise ValueError
        response = httpx.get(url.rstrip("/") + "/api/tags", timeout=5, trust_env=False)
        response.raise_for_status()
        names = {x["name"] for x in response.json()["models"]}
        available = {"qwen2.5:7b", "nomic-embed-text:latest"} <= names
        return {
            "available": available,
            "required_models": ["qwen2.5:7b", "nomic-embed-text"],
        }
    except (ValueError, KeyError, TypeError, httpx.HTTPError):
        return {
            "available": False,
            "required_models": ["qwen2.5:7b", "nomic-embed-text"],
        }


@contextlib.contextmanager
def local_dns():
    original = socket.getaddrinfo

    def resolve(host, *args, **kwargs):
        return original(
            "127.0.0.1" if host == "auth.stockpilot.test" else host, *args, **kwargs
        )

    socket.getaddrinfo = resolve
    try:
        yield
    finally:
        socket.getaddrinfo = original


def certificates(directory):
    now = datetime.now(UTC)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "StockPilot B2B Local Operator CA")]
    )
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=1095))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(False, False, False, False, False, True, True, False, False),
            critical=True,
        )
        .sign(ca_key, hashes.SHA256())
    )
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    cert = (
        x509.CertificateBuilder()
        .subject_name(
            x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "StockPilot B2B Local")])
        )
        .issuer_name(ca_name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                    x509.DNSName("auth.stockpilot.test"),
                ]
            ),
            critical=False,
        )
        .add_extension(
            x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    for name, data, mode in (
        ("ca.crt", ca.public_bytes(serialization.Encoding.PEM), 0o640),
        ("tls.crt", cert.public_bytes(serialization.Encoding.PEM), 0o640),
        (
            "tls.key",
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ),
            0o640,
        ),
        (
            "ca.key",
            ca_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ),
            0o600,
        ),
    ):
        (directory / name).write_bytes(data)
        (directory / name).chmod(mode)


def windows_preflight(ca):
    ps = Path("/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe")
    if not ps.exists():
        return {"available": False, "dns": False, "ca_trusted": False}
    try:
        windows_path = command(["wslpath", "-w", str(ca)])
        # Path is transported as an environment variable, never inserted into code.
        env = os.environ | {"STOCKPILOT_PUBLIC_CA_PATH": windows_path}
        env["WSLENV"] = env.get("WSLENV", "") + ":STOCKPILOT_PUBLIC_CA_PATH"
        script = """$ErrorActionPreference='Stop'; $d=$false; $t=$false;
try { $a=@([System.Net.Dns]::GetHostAddresses('auth.stockpilot.test')); $d=($a.Count -gt 0 -and @($a|Where-Object {$_.IPAddressToString -ne '127.0.0.1'}).Count -eq 0) } catch {}
try { $c=[System.Security.Cryptography.X509Certificates.X509Certificate2]::new($env:STOCKPILOT_PUBLIC_CA_PATH); $ch=[System.Security.Cryptography.X509Certificates.X509Chain]::new(); $ch.ChainPolicy.RevocationMode='NoCheck'; $t=$ch.Build($c) } catch {}
@{available=$true;dns=$d;ca_trusted=$t}|ConvertTo-Json -Compress"""
        return json.loads(
            command(
                [str(ps), "-NoProfile", "-NonInteractive", "-Command", script],
                env=env,
                timeout=20,
            )
        )
    except (DemoError, ValueError):
        return {"available": False, "dns": False, "ca_trusted": False}


class InteractiveDemo:
    def __init__(self, state=DEFAULT_STATE, *, ephemeral=False):
        self.state = Path(state).absolute()
        if not ephemeral and self.state != DEFAULT_STATE:
            raise DemoError("Only one canonical local state directory allowed.")
        if ephemeral and not self.state.name.startswith("stockpilot-v139-test-"):
            raise DemoError("Explicit disposable test root required.")
        if self.state.is_symlink() or any(p.is_symlink() for p in self.state.parents):
            raise DemoError("Symlinked state path refused.")
        self.ephemeral = ephemeral
        self.env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith(("COMPOSE_", "SP_DEMO_", "BFF_TEST_"))
        }
        self.env["COMPOSE_PROFILES"] = ""
        self.manifest = None

    @contextlib.contextmanager
    def locked(self):
        self.state.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock_path = self.state.parent / (self.state.name + ".lock")
        fd = os.open(lock_path, os.O_CREAT | os.O_NOFOLLOW | os.O_RDWR, 0o600)
        try:
            if (
                os.fstat(fd).st_uid != os.getuid()
                or os.fstat(fd).st_mode & 0o777 != 0o600
            ):
                raise DemoError("Unsafe operator lock.")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise DemoError("Another lifecycle operation is active.") from None
            yield
        finally:
            os.close(fd)

    def docker(self):
        endpoint = command(
            ["docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"]
        )
        if not endpoint.startswith("unix://") or self.env.get(
            "DOCKER_HOST", "unix://"
        ).startswith(("tcp:", "ssh:")):
            raise DemoError("Local Unix Docker engine required.")
        command(["docker", "info", "--format", "{{.ServerVersion}}"], timeout=15)

    def compose(self, *args):
        return command(
            [
                "docker",
                "compose",
                "--env-file",
                "/dev/null",
                "-f",
                str(self.state / "compose.json"),
                "-p",
                self.manifest["project"],
                *args,
            ],
            env=self.env,
        )

    def save(self):
        write_json(self.state / "manifest.json", self.manifest)

    def seal(self):
        self.manifest["files"] = {
            p.name: digest(p)
            for p in self.state.iterdir()
            if p.is_file() and p.name != "manifest.json"
        }
        self.save()

    def load(self):
        if (
            not self.state.is_dir()
            or self.state.stat().st_uid != os.getuid()
            or self.state.stat().st_mode & 0o777 != 0o700
        ):
            raise DemoError("Run init first; private state is absent or unsafe.")
        private_file(self.state / "manifest.json")
        m = json.loads((self.state / "manifest.json").read_text())
        if (
            not isinstance(m, dict)
            or not isinstance(m.get("installation"), str)
            or len(m["installation"]) != 32
            or any(c not in "0123456789abcdef" for c in m["installation"])
        ):
            raise DemoError("Invalid installation identity.")
        expected = PROJECT + (
            "-test-" + m["installation"][:12] if self.ephemeral else ""
        )
        if (
            m.get("version") != 1
            or m.get("project") != expected
            or m.get("state") != str(self.state)
            or m.get("purpose") != PURPOSE
            or m.get("phase") != "ready"
        ):
            raise DemoError(
                "Incomplete or foreign initialization; no automatic repair or reset."
            )
        if m.get("template_sha256") != digest(TEMPLATE):
            raise DemoError(
                "Composition changed since init; explicit upgrade procedure required."
            )
        for name, fingerprint in m["files"].items():
            if Path(name).name != name:
                raise DemoError("Invalid private manifest.")
            private_file(
                self.state / name,
                group=name
                in {
                    "bff.json",
                    "operator.json",
                    "tls.crt",
                    "tls.key",
                    "ca.crt",
                    "realm.json",
                    "mongo.key",
                    "proxy.conf",
                },
            )
            if digest(self.state / name) != fingerprint:
                raise DemoError(
                    "Private configuration changed; refusing unsafe restart."
                )
        cert = x509.load_pem_x509_certificate((self.state / "tls.crt").read_bytes())
        if cert.not_valid_after_utc <= datetime.now(UTC):
            raise DemoError("HTTPS certificate expired; explicit rotation required.")
        self.manifest = m

    def attest(self):
        self.docker()
        m = self.manifest
        ids = command(
            [
                "docker",
                "ps",
                "-aq",
                "--filter",
                "label=com.docker.compose.project=" + m["project"],
            ]
        ).split()
        rows = json.loads(command(["docker", "inspect", *ids])) if ids else []
        records = {}
        for row in rows:
            labels = row["Config"].get("Labels") or {}
            name = labels.get("com.docker.compose.service")
            if (
                name not in SERVICES
                or name in records
                or labels.get("stockpilot.audit.purpose") != PURPOSE
                or labels.get("com.docker.compose.project.config_files")
                != str(self.state / "compose.json")
            ):
                raise DemoError(
                    "Foreign or unexpected project container; no operation performed."
                )
            if row["Id"] != m["containers"].get(name) or row["Image"] != m[
                "images"
            ].get(name):
                raise DemoError("Container/image identity changed.")
            for mount in row.get("Mounts", []):
                if mount["Type"] == "volume":
                    if mount["Name"] not in m["volumes"]:
                        raise DemoError("Foreign persistent mount.")
                elif mount["Type"] == "bind":
                    if Path(mount["Source"]).parent != self.state or mount["RW"]:
                        raise DemoError("Foreign or writable bind mount.")
                elif mount["Type"] != "tmpfs":
                    raise DemoError("Unsupported mount.")
            for values in (row["HostConfig"].get("PortBindings") or {}).values():
                if any(
                    v["HostIp"] != "127.0.0.1" or int(v["HostPort"]) not in PORTS
                    for v in values
                ):
                    raise DemoError("Unsafe published port.")
            records[name] = row
        if set(records) != set(SERVICES):
            raise DemoError(
                "A canonical container is missing; refusing implicit recreation."
            )
        for name, identity in m["volumes"].items():
            v = json.loads(command(["docker", "volume", "inspect", name]))[0]
            if volume_identity(v) != identity:
                raise DemoError(
                    "A volume is missing or replaced; no empty volume will be created."
                )
        for name, identity in m["networks"].items():
            n = json.loads(command(["docker", "network", "inspect", name]))[0]
            labels = n.get("Labels") or {}
            if (
                n["Id"] != identity
                or labels.get("stockpilot.audit.purpose") != PURPOSE
                or labels.get("com.docker.compose.project") != m["project"]
            ):
                raise DemoError("Network identity changed.")
            if set(n.get("Containers", {})) - set(m["containers"].values()):
                raise DemoError("Foreign container attached to a canonical network.")
        return records

    def worker(self, action):
        result = self.compose(
            "--profile",
            "operator",
            "run",
            "--rm",
            "--no-deps",
            "--name",
            self.manifest["project"] + "-operator",
            "operator",
            "python",
            "-m",
            "stockpilot.infrastructure.config.b2b_demo_worker",
            action,
        )
        return json.loads(result.splitlines()[-1])

    def init(self, host_ollama, container_ollama):
        if self.state.exists():
            self.load()
            self.attest()
            return {
                "initialized": True,
                "unchanged": True,
                "project": self.manifest["project"],
            }
        self.docker()
        require_capacity()
        check_ports()
        if not ollama_status(host_ollama)["available"] or not container_ollama:
            raise DemoError(
                "Explicit private Ollama endpoints and existing required models needed."
            )
        # Validate URL shape without using the container hostname from the host process.
        u = httpx.URL(container_ollama)
        if u.host != "host.docker.internal":
            try:
                if not ipaddress.ip_address(u.host).is_private:
                    raise ValueError
            except ValueError:
                raise DemoError("Private container Ollama address required.") from None
        if (
            u.scheme not in {"http", "https"}
            or u.path not in {"", "/"}
            or u.query
            or u.userinfo
        ):
            raise DemoError("Private container Ollama URL invalid.")
        installation = uuid4().hex
        project = PROJECT + ("-test-" + installation[:12] if self.ephemeral else "")
        if command(
            [
                "docker",
                "ps",
                "-aq",
                "--filter",
                "label=com.docker.compose.project=" + project,
            ]
        ) or command(
            [
                "docker",
                "volume",
                "ls",
                "-q",
                "--filter",
                "label=com.docker.compose.project=" + project,
            ]
        ):
            raise DemoError(
                "Canonical project exists without matching state; refusing adoption."
            )
        for suffix in ("mongo-data", "mongo-config", "postgres-data", "keycloak-data"):
            if command(
                [
                    "docker",
                    "volume",
                    "ls",
                    "-q",
                    "--filter",
                    "name=^" + project + "_" + suffix + "$",
                ]
            ):
                raise DemoError("Named volume already exists; refusing adoption.")
        if command(
            [
                "docker",
                "network",
                "ls",
                "-q",
                "--filter",
                "label=com.docker.compose.project=" + project,
            ]
        ):
            raise DemoError("Pre-existing project network refused.")
        for suffix in ("edge", "application", "identity", "egress"):
            if command(
                [
                    "docker",
                    "network",
                    "ls",
                    "-q",
                    "--filter",
                    "name=^" + project + "_" + suffix + "$",
                ]
            ):
                raise DemoError("Pre-existing named network refused.")
        self.state.mkdir(mode=0o700)
        self.manifest = {
            "version": 1,
            "installation": installation,
            "project": project,
            "purpose": PURPOSE,
            "state": str(self.state),
            "phase": "initializing",
            "template_sha256": digest(TEMPLATE),
            "revision": command(["git", "rev-parse", "HEAD"]),
            "host_ollama": host_ollama,
            "created_at": datetime.now(UTC).isoformat(),
        }
        self.save()
        passwords = {
            key: secrets.token_urlsafe(36)
            for key in (
                "mongo_password",
                "pg_password",
                "keycloak_db_password",
                "app_mongo_password",
                "app_pg_password",
                "client_secret",
                "configuration_key",
            )
        }
        credentials = {
            "realm": "stockpilot-dev",
            "accounts": [
                {
                    "username": user,
                    "organization": org,
                    "role": role,
                    "password": secrets.token_urlsafe(30),
                }
                for user, org, role in ACCOUNTS
            ],
        }
        write_json(self.state / "credentials.json", credentials)
        certificates(self.state)
        (self.state / "mongo.key").write_text(
            base64.b64encode(secrets.token_bytes(512)).decode()
        )
        (self.state / "mongo.key").chmod(0o640)
        realm = json.loads(
            (ROOT / "docker/keycloak/stockpilot-dev-realm.json").read_text()
        )
        realm["sslRequired"] = "all"
        realm["clients"][0]["secret"] = passwords["client_secret"]
        realm["users"] = [
            {
                "id": f"00000000-0000-4000-8000-{index:012d}",
                "username": row["username"],
                "firstName": "Demo",
                "lastName": row["organization"],
                "email": row["username"] + "@example.test",
                "emailVerified": True,
                "enabled": True,
                "requiredActions": [],
                "credentials": [
                    {"type": "password", "value": row["password"], "temporary": False}
                ],
            }
            for index, row in enumerate(credentials["accounts"], 1)
        ]
        write_json(self.state / "realm.json", realm, 0o640)
        (self.state / "proxy.conf").write_text(
            (ROOT / "docker/b2b/proxy.conf").read_text()
        )
        (self.state / "proxy.conf").chmod(0o600)
        # Canonical topology is rendered, never forked into a second source Compose.
        from scripts.tests.canonical_demo import available_subnet

        subnet = available_subnet(command)
        env = self.env | {
            "BFF_TEST_PROJECT": project,
            "BFF_TEST_DB_PASSWORD": passwords["pg_password"],
            "BFF_TEST_CLIENT_SECRET": passwords["client_secret"],
            "BFF_TEST_USER_PASSWORD": "unused-private-realm",
            "SP_DEMO_RUNTIME": str(self.state),
            "SP_DEMO_GID": str(os.getgid()),
            "SP_DEMO_SUBNET": str(subnet),
            "SP_DEMO_PROXY_IP": str(subnet.network_address + 254),
            "SP_DEMO_PURPOSE": PURPOSE,
        }
        tags = {
            role: project + "-" + role + ":" + installation[:12]
            for role in ("api", "web")
        }
        for role, tag in tags.items():
            if command(["docker", "image", "ls", "-q", tag]):
                raise DemoError("Application image tag already exists.")
            command(
                [
                    "docker",
                    "build",
                    "--label",
                    "stockpilot.audit.purpose=" + PURPOSE,
                    "-f",
                    "Dockerfile" if role == "api" else "frontend/Dockerfile",
                    "-t",
                    tag,
                    ".",
                ],
                timeout=900,
            )
            env["SP_DEMO_" + role.upper() + "_IMAGE"] = tag
        self.manifest["built_images"] = {
            tag: command(["docker", "image", "inspect", "--format", "{{.Id}}", tag])
            for tag in tags.values()
        }
        self.save()
        config = json.loads(
            command(
                [
                    "docker",
                    "compose",
                    "--env-file",
                    "/dev/null",
                    "-f",
                    str(TEMPLATE),
                    "--profile",
                    "*",
                    "config",
                    "--format",
                    "json",
                ],
                env=env,
            )
        )
        config = interactive_composition(
            config,
            passwords | {"replica_key": (self.state / "mongo.key").read_text()},
            installation,
        )
        write_json(self.state / "compose.json", config)
        dbname = "stockpilot_b2b_test_" + installation
        mongo_uri = (
            "mongodb://stockpilot_bff:"
            + passwords["app_mongo_password"]
            + "@mongo:27017/"
            + dbname
            + "?replicaSet=rs0&directConnection=true"
        )
        bff = {
            "BFF_MODE": "b2b_business_test",
            "BFF_ORIGIN": APP,
            "BFF_ISSUER": ISSUER,
            "BFF_CLIENT_ID": "stockpilot-bff",
            "BFF_CLIENT_SECRET": passwords["client_secret"],
            "BFF_CONFIGURATION_KEY": passwords["configuration_key"],
            "BFF_MONGO_URI": mongo_uri,
            "BFF_MONGO_DATABASE": dbname,
            "BFF_OIDC_CA_FILE": "/run/stockpilot/ca.crt",
            "BFF_TRUSTED_PROXY_IPS": json.dumps([env["SP_DEMO_PROXY_IP"]]),
            "BFF_ASSISTANT_ENABLED": "true",
            "BFF_ASSISTANT_POSTGRES_URI": "postgresql://stockpilot_bff:"
            + passwords["app_pg_password"]
            + "@test-postgres:55432/stockpilot_ci",
            "BFF_ASSISTANT_OLLAMA_URL": container_ollama,
            "BFF_ASSISTANT_ALLOW_HTTP": "true",
        }
        write_json(self.state / "bff.json", bff, 0o640)
        write_json(
            self.state / "operator.json",
            passwords
            | {
                "installation": installation,
                "issuer": ISSUER,
                "ollama": container_ollama,
            },
            0o640,
        )
        self.compose("up", "-d", "--wait", "--wait-timeout", "180", *INFRA)
        self.worker("init")
        self.compose(
            "--profile",
            "runtime",
            "up",
            "-d",
            "--wait",
            "--wait-timeout",
            "120",
            "bff",
            "proxy",
        )
        self.capture()
        self.manifest["phase"] = "ready"
        self.seal()
        self.http_ready()
        self.stop()
        return {
            "initialized": True,
            "project": project,
            "application": APP + "/b2b-app/",
            "accounts_file": str(self.state / "credentials.json"),
            "windows": windows_preflight(self.state / "ca.crt"),
            "running": False,
            "public_ca_file": str(self.state / "ca.crt"),
        }

    def capture(self):
        ids = self.compose("--profile", "*", "ps", "-aq").split()
        rows = json.loads(command(["docker", "inspect", *ids]))
        self.manifest["containers"] = {
            r["Config"]["Labels"]["com.docker.compose.service"]: r["Id"] for r in rows
        }
        self.manifest["images"] = {
            r["Config"]["Labels"]["com.docker.compose.service"]: r["Image"]
            for r in rows
        }
        volumes = {
            m["Name"] for r in rows for m in r["Mounts"] if m["Type"] == "volume"
        }
        self.manifest["volumes"] = {}
        for n in volumes:
            v = json.loads(command(["docker", "volume", "inspect", n]))[0]
            labels = v.get("Labels") or {}
            if (
                labels.get("stockpilot.audit.purpose") != PURPOSE
                or labels.get("com.docker.compose.project") != self.manifest["project"]
            ):
                raise DemoError("Volume not created by this installation.")
            self.manifest["volumes"][n] = volume_identity(v)
        self.manifest["networks"] = {
            n: v["NetworkID"]
            for r in rows
            for n, v in r["NetworkSettings"]["Networks"].items()
        }
        for name in self.manifest["networks"]:
            network = json.loads(command(["docker", "network", "inspect", name]))[0]
            labels = network.get("Labels") or {}
            if (
                labels.get("stockpilot.audit.purpose") != PURPOSE
                or labels.get("com.docker.compose.project") != self.manifest["project"]
            ):
                raise DemoError("Network not created by this installation.")
            if set(network.get("Containers", {})) - set(
                self.manifest["containers"].values()
            ):
                raise DemoError("Foreign endpoint on canonical network.")
        self.save()

    def http_ready(self):
        with (
            local_dns(),
            httpx.Client(
                verify=str(self.state / "ca.crt"), trust_env=False, timeout=5
            ) as client,
        ):
            for _ in range(60):
                try:
                    if (
                        client.get(APP + "/b2b/auth/session").status_code == 401
                        and client.get(
                            ISSUER + "/.well-known/openid-configuration"
                        ).status_code
                        == 200
                    ):
                        return
                except httpx.HTTPError:
                    pass
                time.sleep(1)
        raise DemoError("HTTPS/OIDC readiness deadline; private state retained.")

    def start(self, *, require_windows=True):
        self.load()
        rows = self.attest()
        require_capacity()
        if require_windows:
            preflight = windows_preflight(self.state / "ca.crt")
            if not preflight["dns"] or not preflight["ca_trusted"]:
                raise DemoError(
                    "Windows DNS/CA preflight incomplete. Follow the canonical guide; nothing installed automatically."
                )
        if not ollama_status(
            os.environ.get("STOCKPILOT_DEMO_OLLAMA_URL") or self.manifest["host_ollama"]
        )["available"]:
            raise DemoError(
                "Ollama/models unavailable; no downloads or automatic endpoint changes."
            )
        if not rows["proxy"]["State"]["Running"]:
            check_ports()
        self.compose("start", *INFRA)
        self.wait_services(INFRA)
        schema = self.worker("check")
        self.compose("--profile", "runtime", "start", "bff", "proxy")
        self.wait_services(("bff", "proxy"))
        self.http_ready()
        return {
            "running": True,
            "project": self.manifest["project"],
            "application": APP + "/b2b-app/",
            "schema": schema,
            "accounts_file": str(self.state / "credentials.json"),
        }

    def wait_services(self, names):
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            rows = json.loads(
                command(
                    [
                        "docker",
                        "inspect",
                        *[self.manifest["containers"][n] for n in names],
                    ]
                )
            )
            if any(r["State"]["Status"] in {"exited", "dead"} for r in rows):
                raise DemoError(
                    "A service exited during start; data and other services retained."
                )
            if all(
                r["State"]["Running"]
                and r["State"].get("Health", {}).get("Status", "healthy") == "healthy"
                for r in rows
            ):
                return
            time.sleep(1)
        raise DemoError("Service readiness deadline; no reset or force used.")

    def stop(self):
        self.load()
        rows = self.attest()
        pending = None
        # TERM only. Never docker stop's eventual SIGKILL; leave DBs running if drain fails.
        for name in (
            "proxy",
            "bff",
            "keycloak",
            "mongo",
            "test-postgres",
            "keycloak-db",
        ):
            row = rows[name]
            if not row["State"]["Running"]:
                continue
            if name == "keycloak" and all(
                rows[n]["State"]["Running"] for n in ("mongo", "test-postgres")
            ):
                pending = self.worker("check")
            if name == "proxy":
                command(["docker", "exec", row["Id"], "nginx", "-s", "quit"])
            else:
                command(["docker", "container", "kill", "--signal=SIGTERM", row["Id"]])
            deadline = time.monotonic() + 110
            while time.monotonic() < deadline:
                state = json.loads(
                    command(
                        ["docker", "inspect", "--format", "{{json .State}}", row["Id"]]
                    )
                )
                if not state["Running"]:
                    if state.get("OOMKilled") or state.get("ExitCode") not in (0, 143):
                        raise DemoError(
                            "Unclean service exit detected; remaining services retained for diagnosis."
                        )
                    break
                time.sleep(1)
            else:
                raise DemoError(
                    "Graceful drain deadline; no force used, remaining services left running."
                )
        self.attest()
        return {
            "running": False,
            "volumes_preserved": len(self.manifest["volumes"]),
            "durable_diagnostic_after_drain": pending,
            "project": self.manifest["project"],
        }

    def status(self):
        self.load()
        rows = self.attest()
        services = {
            n: {
                "state": r["State"]["Status"],
                "exit_code": r["State"].get("ExitCode"),
                "oom_killed": r["State"].get("OOMKilled", False),
                "health": r["State"].get("Health", {}).get("Status", "not-defined")
                if r["State"]["Running"]
                else "stopped",
            }
            for n, r in rows.items()
        }
        schemas = (
            self.worker("check")
            if all(rows[n]["State"]["Running"] for n in INFRA)
            else {
                "live_check": "unavailable: databases stopped",
                "last_init": {"mongo": 3, "postgres": 2},
            }
        )
        return {
            "project": self.manifest["project"],
            "revision": self.manifest["revision"],
            "application": APP + "/b2b-app/",
            "keycloak": ISSUER,
            "services": services,
            "schema": schemas,
            "ollama": ollama_status(
                os.environ.get("STOCKPILOT_DEMO_OLLAMA_URL")
                or self.manifest["host_ollama"]
            ),
            "disk": capacity(),
            "windows": windows_preflight(self.state / "ca.crt"),
            "accounts": [
                {"username": u, "organization": o, "role": role}
                for u, o, role in ACCOUNTS
            ],
            "accounts_file": str(self.state / "credentials.json"),
            "public_ca_file": str(self.state / "ca.crt"),
        }


def volume_identity(value):
    return {
        k: value.get(k)
        for k in ("Name", "CreatedAt", "Driver", "Labels", "Options", "Scope")
    }


def interactive_composition(config, passwords, installation):
    """Render the same topology privately: no DB host ports, authenticated Mongo."""
    c = copy.deepcopy(config)
    c["services"].pop("auth-mongo", None)
    for network in c["networks"].values():
        network.setdefault("labels", {}).update(
            {
                "stockpilot.audit.purpose": PURPOSE,
                "stockpilot.installation": installation,
            }
        )
    for service in ("mongo", "test-postgres"):
        c["services"][service].pop("ports", None)
    mongo = c["services"]["mongo"]
    mongo["environment"] = {
        "MONGO_INITDB_ROOT_USERNAME": "operator",
        "MONGO_INITDB_ROOT_PASSWORD": passwords["mongo_password"],
        "STOCKPILOT_RS_KEY": passwords["replica_key"],
    }
    # Persist the same internal replica credential in its named config volume.
    # No host key bind: Docker Desktop may rewrite such Sources on restart.
    mongo["entrypoint"] = [
        "bash",
        "-ec",
        (
            'if [ ! -e /data/configdb/replica.key ]; then umask 077; printf %s "$$STOCKPILOT_RS_KEY" > /data/configdb/replica.key; fi; '
            'test "$(sha256sum /data/configdb/replica.key | cut -d" " -f1)" = "$(printf %s "$$STOCKPILOT_RS_KEY" | sha256sum | cut -d" " -f1)" || exit 1; '
            'chown 999:999 /data/configdb/replica.key; chmod 400 /data/configdb/replica.key; exec docker-entrypoint.sh "$@"'
        ),
        "--",
    ]
    mongo["command"] += ["--keyFile", "/data/configdb/replica.key"]
    mongo["healthcheck"]["test"] = [
        "CMD",
        "mongosh",
        "--quiet",
        "--eval",
        "db.getSiblingDB('admin').auth(process.env.MONGO_INITDB_ROOT_USERNAME,process.env.MONGO_INITDB_ROOT_PASSWORD); try { rs.status() } catch(e) { rs.initiate({_id:'rs0',members:[{_id:0,host:'localhost:27017'}]}) }; if (!db.hello().isWritablePrimary) quit(1)",
    ]
    c["services"]["keycloak-db"]["environment"]["POSTGRES_PASSWORD"] = passwords[
        "keycloak_db_password"
    ]
    c["services"]["keycloak"]["environment"]["KC_DB_PASSWORD"] = passwords[
        "keycloak_db_password"
    ]
    # Private realm has concrete generated secrets; no bootstrap administration.
    c["services"]["keycloak"]["environment"].pop("BFF_TEST_USER_PASSWORD", None)
    c["services"]["keycloak"]["environment"].pop("BFF_TEST_CLIENT_SECRET", None)
    bff = c["services"]["bff"]
    for mount in bff["volumes"]:
        if mount["target"] == "/run/stockpilot/ca.crt":
            mount["source"] = str(Path(mount["source"]).with_name("ca.crt"))
    # ROOT is private; all mounts are RO. The proxy config must be group-readable.
    proxy_mount = next(
        v
        for v in c["services"]["proxy"]["volumes"]
        if v["target"] == "/etc/nginx/conf.d/default.conf"
    )
    Path(proxy_mount["source"]).chmod(0o640)
    return c
