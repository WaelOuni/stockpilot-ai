from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from stockpilot.demo_dataset import generate_demo_dataset
from stockpilot.demo_persistence import expected_documents, _bson_normalized
from stockpilot.demo_workspace import workspace_summary
from stockpilot.demo_lifecycle import confirm, ensure_initialized


@pytest.fixture(scope='module')
def dataset():
    return generate_demo_dataset()


def database(documents):
    db = MagicMock()
    db.name = 'stockpilot_demo'
    db.__getitem__.side_effect = lambda name: SimpleNamespace(
        find=lambda *a, **kw: [_bson_normalized(d) for d in documents.get(name, ())])
    return db


def test_mutable_product_metadata_and_stock_do_not_change_reference(dataset):
    documents = deepcopy(expected_documents(dataset))
    documents['products'][0].update(name='Renamed tool', price=42.0, category='OTHER', stock=2)
    result = workspace_summary(database(documents), dataset)
    assert result['reference']['sha256'] == dataset.manifest['sha256']
    assert documents['products'][0]['name'] == 'Renamed tool'


@pytest.mark.parametrize('mutation,field', [
    (lambda d: d['demo_manifests'][0].update(sha256='wrong'), 'immutable seed'),
    (lambda d: d.update(products=d['products'][1:]), 'missing seeded'),
    (lambda d: d['products'][0].update(stock=-1), 'stock'),
    (lambda d: d['products'][0].update(stock=True), 'stock'),
    (lambda d: d['products'][0].update(price=float('nan')), 'price'),
    (lambda d: d['products'][0].update(sku='changed'), 'immutable identity'),
    (lambda d: d['orders'][0].update(customer_id='other'), 'immutable identity'),
    (lambda d: d['orders'][0].update(quantity=99), 'immutable identity'),
    (lambda d: d['orders'][0]['events'][-1].update(source='CUSTOMER'), 'seeded event'),
    (lambda d: d['stock_movements'][0].update(delta=123), 'immutable identity'),
])
def test_corruption_and_partial_initialization_are_not_adopted(dataset, mutation, field):
    documents = deepcopy(expected_documents(dataset))
    mutation(documents)
    with pytest.raises(ValueError, match=field):
        workspace_summary(database(documents), dataset)


@pytest.mark.parametrize('interactive,answer', [(False, 'RESET stockpilot_demo'),
                                              (True, ''), (True, 'yes'),
                                              (True, 'RESET stockpilot'),
                                              (True, 'RESET stockpilot_e2e')])
def test_reset_confirmation_cannot_be_bypassed(monkeypatch, interactive, answer):
    monkeypatch.setattr('stockpilot.demo_lifecycle.sys.stdin', SimpleNamespace(isatty=lambda: interactive))
    monkeypatch.setattr('builtins.input', lambda prompt: answer)
    with pytest.raises(ValueError, match='confirmation|Confirmation'):
        confirm('RESET')


