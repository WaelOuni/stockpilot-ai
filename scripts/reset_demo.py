"""Explicit interactive reset of the fixed, independently verified DEMO stores."""
import sys
import subprocess

from pymongo.errors import PyMongoError
import psycopg

from scripts.run_demo import ensure_port_is_free, API_PORT, WEB_PORT
from stockpilot.demo_runtime import resolve_demo_settings
from stockpilot.demo_lifecycle import demo_resources, reset_workspace


def main():
    try:
        settings = resolve_demo_settings()
        ensure_port_is_free(API_PORT)
        ensure_port_is_free(WEB_PORT)
        with demo_resources(settings) as resources:
            reset_workspace(settings, resources)
        return 0
    except (ValueError, RuntimeError) as error:
        print(f'DEMO reset refused: {error}', file=sys.stderr)
    except (EOFError, KeyboardInterrupt):
        print('DEMO reset cancelled; confirmation was not completed.', file=sys.stderr)
    except (OSError, subprocess.SubprocessError, PyMongoError, psycopg.Error):
        print('DEMO reset failed; infrastructure details withheld. '
              'Inspect DEMO state; no automatic retry.', file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
