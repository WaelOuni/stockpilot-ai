"""Offline backup-file verification. No Docker, database connection or restoration.

Only run after every dump completed successfully during the approved write pause.
Checks hashes, gzip CRC and PostgreSQL archive catalogs; does not prove restoration.
"""
import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import subprocess

DATABASES = ('postgres', 'stockpilot', 'stockpilot_demo', 'stockpilot_e2e', 'stockpilot_eval')
PROTECTED_MONGO = ('stockpilot', 'stockpilot_demo', 'stockpilot_e2e')
ROOT = Path(__file__).resolve().parents[1]


def verify(directory):
    directory = directory.resolve()
    if directory == ROOT or ROOT in directory.parents or directory.stat().st_mode & 0o077:
        raise ValueError('Use a private directory outside Git.')
    context_path = directory / 'backup-context.json'
    if context_path.is_symlink() or context_path.stat().st_mode & 0o077:
        raise ValueError('Source inventory must remain private.')
    context = json.loads(context_path.read_text())
    if not set(PROTECTED_MONGO) <= set(context['mongo_databases']):
        raise ValueError('Required Mongo databases missing from source inventory.')
    if set(context['postgres_databases']) != set(DATABASES):
        raise ValueError('PostgreSQL inventory changed; update the approved plan.')
    files = []
    for name in ['mongo-full.archive.gz', 'postgres-globals.sql', *[f'{db}.dump' for db in DATABASES]]:
        path = directory / name
        if not path.is_file() or path.is_symlink() or path.stat().st_mode & 0o077 or not path.stat().st_size:
            raise ValueError('Missing, empty or insufficiently private backup file.')
        if name.endswith('.gz'):
            with gzip.open(path, 'rb') as compressed:
                while compressed.read(1024 * 1024):
                    pass
        if name.endswith('.dump'):
            subprocess.run(['pg_restore', '--list', str(path)], stdout=subprocess.DEVNULL,
                           stderr=subprocess.PIPE, check=True, timeout=60)
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        files.append({'file': name, 'bytes': path.stat().st_size, 'sha256': digest.hexdigest(),
                      'modified_at_utc': datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()})
    return {'schema_version': 1, 'verified_at_utc': datetime.now(timezone.utc).isoformat(),
            'mongo': {'strategy': 'full dump with oplog; replay required',
                      'protected_databases': list(PROTECTED_MONGO),
                      'source_inventory': context['mongo_databases'], 'excluded_database': 'local'},
            'postgres': {'databases': list(DATABASES), 'globals_included': True},
            'files': files, 'file_integrity_verified': True, 'restoration_tested': False,
            'limitations': ['Source inventory is operator-supplied; verify before dumping.',
                            'File checks do not establish logical consistency or restore success.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--check', action='store_true', help='Compare files against the existing manifest; write nothing')
    args = parser.parse_args()
    try:
        manifest = verify(args.directory)
        if args.check:
            previous = json.loads((args.directory / 'manifest.json').read_text())
            if previous['files'] != manifest['files'] or previous['postgres'] != manifest['postgres'] or previous['mongo'] != manifest['mongo']:
                raise ValueError('Backup files or inventory changed.')
            print('Existing hashes and file checks match. No files changed; restoration status unchanged.')
            return 0
        descriptor = os.open(args.directory / 'manifest.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'w') as stream:
            json.dump(manifest, stream, indent=2)
            stream.write('\n')
        print('Offline file checks passed; private manifest created. Restoration remains untested.')
        return 0
    except (ValueError, KeyError, TypeError, EOFError, OSError, subprocess.SubprocessError):
        print('Backup verification refused. Inspect private files; details withheld.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