def test_exact_interactive_confirmation_is_required(monkeypatch):
    monkeypatch.setattr('stockpilot.demo_lifecycle.sys.stdin', SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr('builtins.input', lambda prompt: 'RESET stockpilot_demo')
    confirm('RESET')  # Only authorization is tested. No reset is performed.


def test_partial_mongo_never_reinitializes(monkeypatch):
    db = MagicMock()
    db.list_collection_names.return_value = ['products']
    resources = SimpleNamespace(database=db)
    monkeypatch.setattr('stockpilot.demo_lifecycle._postgres_exists', lambda r: False)
    monkeypatch.setattr('stockpilot.demo_lifecycle.initialize_demo', lambda *a: pytest.fail('No seed'))
    with pytest.raises(ValueError, match='Partial DEMO'):
        ensure_initialized(None, resources)


def test_existing_demo_is_only_verified_not_seeded(monkeypatch):
    db = MagicMock()
    db.list_collection_names.return_value = ['products', 'demo_manifests']
    resources = SimpleNamespace(database=db)
    monkeypatch.setattr('stockpilot.demo_lifecycle._postgres_exists', lambda r: True)
    monkeypatch.setattr('stockpilot.demo_lifecycle.preflight_demo', lambda s: 'verified')
    monkeypatch.setattr('stockpilot.demo_lifecycle.confirm', lambda *a: pytest.fail('No confirmation'))
    monkeypatch.setattr('stockpilot.demo_lifecycle.initialize_demo', lambda *a: pytest.fail('No seed'))
    assert ensure_initialized(None, resources) == 'verified'


@pytest.mark.parametrize('target', ['stockpilot', 'stockpilot_e2e', 'unknown'])
def test_lifecycle_refuses_non_demo_before_any_connection(monkeypatch, target):
    from stockpilot.demo_lifecycle import demo_resources
    from stockpilot.infrastructure.config.settings import Settings
    monkeypatch.setattr('stockpilot.demo_lifecycle.MongoClient', lambda *a, **kw: pytest.fail('No connection'))
    monkeypatch.setattr('stockpilot.demo_lifecycle.psycopg.connect', lambda *a, **kw: pytest.fail('No connection'))
    settings = Settings(mongo_database=target)
    with pytest.raises(ValueError, match='exactly stockpilot_demo'):
        with demo_resources(settings):
            pytest.fail('No resource acquired')


def test_interrupted_reset_is_visible_and_never_resumed_automatically(dataset):
    from stockpilot.demo_workspace import WORKSPACE_STATE_ID
    documents = deepcopy(expected_documents(dataset))
    documents['demo_manifests'] += ({'_id': WORKSPACE_STATE_ID, 'version': 1,
                                   'baseline_sha256': dataset.manifest['sha256'],
                                   'status': 'reset_pending', 'mongo_identity': 'mongo',
                                   'postgres_identity': 'postgres'},)
    db = database(documents)
    with pytest.raises(ValueError, match='reset_pending'):
        workspace_summary(db, dataset)
    assert workspace_summary(db, dataset, allow_reset_pending=True)['workspace'] == 'mutable/persistent'


def test_reset_transaction_plan_never_runs_ddl_or_database_drop(dataset):
    # Mock-only plan validation: no reset is performed on any database.
    from stockpilot.demo_lifecycle import _reset_mongo, Resources
    client = MagicMock()
    db = MagicMock()
    collections = {name: MagicMock() for name in expected_documents(dataset)}
    db.__getitem__.side_effect = collections.__getitem__
    db.list_collection_names.return_value = list(collections)
    resources = Resources(client, db, MagicMock(), 'mongo', 'postgres')
    session = client.start_session.return_value.__enter__.return_value
    session.with_transaction.side_effect = lambda callback, **kw: callback(session)
    _reset_mongo(resources, dataset)
    db.list_collection_names.assert_called_once_with()  # Never listCollections inside a transaction.
    client.drop_database.assert_not_called()
    db.drop_collection.assert_not_called()
    for name in expected_documents(dataset):
        db[name].delete_many.assert_called_once_with({}, session=session)
        assert all(call.kwargs['session'] is session for call in db[name].insert_many.call_args_list)


def test_orphan_technical_ownership_is_not_adopted_as_business(dataset):
    from bson import ObjectId
    documents = deepcopy(expected_documents(dataset))
    documents['products'] += ({'_id': ObjectId(), 'sku': 'TECHNICAL-ORPHAN', 'name': 'Tool',
                              'category': 'OTHER', 'price': 1.0, 'stock': 0,
                              'demo_test_run_id': 'unowned', 'demo_test_kind': 'product'},)
    with pytest.raises(ValueError, match='unknown technical owner'):
        workspace_summary(database(documents), dataset)


@pytest.mark.parametrize('field,value', [('category', []), ('demo_test_run_id', []), ('stock', -1)])
def test_malformed_business_addition_has_a_safe_diagnostic(dataset, field, value):
    from bson import ObjectId
    documents = deepcopy(expected_documents(dataset))
    product = {'_id': ObjectId(), 'sku': 'NEW-STRUCTURAL', 'name': 'Tool',
               'category': 'OTHER', 'price': 1.0, 'stock': 0}
    product[field] = value
    documents['products'] += (product,)
    with pytest.raises(ValueError, match='DEMO workspace incompatible'):
        workspace_summary(database(documents), dataset)


def test_bson_int64_stock_remains_a_valid_integer(dataset):
    from bson.int64 import Int64
    documents = deepcopy(expected_documents(dataset))
    documents['products'][0]['stock'] = Int64(2**32)
    assert workspace_summary(database(documents), dataset)['workspace'] == 'mutable/persistent'
