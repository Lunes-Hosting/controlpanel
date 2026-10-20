"""Bounded, per-account display snapshots; never used for permission or spending checks."""

from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from threading import BoundedSemaphore, Lock

from runtime.display_cache import display_cache
from runtime.stats_snapshot import StatsSnapshot
from .server_manager import improve_list_servers
from .credit_manager import convert_to_product

_snapshots = OrderedDict()
_lock = Lock()
_workers = ThreadPoolExecutor(max_workers=4, thread_name_prefix='dashboard-inventory')
_slots = BoundedSemaphore(12)


def _submit(function):
    if not _slots.acquire(blocking=False):
        raise RuntimeError('Inventory refresh busy')
    def run():
        try:
            function()
        finally:
            _slots.release()
    try:
        _workers.submit(run)
    except BaseException:
        _slots.release()
        raise


def load_dashboard_inventory(ptero_id):
    ptero_id = int(ptero_id)
    payload = improve_list_servers(ptero_id, timeout=(3, 12))
    if not payload or payload['attributes']['id'] != ptero_id:
        raise ValueError('Account inventory unavailable')
    source = payload['attributes']['relationships']['servers']['data']
    if not isinstance(source, list):
        raise ValueError('Invalid account inventory')
    servers = []
    monthly = 0.0
    for server in source:
        attributes = server['attributes']
        if attributes['user'] != ptero_id:
            raise ValueError('Unexpected inventory owner')
        monthly += float(convert_to_product(server)['price'])
        # Discard environment variables, API metadata and other unused fields
        # before copying/caching or rendering a dashboard response.
        servers.append({'attributes': {
            'id': int(attributes['id']), 'name': attributes['name'],
            'identifier': attributes.get('identifier', str(attributes['id'])),
            'status': 'suspended' if attributes.get('suspended') else attributes.get('status'),
            'limits': {key: attributes.get('limits', {}).get(key, 0) for key in ('cpu', 'memory', 'disk')},
            'feature_limits': {'databases': attributes.get('feature_limits', {}).get('databases', 'Unlimited')},
        }})
    return {'servers': servers, 'server_count': len(servers),
            'monthly_usage': int(monthly) if monthly.is_integer() else round(monthly, 2)}


def dashboard_inventory(account_id, ptero_id):
    key = (int(account_id), int(ptero_id) if ptero_id is not None else None)
    with _lock:
        record = _snapshots.get(key)
        if record is None:
            record = [display_cache.generation, StatsSnapshot(
                lambda: load_dashboard_inventory(ptero_id), ttl=15, retry_delay=15, submit=_submit)]
            _snapshots[key] = record
        elif record[0] != display_cache.generation:
            # Local server mutations already invalidate the shared display cache.
            # Keep the last display but refresh it immediately after such a change.
            with record[1].lock:
                record[1].updated = None
            record[0] = display_cache.generation
        _snapshots.move_to_end(key)
        while len(_snapshots) > 128:
            _snapshots.popitem(last=False)
        snapshot = record[1]
    return snapshot.get()
