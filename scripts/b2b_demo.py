"""Single operator entrypoint: isolated validation only, never persistent install.

Usage: .venv/bin/python scripts/b2b_demo.py validate [--regressions]
Secrets/backups remain in a mode-0700 ignored runtime directory. No production
credentials, public deployment, migration at app startup, or broad cleanup.
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["validate"])
    parser.add_argument("--regressions", action="store_true")
    options = parser.parse_args()
    endpoint = os.environ.get("STOCKPILOT_DEMO_OLLAMA_URL")
    if not endpoint:
        parser.error("Explicit STOCKPILOT_DEMO_OLLAMA_URL required; no model download.")
    if not os.environ.get("STOCKPILOT_DEMO_OLLAMA_CONTAINER_URL"):
        parser.error("Explicit private STOCKPILOT_DEMO_OLLAMA_CONTAINER_URL required.")
    os.environ["V1383_OLLAMA_URL"] = endpoint
    from scripts.tests.validate_bff_keycloak import main as campaign

    sys.argv = [sys.argv[0], "--assistant", "--canonical"]
    if options.regressions:
        sys.argv.append("--regressions")
    campaign()


if __name__ == "__main__":
    main()
