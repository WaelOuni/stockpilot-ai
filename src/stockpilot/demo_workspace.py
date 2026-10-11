"""Validate a persistent, mutable DEMO against its immutable seed provenance."""

from collections import Counter
from datetime import datetime
import math
import hashlib
from uuid import UUID

from bson import ObjectId, json_util

from stockpilot.demo_dataset import DemoDataset, validate_demo_dataset
from stockpilot.demo_persistence import (
    _bson_normalized, _test_run_manifests, expected_documents,
)
from stockpilot.domain.enums.order_status import OrderStatus
from stockpilot.domain.enums.product_category import ProductCategory
from stockpilot.domain.entities.order_event import OrderEventSource
from stockpilot.domain.services.order_lifecycle import (
    validate_order_status_transition, validate_order_cancellation,
)

WORKSPACE_STATE_ID = 'stockpilot-demo-workspace'


def _refuse(collection, field):
    raise ValueError(f'DEMO workspace incompatible: {collection}.{field}; no data changed. '
                     'Stop DEMO, inspect/restore its backup or use the explicit reset procedure.')


def _text(document, field, collection, *, optional=False):
    value = document.get(field)
    if optional and value is None:
        return
    if not isinstance(value, str) or not value.strip():
        _refuse(collection, field)


def _integer(document, field, collection, *, minimum=0):
    if (isinstance(document.get(field), bool) or not isinstance(document.get(field), int)
            or document[field] < minimum):
        _refuse(collection, field)


def _uuid(document, field, collection):
    value = document.get(field)
    try:
        parsed = UUID(value)
        if parsed.version not in (4, 5) or str(parsed) != value:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        _refuse(collection, field)


def _timestamp(value, collection, field):
    try:
        result = value if isinstance(value, datetime) else datetime.fromisoformat(value)
        if result.tzinfo is None or result.utcoffset() is None:
            raise ValueError
        return result
    except (ValueError, TypeError, AttributeError):
        _refuse(collection, field)


