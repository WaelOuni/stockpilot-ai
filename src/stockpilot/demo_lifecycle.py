"""Exclusive, identity-checked lifecycle of the fixed local DEMO stores."""

from contextlib import contextmanager
from dataclasses import dataclass
import sys

import psycopg
from psycopg.conninfo import make_conninfo
from pymongo import MongoClient, ReadPreference
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

from stockpilot.demo_dataset import DEMO_DATABASE, generate_demo_dataset
from stockpilot.demo_persistence import (
    guarded_database, initialize_demo, expected_documents, validate_existing_schema,
)
from stockpilot.demo_runtime import (
    preflight_demo, validate_demo_targets, postgres_server_identity, verify_postgres_schema,
)
from stockpilot.demo_workspace import WORKSPACE_STATE_ID, workspace_summary
from stockpilot.infrastructure.config.settings import Settings

# Server-wide advisory lock held on postgres, independent of DEMO DB existence.
LIFECYCLE_LOCK = 0x535044454D4F


def confirm(action):
    """No flag/env/noninteractive bypass of initialization or reset authorization."""
    phrase = f'{action} {DEMO_DATABASE}'
    if not sys.stdin.isatty():
        raise ValueError(f'Interactive confirmation required: {phrase}. No data changed.')
    try:
        answer = input(f'Type exactly "{phrase}" to continue: ')
    except EOFError:
        raise ValueError('Confirmation cancelled; no data changed.') from None
    if answer != phrase:
        raise ValueError('Confirmation refused; no data changed.')


@dataclass
class Resources:
    client: object
    database: object
    admin: object
    mongo_identity: str
    postgres_identity: str


@contextmanager
def demo_resources(settings):
    """Check both actual instances BEFORE collection access, then serialize lifecycle."""
    checkpoint = validate_demo_targets(settings)
    from scripts.prepare_demo_postgres import demo_uri
    expected_pg = postgres_server_identity(make_conninfo(demo_uri(), dbname='postgres'))
    with MongoClient(Settings().mongo_uri, serverSelectionTimeoutMS=5000,
                     connectTimeoutMS=5000, socketTimeoutMS=15000) as reference:
        expected_mongo = reference.admin.command('replSetGetConfig')['config'].get('settings', {}).get('replicaSetId')
        if expected_mongo is None:
            raise ValueError('Mongo replica-set identity is unavailable.')
    with MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=5000,
                     connectTimeoutMS=5000, socketTimeoutMS=15000) as client:
        actual = client.admin.command('replSetGetConfig')['config'].get('settings', {}).get('replicaSetId')
        if actual != expected_mongo:
            raise ValueError('Unexpected Mongo demo server; no data changed.')
        # Both effective PostgreSQL URLs must refer to the independently verified server.
        for uri in (settings.postgres_url, checkpoint):
            if postgres_server_identity(make_conninfo(uri, dbname='postgres')) != expected_pg:
                raise ValueError('Unexpected PostgreSQL demo server; no data changed.')
            with psycopg.connect(make_conninfo(uri, dbname='postgres'), connect_timeout=5) as probe:
                probe.execute('SET TRANSACTION READ ONLY')
                if probe.execute('SELECT current_schema()').fetchone()[0] != 'public':
                    raise ValueError('DEMO PostgreSQL effective namespace must be public.')
        with psycopg.connect(make_conninfo(settings.postgres_url, dbname='postgres'),
                             autocommit=True, connect_timeout=5) as admin:
            if str(admin.execute('SELECT system_identifier FROM pg_control_system()').fetchone()[0]) != expected_pg:
                raise ValueError('Unexpected PostgreSQL lifecycle connection.')
            if admin.execute('SELECT current_database(), current_schema()').fetchone() != ('postgres', 'public'):
                raise ValueError('PostgreSQL lifecycle namespace must be postgres/public.')
            if not admin.execute('SELECT pg_try_advisory_lock(%s)', (LIFECYCLE_LOCK,)).fetchone()[0]:
                raise ValueError('DEMO is already running or being initialized/reset; stop it first.')
            try:
                database = guarded_database(client, DEMO_DATABASE)
                state = database['demo_manifests'].find_one({'_id': WORKSPACE_STATE_ID})
                if state and (state.get('mongo_identity') != str(actual)
                              or state.get('postgres_identity') != expected_pg):
                    raise ValueError('DEMO lifecycle marker belongs to different instances; no data changed.')
                yield Resources(client, database, admin, str(actual), expected_pg)
            finally:
                admin.execute('SELECT pg_advisory_unlock(%s)', (LIFECYCLE_LOCK,))


def _postgres_exists(resources):
    return bool(resources.admin.execute('SELECT 1 FROM pg_database WHERE datname = %s',
                                        (DEMO_DATABASE,)).fetchone())


