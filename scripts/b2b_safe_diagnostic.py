"""Bounded BFF configuration projection. Never emit arbitrary configuration values.

This is a display diagnostic, not a connection test or a security preflight.
All addresses, credentials, paths, identifiers and freely chosen strings are omitted.
"""
import json
import math
import os
from pathlib import Path
import re
import stat
import sys

LIMIT = 32768
BOOLS = ('BFF_ASSISTANT_ENABLED', 'BFF_ASSISTANT_ALLOW_HTTP', 'BFF_ALLOW_LOOPBACK_HTTP_ISSUER')
INTS = {'BFF_MAX_INFLIGHT': (1, 32), 'BFF_REQUESTS_PER_MINUTE': (1, 10000),
        'BFF_SESSION_ABSOLUTE_SECONDS': (30, 3600), 'BFF_SESSION_IDLE_SECONDS': (10, 1800),
        'BFF_TRANSACTION_SECONDS': (30, 300)}
NUMBERS = {'BFF_BODY_TIMEOUT_SECONDS': (0, 10, False),
           'BFF_ASSISTANT_TIMEOUT_SECONDS': (1, 90, True)}
MODES = frozenset(('disabled', 'b2b_auth_test', 'b2b_business_test'))
FIELDS = ('BFF_MODE', *BOOLS, *INTS, *NUMBERS)


class DiagnosticRefused(Exception):
    """Messages are fixed codes only; never retain input in an exception."""


def _value(key, value):
    if key == 'BFF_MODE':
        return value if type(value) is str and value in MODES else None
    if key in BOOLS:
        if type(value) is bool:
            return value
        if type(value) is str and value in ('true', 'false'):
            return value == 'true'
        return None
    if key in INTS:
        if type(value) is str:
            if len(value) > 5 or re.fullmatch(r'[0-9]+', value) is None:
                return None
            value = int(value)
        if type(value) is not int:
            return None
        lo, hi = INTS[key]
        return value if lo <= value <= hi else None
    if key in NUMBERS:
        if type(value) is str:
            if len(value) > 12 or re.fullmatch(r'[0-9]+(?:\.[0-9]+)?', value) is None:
                return None
            value = float(value)
        if type(value) not in (int, float) or (type(value) is float and not math.isfinite(value)):
            return None
        lo, hi, inclusive = NUMBERS[key]
        return value if (value >= lo if inclusive else value > lo) and value <= hi else None
    return None


def project(configuration):
    if type(configuration) is not dict:
        raise DiagnosticRefused('configuration_object_required')
    fields = {}
    for key in FIELDS:
        if key not in configuration:
            fields[key] = {'status': 'absent'}
            continue
        value = _value(key, configuration[key])
        fields[key] = {'status': 'invalid'} if value is None else {'status': 'observed', 'value': value}
    absolute, idle = fields['BFF_SESSION_ABSOLUTE_SECONDS'], fields['BFF_SESSION_IDLE_SECONDS']
    relation = 'unknown'
    if absolute['status'] == idle['status'] == 'observed':
        relation = 'consistent' if idle['value'] <= absolute['value'] else 'inconsistent'
    return {'version': 1, 'scope': 'bounded_display_only_not_configuration_validation',
            'fields': fields, 'idle_not_above_absolute': relation,
            'addresses_credentials_and_other_fields': 'omitted'}


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise DiagnosticRefused('duplicate_json_key')
        value[key] = item
    return value


def _read(path):
    target = Path(path).absolute()
    if any(p.is_symlink() for p in (target, *target.parents)):
        raise DiagnosticRefused('input_refused')
    fd = os.open(target, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    try:
        facts = os.fstat(fd)
        if not stat.S_ISREG(facts.st_mode) or facts.st_size > LIMIT:
            raise DiagnosticRefused('input_refused')
        if os.name != 'nt' and (facts.st_mode & 0o007 or facts.st_uid != os.getuid()):
            raise DiagnosticRefused('input_refused')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            return stream.read(LIMIT + 1)
    finally:
        os.close(fd)


def main(argv=None, *, source=None, output=None):
    argv = sys.argv[1:] if argv is None else argv
    source = sys.stdin.buffer if source is None else source
    output = sys.stdout if output is None else output
    try:
        if argv == ['--help']:
            output.write('Usage: b2b_safe_diagnostic.py --stdin | --file PRIVATE_JSON\n')
            return 0
        if argv == ['--stdin']:
            raw = source.read(LIMIT + 1)
        elif len(argv) == 2 and argv[0] == '--file' and type(argv[1]) is str:
            raw = _read(argv[1])
        else:
            raise DiagnosticRefused('arguments_refused')
        if type(raw) is not bytes or len(raw) > LIMIT:
            raise DiagnosticRefused('input_refused')
        configuration = json.loads(raw, object_pairs_hook=_pairs,
                                   parse_constant=lambda unused: (_ for _ in ()).throw(DiagnosticRefused('json_refused')))
        result = project(configuration)
        output.write(json.dumps(result, allow_nan=False, sort_keys=True) + '\n')
        return 2 if any(v['status'] == 'invalid' for v in result['fields'].values()) or result['idle_not_above_absolute'] == 'inconsistent' else 0
    except Exception:
        # Input, path, JSON and OS error details can themselves contain credentials.
        output.write('{"status":"refused","reason":"input_or_projection_refused"}\n')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
