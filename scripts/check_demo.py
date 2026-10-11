"""Read-only DEMO identity, provenance and structure diagnosis; never initialize."""
import json
import subprocess
import sys

import psycopg
from pymongo.errors import PyMongoError

from stockpilot.demo_runtime import preflight_demo, resolve_demo_settings


def main():
    try:
        result = preflight_demo(resolve_demo_settings())
        print(json.dumps({'target': 'stockpilot_demo', 'read_only': True,
                          'mongo': result.mongo_summary, 'rag_rows': result.rag_document_count}, indent=2))
        return 0
    except (ValueError, RuntimeError) as error:
        print(f'DEMO diagnosis refused: {error}', file=sys.stderr)
    except (OSError, subprocess.SubprocessError, PyMongoError, psycopg.Error):
        print('DEMO diagnosis failed: infrastructure details withheld; no data changed.', file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
