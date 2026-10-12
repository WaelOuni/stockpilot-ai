"""Offline fixtures only: no backups of real data and no restoration."""
import gzip
import json
import os
from pathlib import Path
import subprocess

import pytest

from scripts import demo_backup_manifest as manifest


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    directory = tmp_path / 'private'
    directory.mkdir(mode=0o700)
    context = {'mongo_databases': list(manifest.PROTECTED_MONGO),
               'postgres_databases': list(manifest.DATABASES),
               'ignored_private_field': 'fixture-private-never-copy'}
    files = {'backup-context.json': json.dumps(context).encode(),
             'mongo-full.archive.gz': gzip.compress(b'synthetic archive fixture'),
             'postgres-globals.sql': b'-- fixture-private-no-real-role-verifier\n'}
    files.update({db + '.dump': b'PGDMPsynthetic-fixture' for db in manifest.DATABASES})
    for name, data in files.items():
        (directory / name).write_bytes(data)
        (directory / name).chmod(0o600)
    monkeypatch.setattr(manifest.subprocess, 'run', lambda *a, **k: None)
    return directory


def test_manifest_contains_hashes_and_explicitly_no_restore_claim(bundle):
    result = manifest.verify(bundle)
    assert len(result['files']) == 7
    assert all(len(item['sha256']) == 64 and item['bytes'] > 0 for item in result['files'])
    assert not result['restoration_tested']
    assert 'fixture-private' not in json.dumps(result)
    assert result['postgres']['databases'] == list(manifest.DATABASES)


@pytest.mark.parametrize('failure', ['missing', 'empty', 'public', 'symlink', 'corrupt'])
def test_invalid_archive_refused(bundle, failure):
    path = bundle / 'mongo-full.archive.gz'
    if failure == 'missing': path.unlink()
    if failure == 'empty': path.write_bytes(b'')
    if failure == 'public': path.chmod(0o644)
    if failure == 'symlink':
        path.unlink()
        path.symlink_to(bundle / 'postgres-globals.sql')
    if failure == 'corrupt': path.write_bytes(b'not-gzip')
    with pytest.raises((ValueError, OSError)):
        manifest.verify(bundle)


@pytest.mark.parametrize('key', ['mongo_databases', 'postgres_databases'])
def test_missing_database_refused(bundle, key):
    path = bundle / 'backup-context.json'
    context = json.loads(path.read_text())
    context[key].pop()
    path.write_text(json.dumps(context))
    with pytest.raises(ValueError): manifest.verify(bundle)


def test_pg_catalog_failure_refused(bundle, monkeypatch):
    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, ['pg_restore'], stderr=b'fixture-private-diagnostic')
    monkeypatch.setattr(manifest.subprocess, 'run', fail)
    monkeypatch.setattr('sys.argv', ['manifest', str(bundle)])
    assert manifest.main() == 1
    assert not (bundle / 'manifest.json').exists()


def test_manifest_is_private_immutable_and_detects_tampering(bundle, monkeypatch, capsys):
    monkeypatch.setattr('sys.argv', ['manifest', str(bundle)])
    assert manifest.main() == 0
    assert (bundle / 'manifest.json').stat().st_mode & 0o777 == 0o600
    assert manifest.main() == 1  # Never overwrite a previous manifest.
    monkeypatch.setattr('sys.argv', ['manifest', str(bundle), '--check'])
    assert manifest.main() == 0
    (bundle / 'postgres-globals.sql').write_bytes(b'changed-fixture')
    assert manifest.main() == 1
    assert 'fixture-private' not in capsys.readouterr().out


def test_repository_target_refused_before_backup_processing(monkeypatch):
    with pytest.raises(ValueError): manifest.verify(manifest.ROOT)
