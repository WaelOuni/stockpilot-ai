"""Synthetic-only non-disclosure regression tests; never load host configuration."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / 'scripts' / 'b2b_safe_diagnostic.py'
SPEC = importlib.util.spec_from_file_location('safe_diagnostic_under_test', TOOL)
diagnostic = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(diagnostic)

CANARY = 'SYNTHETIC_DIAGNOSTIC_CANARY_74d9'
URIS = (
    f'postgresql://user:{CANARY}@synthetic.invalid/database',
    f'https://{CANARY}.invalid/model',
    f'mongodb://{CANARY}.invalid/database',
    f'http://synthetic.invalid/{CANARY}\n\x1b[31m',
)
FREE_FIELDS = (
    'ORIGIN', 'ISSUER', 'OIDC_CA_FILE', 'CLIENT_ID', 'CLIENT_SECRET',
    'MONGO_URI', 'MONGO_DATABASE', 'CONFIGURATION_KEY', 'ASSISTANT_POSTGRES_URI',
    'ASSISTANT_SCHEMA', 'ASSISTANT_OLLAMA_URL', 'ASSISTANT_MODEL',
    'ASSISTANT_EMBEDDING_MODEL', 'TRUSTED_PROXY_IPS',
)


class Explosive:
    def __str__(self):
        raise AssertionError(CANARY)

    def __repr__(self):
        raise AssertionError(CANARY)


class SafeDiagnosticTests(unittest.TestCase):
    def run_cli(self, payload=b'{}', argv=None, **kwargs):
        stdout, stderr, output = io.StringIO(), io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = diagnostic.main(['--stdin'] if argv is None else argv,
                                   source=io.BytesIO(payload), output=output, **kwargs)
        text = output.getvalue() + stdout.getvalue() + stderr.getvalue()
        self.assertNotIn(CANARY, text)
        self.assertEqual(stdout.getvalue(), '')
        self.assertEqual(stderr.getvalue(), '')
        return code, json.loads(output.getvalue())

    def field(self, key, value):
        return diagnostic.project({key: value})['fields'][key]

    def test_allowlist_exactly_matches_documented_public_scalars(self):
        expected = {'BFF_MODE', 'BFF_ASSISTANT_ENABLED', 'BFF_ASSISTANT_ALLOW_HTTP',
                    'BFF_ALLOW_LOOPBACK_HTTP_ISSUER', 'BFF_MAX_INFLIGHT',
                    'BFF_REQUESTS_PER_MINUTE', 'BFF_SESSION_ABSOLUTE_SECONDS',
                    'BFF_SESSION_IDLE_SECONDS', 'BFF_TRANSACTION_SECONDS',
                    'BFF_BODY_TIMEOUT_SECONDS', 'BFF_ASSISTANT_TIMEOUT_SECONDS'}
        self.assertEqual(set(diagnostic.FIELDS), expected)
        self.assertEqual(len(diagnostic.FIELDS), len(expected))

    def test_empty_input_is_absent_and_never_claims_config_validation(self):
        code, report = self.run_cli()
        self.assertEqual(code, 0)
        self.assertEqual({v['status'] for v in report['fields'].values()}, {'absent'})
        self.assertEqual(report['idle_not_above_absolute'], 'unknown')
        self.assertIn('not_configuration_validation', report['scope'])

    def test_every_allowed_field_rejects_every_uri_without_echo(self):
        for key in diagnostic.FIELDS:
            for uri in URIS:
                with self.subTest(key=key, uri_kind=URIS.index(uri)):
                    code, report = self.run_cli(json.dumps({key: uri}).encode())
                    self.assertEqual(code, 2)
                    self.assertEqual(report['fields'][key], {'status': 'invalid'})

    def test_every_free_field_and_unknown_key_omitted_without_inspection(self):
        keys = tuple(f'BFF_{name}' for name in FREE_FIELDS)
        for key in keys + tuple(name.lower() for name in keys) + (CANARY, 'arbitrary\n\u2603'):
            for uri in URIS:
                with self.subTest(key_kind='unknown' if key == CANARY else key,
                                  uri_kind=URIS.index(uri)):
                    code, report = self.run_cli(json.dumps({key: uri, 'nested': {'deep': uri}}).encode())
                    self.assertEqual(code, 0)
                    self.assertTrue(all(v == {'status': 'absent'} for v in report['fields'].values()))

    def test_regression_broad_assistant_selection_leaks_but_projection_does_not(self):
        supplied = {'BFF_ASSISTANT_OLLAMA_URL': URIS[1], 'BFF_ASSISTANT_ENABLED': True}
        unsafe = {k: v for k, v in supplied.items() if 'ASSISTANT' in k}
        self.assertIn(CANARY, json.dumps(unsafe))
        safe = json.dumps(diagnostic.project(supplied))
        self.assertNotIn(CANARY, safe)
        self.assertEqual(diagnostic.project(supplied)['fields']['BFF_ASSISTANT_ENABLED']['value'], True)

    def test_objects_never_stringified_and_disallowed_objects_never_examined(self):
        for key in diagnostic.FIELDS:
            with self.subTest(key=key):
                self.assertEqual(self.field(key, Explosive()), {'status': 'invalid'})
        result = diagnostic.project({'BFF_ASSISTANT_OLLAMA_URL': Explosive()})
        self.assertNotIn(CANARY, json.dumps(result))

    def test_top_level_object_required_without_echo(self):
        for raw in (b'[]', b'null', b'1', json.dumps(URIS[0]).encode()):
            with self.subTest(kind=raw[:1]):
                code, report = self.run_cli(raw)
                self.assertEqual(code, 2)
                self.assertEqual(report['status'], 'refused')
        with self.assertRaises(diagnostic.DiagnosticRefused) as error:
            diagnostic.project(Explosive())
        self.assertNotIn(CANARY, str(error.exception))

    def test_boolean_values_are_closed(self):
        for key in diagnostic.BOOLS:
            for raw, expected in ((True, True), (False, False), ('true', True), ('false', False)):
                with self.subTest(key=key, raw=raw):
                    self.assertEqual(self.field(key, raw), {'status': 'observed', 'value': expected})
            for raw in (0, 1, None, [], {}, 'True', '1', 'false\n', ' false'):
                with self.subTest(key=key, invalid_type=type(raw).__name__):
                    self.assertEqual(self.field(key, raw), {'status': 'invalid'})

    def test_integer_boundaries_and_type_confusion(self):
        for key, (lo, hi) in diagnostic.INTS.items():
            for raw in (lo, hi, str(lo), str(hi)):
                with self.subTest(key=key, raw=raw):
                    self.assertEqual(self.field(key, raw)['status'], 'observed')
            for raw in (lo - 1, hi + 1, True, False, 1.0, None, '1e1', '+1', ' 1', '1\n', '1.0', '١'):
                with self.subTest(key=key, invalid_type=type(raw).__name__):
                    self.assertEqual(self.field(key, raw), {'status': 'invalid'})

    def test_numbers_bounds_nonfinite_and_strict_types(self):
        for key, (lo, hi, inclusive) in diagnostic.NUMBERS.items():
            valid = (hi, float(hi), str(hi), lo if inclusive else 0.25)
            invalid = (hi + 1, lo - 1, True, False, None, [], '1e1', 'NaN', 'Infinity',
                       '-1', ' 1', '1\n', float('nan'), float('inf'), -float('inf'))
            for raw in valid:
                with self.subTest(key=key, valid_type=type(raw).__name__):
                    self.assertEqual(self.field(key, raw)['status'], 'observed')
            for raw in invalid + (() if inclusive else (lo,)):
                with self.subTest(key=key, invalid_type=type(raw).__name__):
                    self.assertEqual(self.field(key, raw), {'status': 'invalid'})

    def test_arbitrary_large_integer_is_invalid_not_projection_exception(self):
        for key in diagnostic.NUMBERS:
            with self.subTest(key=key):
                self.assertEqual(self.field(key, 10 ** 1000), {'status': 'invalid'})

    def test_modes_closed_and_case_sensitive(self):
        for mode in ('disabled', 'b2b_auth_test', 'b2b_business_test'):
            self.assertEqual(self.field('BFF_MODE', mode)['value'], mode)
        for mode in ('DISABLED', 'production', '', True, 1, [], {}, URIS[0]):
            self.assertEqual(self.field('BFF_MODE', mode), {'status': 'invalid'})

    def test_session_relation_is_not_accepted_when_inconsistent_or_unknown(self):
        for absolute, idle, expected, code in ((30, 30, 'consistent', 0),
                                               (30, 31, 'inconsistent', 2),
                                               (30, None, 'unknown', 2)):
            actual, report = self.run_cli(json.dumps({'BFF_SESSION_ABSOLUTE_SECONDS': absolute,
                                                      'BFF_SESSION_IDLE_SECONDS': idle}).encode())
            self.assertEqual(report['idle_not_above_absolute'], expected)
            self.assertEqual(actual, code)

    def test_duplicate_keys_refused_including_unknown_nested_keys(self):
        for raw in (b'{"BFF_MODE":"disabled","BFF_MODE":"b2b_auth_test"}',
                    ('{"' + CANARY + '":1,"' + CANARY + '":2}').encode(),
                    b'{"unknown":{"duplicate":1,"duplicate":2}}'):
            code, report = self.run_cli(raw)
            self.assertEqual((code, report['status']), (2, 'refused'))

    def test_json_syntax_encoding_depth_and_constants_fail_closed(self):
        for raw in (b'{', b'\xff', b'NaN', b'Infinity', b'-Infinity',
                    b'{"BFF_BODY_TIMEOUT_SECONDS":NaN}',
                    ('{"' + CANARY + '":').encode(),
                    b'[' * 1500 + b'0' + b']' * 1500):
            code, report = self.run_cli(raw)
            self.assertEqual((code, report['status']), (2, 'refused'))

    def test_large_stdin_and_wrong_source_type_refused(self):
        for raw in (b' ' * (diagnostic.LIMIT + 1),):
            code, report = self.run_cli(raw)
            self.assertEqual((code, report['status']), (2, 'refused'))
        output = io.StringIO()
        self.assertEqual(diagnostic.main(['--stdin'], source=io.StringIO(CANARY), output=output), 2)
        self.assertNotIn(CANARY, output.getvalue())

    def test_stdin_read_is_bounded(self):
        source, output = mock.Mock(), io.StringIO()
        source.read.return_value = b'{}'
        self.assertEqual(diagnostic.main(['--stdin'], source=source, output=output), 0)
        source.read.assert_called_once_with(diagnostic.LIMIT + 1)

    def test_paths_arguments_and_help_never_echo(self):
        for argv in ([], [CANARY], ['--file'], ['--stdin', CANARY],
                     ['--help', CANARY], ['--file', CANARY + '/missing.json']):
            code, report = self.run_cli(argv=argv)
            self.assertEqual((code, report['status']), (2, 'refused'))
        output, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(diagnostic.main(['--help'], source=io.BytesIO(), output=output), 0)
        self.assertNotIn(CANARY, output.getvalue() + err.getvalue())
        self.assertIn('--stdin', output.getvalue())

    def test_file_open_exception_payload_is_suppressed(self):
        for error in (OSError(CANARY), ValueError(URIS[0]), RuntimeError(URIS[1])):
            with mock.patch.object(diagnostic, '_read', side_effect=error):
                code, report = self.run_cli(argv=['--file', CANARY])
            self.assertEqual((code, report['status']), (2, 'refused'))

    def test_regular_private_synthetic_file_and_oversize(self):
        with tempfile.TemporaryDirectory(prefix='safe-diagnostic-synthetic-') as temp:
            path = Path(temp) / 'input.json'
            path.write_bytes(b'{"BFF_MODE":"disabled"}')
            if os.name != 'nt':
                path.chmod(0o600)
            code, report = self.run_cli(argv=['--file', str(path)])
            self.assertEqual(code, 0)
            self.assertEqual(report['fields']['BFF_MODE']['value'], 'disabled')
            path.write_bytes(b' ' * (diagnostic.LIMIT + 1))
            code, report = self.run_cli(argv=['--file', str(path)])
            self.assertEqual((code, report['status']), (2, 'refused'))

    def test_symlink_target_or_parent_is_refused_before_open(self):
        for at_parent in (False, True):
            with self.subTest(at_parent=at_parent):
                count = 0
                def fake_symlink(path):
                    nonlocal count
                    count += 1
                    return count == (2 if at_parent else 1)
                with mock.patch.object(Path, 'is_symlink', fake_symlink), \
                        mock.patch.object(diagnostic.os, 'open') as opened:
                    code, report = self.run_cli(argv=['--file', CANARY + '/input.json'])
                opened.assert_not_called()
                self.assertEqual((code, report['status']), (2, 'refused'))

    def test_directory_input_refused(self):
        with tempfile.TemporaryDirectory(prefix='safe-diagnostic-synthetic-') as temp:
            code, report = self.run_cli(argv=['--file', temp])
            self.assertEqual((code, report['status']), (2, 'refused'))

    def test_real_symlink_refused_when_platform_allows_creation(self):
        with tempfile.TemporaryDirectory(prefix='safe-diagnostic-synthetic-') as temp:
            path, link = Path(temp) / 'input.json', Path(temp) / 'link.json'
            path.write_bytes(b'{}')
            try:
                link.symlink_to(path)
            except (OSError, NotImplementedError):
                self.skipTest('native symlink creation unavailable; deterministic target/parent mocks still run')
            code, report = self.run_cli(argv=['--file', str(link)])
            self.assertEqual((code, report['status']), (2, 'refused'))

    @unittest.skipIf(os.name == 'nt', 'POSIX owner/mode semantics only')
    def test_other_readable_synthetic_file_refused(self):
        with tempfile.TemporaryDirectory(prefix='safe-diagnostic-synthetic-') as temp:
            path = Path(temp) / 'input.json'
            path.write_bytes(b'{}')
            path.chmod(0o604)
            code, report = self.run_cli(argv=['--file', str(path)])
            self.assertEqual((code, report['status']), (2, 'refused'))

    def test_real_process_stdout_stderr_and_exit_status(self):
        for payload, expected in ((b'{}', 0), (json.dumps({'BFF_MODE': URIS[0]}).encode(), 2),
                                  (b'{"BFF_MODE":NaN}', 2)):
            result = subprocess.run([sys.executable, '-B', str(TOOL), '--stdin'],
                                    input=payload, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    timeout=10, check=False)
            self.assertEqual(result.returncode, expected)
            self.assertEqual(result.stderr, b'')
            self.assertNotIn(CANARY.encode(), result.stdout + result.stderr)
            json.loads(result.stdout)


if __name__ == '__main__':
    unittest.main()
