"""Provision and run the real Vue campaign inside the already verified BFF harness."""

import json
import shutil
import subprocess
import sys
from pathlib import Path


def run_browser(db, env, directory, cert, passed, *, continuity=False):
    # The parent has verified the local tmpfs project and disposable database.
    db.b2b_memberships.update_one(
        {"membership_id": "fixture-membership"},
        {"$set": {"role": "ORGANIZATION_ADMIN"}},
    )
    db.b2b_memberships.update_one(
        {"membership_id": "fixture-other-membership"}, {"$set": {"role": "USER"}}
    )
    for org, name, price in (
        ("fixture-org", "Perceuse A", 4900),
        ("fixture-other", "Perceuse B", 9900),
    ):
        db.b2b_products.update_one(
            {"organization_id": org, "sku": "DRILL-001"},
            {"$set": {"name": name, "price_minor": price}},
        )
    # Trust only this harness certificate, in a disposable Linux NSS database.
    # Chromium still verifies the certificate, hostname and validity normally.
    certutil = env.get("B2B_TEST_CERTUTIL") or shutil.which("certutil")
    if not certutil:
        raise RuntimeError("libnss3-tools certutil is required for isolated browser TLS trust.")
    browser_home = directory / "browser-home"
    nssdb = browser_home / ".pki" / "nssdb"
    nssdb.mkdir(parents=True)
    for arguments in (
        ["-N", "--empty-password"],
        ["-A", "-n", "StockPilot isolated test", "-t", "C,,", "-i", str(cert)],
    ):
        subprocess.run([certutil, "-d", "sql:" + str(nssdb), *arguments], check=True, capture_output=True)
    node = shutil.which("node")
    if not node:
        raise RuntimeError("Node runtime unavailable.")
    browser_env = env | {
        "NODE_EXTRA_CA_CERTS": str(cert),
        "HOME": str(browser_home),
        "PLAYWRIGHT_BROWSERS_PATH": env.get("PLAYWRIGHT_BROWSERS_PATH", str(Path.home() / ".cache" / "ms-playwright")),
        "B2B_BROWSER_DATABASE": db.name,
        "MONGO_TEST_URI": env["BFF_MONGO_URI"],
        "B2B_TEST_PYTHON": sys.executable,
        "B2B_BROWSER_DIRECTORY": str(directory),
        "B2B_BROWSER_REPORT": str(directory / "browser-report.json"),
    }
    with (directory / "browser-private.log").open("w") as output:
        result = subprocess.run(
            [node, "b2b-session-continuity.mjs" if continuity else "b2b-browser.mjs"],
            cwd=Path(__file__).resolve().parents[2] / "frontend",
            env=browser_env,
            stdout=output,
            stderr=subprocess.STDOUT,
            timeout=240,
            check=False,
        )
    report_path = directory / "browser-report.json"
    if not report_path.is_file():
        raise RuntimeError("Browser campaign did not preserve a report.")
    report = json.loads(report_path.read_text())
    if result.returncode or not report["passed"]:
        raise RuntimeError("Real browser campaign failed; inspect private diagnostics.")
    for name in report["checks"]:
        passed("Chromium: " + name)
