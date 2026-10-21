"""Two fixed loopback port sets for the disposable BFF harness only."""

import os

preset = os.environ.get("B2B_TEST_PORT_SET", "default")
if preset not in {"default", "isolated"}:
    raise RuntimeError("Unknown disposable BFF port set.")
APP, OIDC, MONGO, POSTGRES = (
    (59914, 59915, 59916, 59918)
    if preset == "isolated"
    else (57914, 57915, 57916, 57918)
)
ORIGIN = f"https://127.0.0.1:{APP}"
