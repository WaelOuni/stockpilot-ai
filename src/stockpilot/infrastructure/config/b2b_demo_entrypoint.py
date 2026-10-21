"""Image-contained launcher for the explicitly enabled canonical test BFF.

Importing this module performs no I/O. Configuration is a private RO mounted
secret, never an environment file in the repository or a browser payload.
"""

import json
import logging
import os
from pathlib import Path


def load_configuration(path=Path("/run/stockpilot/bff.json")):
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 32768:
            raise ValueError
        if path.stat().st_mode & 0o007:
            raise ValueError
        configuration = json.loads(path.read_text())
        if not isinstance(configuration, dict) or not configuration:
            raise ValueError
        if not all(
            isinstance(key, str) and key.startswith("BFF_") and isinstance(value, str)
            for key, value in configuration.items()
        ):
            raise ValueError
        return configuration
    except (ValueError, OSError):
        raise RuntimeError("Private BFF configuration required.") from None


def main():
    os.environ.update(load_configuration())
    logger = logging.getLogger("stockpilot.bff.http")
    logger.setLevel(logging.INFO)
    logger.addHandler(logging.StreamHandler())
    logger.propagate = False
    import uvicorn

    uvicorn.run(
        "stockpilot.bff:create_app",
        factory=True,
        host="0.0.0.0",
        port=8000,
        access_log=False,
        proxy_headers=False,
        timeout_graceful_shutdown=95,
    )


if __name__ == "__main__":
    main()