def ensure_initialized(settings, resources):
    """Only a genuinely absent Mongo workspace may be seeded after confirmation."""
    database = resources.database
    if database.list_collection_names():
        # Includes empty/partial/unknown schemas: never infer permission to reseed.
        if not _postgres_exists(resources):
            raise ValueError('Partial DEMO: Mongo exists but PostgreSQL is absent; no automatic repair.')
        return preflight_demo(settings)
    if _postgres_exists(resources):
        # An existing PostgreSQL DB must already have the approved schema.
        # No migration or truncation is permitted by first-launch initialization.
        with psycopg.connect(settings.postgres_url, connect_timeout=5) as connection:
            connection.execute('SET TRANSACTION READ ONLY')
            if connection.execute('SELECT current_schema()').fetchone()[0] != 'public':
                raise ValueError('DEMO PostgreSQL namespace must be public.')
            verify_postgres_schema(connection, require_checkpoint=True, require_knowledge=True)
    print('DEMO absent: initialize synthetic Mongo products/orders/history in stockpilot_demo only. '
          'Create PostgreSQL schemas only if its DEMO database is absent. No DEV/E2E data copied.')
    confirm('INITIALIZE')
    if not _postgres_exists(resources):
        from scripts.prepare_demo_postgres import provision
        provision(settings.postgres_url)
    initialize_demo(resources.client, database, generate_demo_dataset())
    return preflight_demo(settings)


@contextmanager
def managed_demo(settings):
    with demo_resources(settings) as resources:
        yield ensure_initialized(settings, resources)


def _reset_mongo(resources, dataset):
    """Atomic replacement of approved Mongo contents; never drop a database/collection."""
    database = resources.database
    record = {'_id': WORKSPACE_STATE_ID, 'version': 1, 'status': 'reset_pending',
              'baseline_sha256': dataset.manifest['sha256'],
              'mongo_identity': resources.mongo_identity,
              'postgres_identity': resources.postgres_identity}
    # This marker prevents a cross-store interruption from masquerading as a ready DEMO.
    database['demo_manifests'].replace_one({'_id': WORKSPACE_STATE_ID}, record, upsert=True)

    existing = set(database.list_collection_names())

    def replace(session):
        for name in (*expected_documents(dataset), 'product_imports'):
            if name in existing:
                database[name].delete_many({}, session=session)
        for name, documents in expected_documents(dataset).items():
            for offset in range(0, len(documents), 500):
                database[name].insert_many(list(documents[offset:offset + 500]), session=session)
        database['demo_manifests'].insert_one(record, session=session)

    with resources.client.start_session() as session:
        session.with_transaction(replace, read_concern=ReadConcern('snapshot'),
                                 write_concern=WriteConcern('majority'),
                                 read_preference=ReadPreference.PRIMARY, max_commit_time_ms=10000)


def reset_workspace(settings, resources):
    """Called only by the interactive reset command under the lifetime lock."""
    validate_demo_targets(settings)
    if resources.database.name != DEMO_DATABASE:
        raise ValueError('Reset requires exactly stockpilot_demo.')
    if resources.database.client is not resources.client:
        raise ValueError('Reset database does not belong to the guarded client.')
    config = resources.client.admin.command('replSetGetConfig')['config']
    if str(config.get('settings', {}).get('replicaSetId')) != resources.mongo_identity:
        raise ValueError('Mongo instance changed before reset.')
    guarded_database(resources.client, DEMO_DATABASE)
    dataset = generate_demo_dataset()
    validate_existing_schema(resources.database, complete=True, workspace=True)
    summary = workspace_summary(resources.database, dataset, allow_reset_pending=True)
    # Recheck identities and complete existing PostgreSQL schemas without writes.
    checkpoint = validate_demo_targets(settings)
    with psycopg.connect(checkpoint, connect_timeout=5) as connection:
        connection.execute('SET TRANSACTION READ ONLY')
        if (connection.execute('SELECT current_database(), current_schema()').fetchone()
                != (DEMO_DATABASE, 'public')):
            raise ValueError('Reset PostgreSQL target must be stockpilot_demo/public.')
        if str(connection.execute('SELECT system_identifier FROM pg_control_system()').fetchone()[0]) != resources.postgres_identity:
            raise ValueError('PostgreSQL instance changed before reset.')
        verify_postgres_schema(connection, require_checkpoint=True, require_knowledge=True)
    print('RESET TARGET: Mongo stockpilot_demo, PostgreSQL stockpilot_demo/public on verified instances.')
    print(f"Current Mongo counts: {summary['total_documents']}")
    print('All DEMO products, orders, stock movements, import receipts and test manifests will be '
          'replaced by the versioned seed. Chat checkpoints will be cleared. '
          'Schemas/indexes, migrations and RAG documents are preserved. Stop DEMO and back it up first.')
    confirm('RESET')
    # No write path exists before the interactive confirmation.
    _reset_mongo(resources, dataset)
    with psycopg.connect(checkpoint, connect_timeout=5) as connection:
        if (connection.execute('SELECT current_database(), current_schema()').fetchone()
                != (DEMO_DATABASE, 'public')):
            raise ValueError('PostgreSQL reset target changed; reset_pending retained.')
        if str(connection.execute('SELECT system_identifier FROM pg_control_system()').fetchone()[0]) != resources.postgres_identity:
            raise ValueError('PostgreSQL reset identity changed; reset_pending retained.')
        connection.execute('TRUNCATE public.checkpoints, public.checkpoint_blobs, public.checkpoint_writes')
    resources.database['demo_manifests'].update_one(
        {'_id': WORKSPACE_STATE_ID, 'status': 'reset_pending'}, {'$set': {'status': 'ready'}})
    preflight_demo(settings)
    print('DEMO reset completed. Start again with bash scripts/run-demo.sh.')
