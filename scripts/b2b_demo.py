"""Single canonical B2B operator: explicit local lifecycle or disposable validation.

Usage: b2b_demo.py {init,start,status,stop,resume}
       b2b_demo.py validate [--regressions]
Local state/secrets stay outside Git. init is an explicit installation action;
validation never initializes the canonical persistent directory.
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=["validate", "init", "start", "status", "stop", "resume"]
    )
    parser.add_argument("--regressions", action="store_true")
    options = parser.parse_args()
    if options.action != "validate":
        if options.regressions:
            parser.error("--regressions is reserved for disposable validate.")
        import json

        from scripts.b2b_interactive import DemoError, InteractiveDemo

        try:
            operator = InteractiveDemo()
            with operator.locked():
                if options.action == "init":
                    result = operator.init(
                        os.environ.get("STOCKPILOT_DEMO_OLLAMA_URL", ""),
                        os.environ.get("STOCKPILOT_DEMO_OLLAMA_CONTAINER_URL", ""),
                    )
                elif options.action in {"start", "resume"}:
                    result = operator.start()
                else:
                    result = getattr(operator, options.action)()
            print(json.dumps(result, indent=2))
        except DemoError as error:
            raise SystemExit(str(error)) from None
        except (OSError, ValueError, KeyError):
            raise SystemExit(
                "Local lifecycle refused safely. Check private state and the canonical guide; no reset is performed."
            ) from None
        return
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