def workspace_summary(database, dataset: DemoDataset, *, allow_reset_pending=False, session=None):
    """No writes. Check provenance, required seed identities and current business structure.

    Counts/hash describe the source seed, never a checksum of live inventory.
    Ordinary additions need no test-run manifest. Renaming does not rewrite order snapshots.
    """
    validate_demo_dataset(dataset)
    options = {'session': session} if session is not None else {}
    names = (*expected_documents(dataset), 'product_imports')
    actual = {name: list(database[name].find({}, **options)) for name in names}
    expected = expected_documents(dataset)
    manifests = actual['demo_manifests']
    if any(not isinstance(d.get('_id'), str) for d in manifests):
        _refuse('demo_manifests', 'manifest identity must be a string')
    baseline = [d for d in manifests if d.get('_id') == dataset.dataset_id]
    if baseline != [_bson_normalized(dataset.manifest)]:
        _refuse('demo_manifests', 'immutable seed manifest missing/changed (partial or unknown DEMO)')
    if any(d.get('demo_test_kind') == 'run_manifest'
           and not isinstance(d.get('status'), str) for d in manifests):
        _refuse('demo_manifests', 'technical status must be a string')
    runs = _test_run_manifests(manifests, dataset)
    state = [d for d in manifests if d.get('_id') == WORKSPACE_STATE_ID]
    allowed = {dataset.dataset_id, WORKSPACE_STATE_ID} | {d['_id'] for d in runs.values()}
    if any(d.get('_id') not in allowed for d in manifests):
        _refuse('demo_manifests', 'unknown manifest')
    if state:
        record = state[0]
        if (record.get('version') != 1 or record.get('baseline_sha256') != dataset.manifest['sha256']
                or record.get('status') not in ('ready', 'reset_pending')
                or not record.get('mongo_identity') or not record.get('postgres_identity')):
            _refuse('demo_manifests', 'workspace lifecycle marker')
        if record['status'] != 'ready' and not allow_reset_pending:
            _refuse('demo_manifests', 'reset_pending: explicit reset must be confirmed again')

    by_id = {}
    for name in ('products', 'orders', 'stock_movements'):
        docs = actual[name]
        if any(not isinstance(d.get('_id'), ObjectId) for d in docs):
            _refuse(name, '_id')
        by_id[name] = {d['_id']: d for d in docs}
        # The current APIs cannot delete seeded products/orders/history.
        # Missing identities indicate incomplete initialization or an out-of-band deletion.
        for initial in expected[name]:
            document = by_id[name].get(initial['_id'])
            if document is None:
                _refuse(name, 'missing seeded identity (partial DEMO)')
            immutable = ({'_id', 'sku', 'demo_dataset_id'} if name == 'products' else
                         set(initial) - {'status', 'events'} if name == 'orders' else set(initial))
            normalized = _bson_normalized(initial)
            if any(document.get(key) != normalized[key] for key in immutable):
                _refuse(name, 'immutable identity/history changed')
            if name == 'orders' and (not isinstance(document.get('events'), list)
                                     or document['events'][:len(initial['events'])] != initial['events']):
                _refuse(name, 'seeded event history changed')

    skus = set()
    for product in actual['products']:
        for key in ('sku', 'name'):
            _text(product, key, 'products')
        if product['sku'] in skus:
            _refuse('products', 'duplicate SKU')
        skus.add(product['sku'])
        if (not isinstance(product.get('category'), str)
                or product['category'] not in ProductCategory._value2member_map_):
            _refuse('products', 'category')
        price = product.get('price')
        if (isinstance(price, bool) or not isinstance(price, (int, float))
                or not math.isfinite(price) or price < 0):
            _refuse('products', 'price')
        _integer(product, 'stock', 'products')

    order_ids, operations = set(), set()
    for order in actual['orders']:
        for key in ('order_id', 'operation_id'):
            _uuid(order, key, 'orders')
        if order['order_id'] in order_ids or order['operation_id'] in operations:
            _refuse('orders', 'duplicate identity/operation')
        order_ids.add(order['order_id']); operations.add(order['operation_id'])
        _text(order, 'product_name', 'orders')
        _text(order, 'customer_id', 'orders', optional=True)  # Retained legacy orders stay unowned.
        _integer(order, 'quantity', 'orders', minimum=1)
        if (not isinstance(order.get('reserved_product_id'), ObjectId)
                or order['reserved_product_id'] not in by_id['products']):
            _refuse('orders', 'reserved_product_id')
        try:
            status = OrderStatus(order['status'])
        except (ValueError, KeyError, TypeError):
            _refuse('orders', 'status')
        previous_time = _timestamp(order.get('created_at'), 'orders', 'created_at')
        events = order.get('events')
        if not isinstance(events, list) or not events:
            _refuse('orders', 'events')
        previous = None
        for event in events:
            try:
                current = OrderStatus(event['status'])
                OrderEventSource(event['source'])
                when = _timestamp(event['occurred_at'], 'orders', 'events.occurred_at')
                if when < previous_time or (previous is None and current != OrderStatus.CREATED):
                    raise ValueError
                if previous is not None:
                    if current == OrderStatus.CANCELLED:
                        validate_order_cancellation(previous)
                    else:
                        validate_order_status_transition(previous, current)
            except (ValueError, KeyError, TypeError):
                _refuse('orders', 'event history')
            previous, previous_time = current, when
        if previous != status:
            _refuse('orders', 'events/status mismatch')

    movement_ids, operations = set(), set()
    for movement in actual['stock_movements']:
        for key in ('movement_id', 'operation_id'):
            _uuid(movement, key, 'stock_movements')
        if movement['movement_id'] in movement_ids or movement['operation_id'] in operations:
            _refuse('stock_movements', 'duplicate identity/operation')
        movement_ids.add(movement['movement_id']); operations.add(movement['operation_id'])
        if not isinstance(movement.get('product_id'), ObjectId):
            _refuse('stock_movements', 'product identity')
        product = by_id['products'].get(movement['product_id'])
        if product is None or product['sku'] != movement.get('product_sku'):
            _refuse('stock_movements', 'product identity')
        for key in ('stock_before', 'stock_after'):
            _integer(movement, key, 'stock_movements')
        if (isinstance(movement.get('delta'), bool) or not isinstance(movement.get('delta'), int)
                or movement['stock_before'] + movement['delta'] != movement['stock_after']):
            _refuse('stock_movements', 'arithmetic')
        _text(movement, 'reason', 'stock_movements')
        if movement.get('source') != 'BACKOFFICE':
            _refuse('stock_movements', 'source')
        _timestamp(movement.get('occurred_at'), 'stock_movements', 'occurred_at')

    operations = set()
    for receipt in actual['product_imports']:
        _uuid(receipt, 'operation_id', 'product_imports')
        _text(receipt, 'fingerprint', 'product_imports')
        _integer(receipt, 'created', 'product_imports', minimum=1)
        items = receipt.get('skus')
        if (not isinstance(items, list) or any(not isinstance(s, str) for s in items)
                or len(set(items)) != receipt['created'] or not set(items) <= skus
                or receipt['operation_id'] in operations):
            _refuse('product_imports', 'receipt inventory/operation')
        operations.add(receipt['operation_id'])

    # Keep ownership checks for explicitly tagged technical fixtures. Ordinary API
    # writes have no such claims and never require an artificial test manifest.
    for name in ('products', 'orders', 'stock_movements'):
        seeded_ids = {d['_id'] for d in expected[name]}
        for document in actual[name]:
            if document.get('demo_dataset_id') not in (None, dataset.dataset_id):
                _refuse(name, 'unknown seed provenance')
            if document.get('demo_dataset_id') and document['_id'] not in seeded_ids:
                _refuse(name, 'unowned seeded identity')
            run_id = document.get('demo_test_run_id')
            if run_id is None:
                if document.get('demo_test_kind') is not None:
                    _refuse(name, 'unowned technical marker')
                continue
            if not isinstance(run_id, str):
                _refuse(name, 'technical owner must be a string')
            run = runs.get(run_id)
            if run is None:
                _refuse(name, 'unknown technical owner')
            if name == 'products':
                owned = document.get('demo_test_kind') == 'product' and document['sku'] in run['product_skus']
            elif name == 'orders':
                owned = document['order_id'] in run['order_ids'] and document['operation_id'] in run['operation_ids']
            else:
                owned = document['product_sku'] in run['product_skus'] and document['operation_id'] in run['operation_ids']
            if not owned:
                _refuse(name, 'technical resource outside its manifest')
    for run_id, run in runs.items():
        owned_skus = {p['sku'] for p in actual['products'] if p.get('demo_test_run_id') == run_id}
        if owned_skus != set(run['product_skus']):
            _refuse('demo_manifests', 'partial technical product inventory')

    # Technical fixtures retain provenance; ordinary business additions are equally accepted.
    return {
        'reference': {'dataset_id': dataset.dataset_id, 'sha256': dataset.manifest['sha256'],
                      **dataset.manifest['collections'], 'manifests': 1},
        'test_fixtures': {'runs': len(runs),
                          'runs_by_status': dict(Counter(m['status'] for m in runs.values())),
                          **{name: sum(d.get('demo_test_run_id') in runs for d in actual[name])
                             for name in ('products', 'orders', 'stock_movements')}},
        'total_documents': {name: len(docs) for name, docs in actual.items()},
        'workspace': 'mutable/persistent',
        'working_sha256': hashlib.sha256(json_util.dumps(
            {name: sorted(docs, key=lambda d: str(d['_id'])) for name, docs in actual.items()},
            sort_keys=True).encode()).hexdigest(),
    }
