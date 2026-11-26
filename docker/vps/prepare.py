"""Prepare private configuration for a new, synthetic v0.2.9 VPS qualification."""
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sys
from urllib.parse import urlsplit
from uuid import uuid4


def hostname(key):
    host = os.environ[key]
    label = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    if len(host) > 253 or not re.fullmatch(rf"{label}(?:\.{label})+", host):
        raise ValueError(f"Invalid DNS name in {key}")
    return host


def main():
    private = Path(os.environ["PRIVATE_DIR"])
    if not private.is_absolute() or private.resolve() != private or private.is_relative_to(Path(__file__).resolve().parents[2]):
        raise ValueError("Choose a new absolute directory outside the checkout")
    app, auth = hostname("APP_HOST"), hostname("AUTH_HOST")
    proxy = ipaddress.IPv4Address(os.environ["PROXY_IP"])
    subnet = ipaddress.IPv4Network(os.environ["EDGE_SUBNET"])
    pool = ipaddress.IPv4Network(os.environ["EDGE_POOL"])
    if not pool.subnet_of(subnet) or proxy in pool:
        raise ValueError("Reserve the proxy IP outside the dynamic edge pool")
    if app == auth or proxy not in subnet or proxy in (subnet.network_address, subnet.broadcast_address):
        raise ValueError("Use distinct hosts and an available proxy address in the edge subnet")
    if os.getegid() != int(os.environ["RUNTIME_GID"]):
        raise ValueError("Set RUNTIME_GID to the effective group of the provisioning user")
    endpoint = os.environ.get("INFERENCE_URL", "")
    if endpoint:
        url = urlsplit(endpoint)
        if url.scheme != "https" or not url.hostname or url.username is not None or url.password is not None or url.path not in ("", "/") or url.query or url.fragment:
            raise ValueError("Expected an HTTPS inference origin")
    # All files are individually bound read-only. Only selected container UIDs
    # need the shared files readable. The parent directory is owner-only.
    os.umask(0o077)
    private.mkdir(mode=0o700, exist_ok=False)
    def write(name, value, mode=0o444):
        path = private / name
        path.write_text(value)
        path.chmod(mode)

    mongo, pg, kc_pg, kc, client, app_mongo, app_pg = (secrets.token_urlsafe(48) for _ in range(7))
    db = "stockpilot_b2b_test_" + uuid4().hex
    origin = f"https://{app}"
    bff = {
        "BFF_MODE": "b2b_business_test", "BFF_ORIGIN": origin,
        "BFF_ISSUER": f"https://{auth}/realms/stockpilot", "BFF_CLIENT_ID": "stockpilot-bff",
        "BFF_CLIENT_SECRET": client, "BFF_CONFIGURATION_KEY": secrets.token_urlsafe(48),
        "BFF_TRUSTED_PROXY_IPS": json.dumps([str(proxy)]),
        "BFF_OIDC_CA_FILE": "/run/stockpilot/oidc-ca.pem",
        "BFF_MONGO_DATABASE": db,
        "BFF_MONGO_URI": f"mongodb://stockpilot_bff:{app_mongo}@mongo:27017/{db}?replicaSet=rs0",
        "BFF_ASSISTANT_POSTGRES_URI": f"postgresql://stockpilot_bff:{app_pg}@postgres:55432/stockpilot_test_vps",
        "BFF_ASSISTANT_ENABLED": "true" if endpoint else "false",
        "BFF_ASSISTANT_OLLAMA_URL": endpoint, "BFF_ASSISTANT_MODEL": "qwen2.5:7b",
        "BFF_ASSISTANT_EMBEDDING_MODEL": "nomic-embed-text",
    }
    write("bff.json", json.dumps(bff), 0o640)
    write("operator.json", json.dumps(dict(database=db, mongo=mongo, pg=pg, app_mongo=app_mongo, app_pg=app_pg)), 0o640)
    for name, value in (("mongo-password", mongo), ("pg-password", pg), ("kc-pg-password", kc_pg),
                        ("kc-password", kc), ("rs-key", secrets.token_hex(96))):
        write(name, value + "\n")
    write("keycloak.conf", f"db-password={kc}\n")
    template = Path(__file__).resolve().parents[1] / "keycloak/stockpilot-dev-realm.json"
    realm = json.loads(template.read_text())
    realm.pop("users")
    realm.update(realm="stockpilot", sslRequired="all", loginTheme="stockpilot")
    oidc, = realm["clients"]
    oidc.update(secret=client, redirectUris=[origin + "/b2b/auth/callback"], webOrigins=[origin])
    oidc["attributes"]["post.logout.redirect.uris"] = origin + "/b2b/auth/logged-out"
    write("realm.json", json.dumps(realm))
    shutil.copyfile("/etc/ssl/certs/ca-certificates.crt", private / "oidc-ca.pem")
    (private / "oidc-ca.pem").chmod(0o444)
    (private / "tls").mkdir(mode=0o750)
    (private / "tls").chmod(0o750)


if __name__ == "__main__":
    try:
        main()
    except (OSError, KeyError, ValueError) as error:
        sys.exit(f"Preparation refused ({type(error).__name__}). Verify parameters and use a fresh directory.")
