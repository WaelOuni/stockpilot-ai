"""Verify externally visible HTTPS routes, discovery and BFF login initiation."""
import argparse
import json
import os
import subprocess
import sys
from urllib.parse import parse_qs, urlsplit


def verify(args):
    app, auth = os.environ["APP_HOST"], os.environ["AUTH_HOST"]
    origin = "https://" + app
    issuer = "https://" + auth + "/realms/stockpilot"

    def request(host, path, status, *extra):
        cmd = ["curl", "--silent", "--show-error", "--noproxy", "*", "--max-time", "15",
               "--proto", "=https", "--write-out", "\n%{http_code}"]
        if args.loopback_port:
            cmd += ["--connect-to", f"{host}:443:127.0.0.1:{args.loopback_port}"]
        if args.ca:
            cmd += ["--cacert", args.ca]
        body, actual = subprocess.check_output([*cmd, *extra, f"https://{host}{path}"]).rsplit(b"\n", 1)
        if int(actual) != status:
            raise ValueError(f"Unexpected {actual.decode()} at {path}")
        return body

    if b'<div id="app">' not in request(app, "/b2b-app/", 200):
        raise ValueError("Missing application shell")
    request(app, "/health", 404)
    request(app, "/api/", 404)
    request(auth, "/admin/", 404)
    discovery = json.loads(request(auth, "/realms/stockpilot/.well-known/openid-configuration", 200))
    if discovery["issuer"] != issuer or not discovery["authorization_endpoint"].startswith(issuer + "/"):
        raise ValueError("Discovery issuer/endpoint mismatch")
    request(app, "/b2b/auth/session", 401)
    headers = ["--header", "Origin: " + origin, "--header", "Content-Type: application/json",
               "--data", '{"organization_id":"vps-acceptance"}']
    request(app, "/b2b/auth/login", 403, *headers)
    body = request(app, "/b2b/auth/login", 200, *headers, "--header", "X-BFF-CSRF: login")
    url = urlsplit(json.loads(body)["authorization_url"])
    params = parse_qs(url.query)
    expected = {"redirect_uri": origin + "/b2b/auth/callback", "response_type": "code",
                "client_id": "stockpilot-bff", "code_challenge_method": "S256"}
    if (url._replace(query="").geturl() != discovery["authorization_endpoint"]
            or any(params.get(k) != [v] for k, v in expected.items())
            or not all(params.get(k) for k in ("state", "nonce", "code_challenge"))):
        raise ValueError("Unexpected OIDC code flow")
    print("HTTPS, B2B routes, OIDC discovery, callback, PKCE and login CSRF verified.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ca", help="Trust anchor for an isolated lab")
    parser.add_argument("--loopback-port", type=int, help="Connect to a local qualification port, retaining SNI")
    try:
        verify(parser.parse_args())
    except (OSError, KeyError, ValueError, subprocess.SubprocessError) as error:
        sys.exit(f"VPS verification failed: {error}" if type(error) is ValueError else "VPS verification failed")
