"""Server-side Outbound Bandwidth addon access; Application key stays private."""
import logging
from functools import wraps

import requests
from config import PTERODACTYL_URL
from runtime.bandwidth import bandwidth_view
from runtime.display_cache import display_cache
from .utils import HEADERS
from .database_manager import DatabaseManager

logger = logging.getLogger(__name__)


def serialized_plan_update(view):
    @wraps(view)
    def wrapped(server_id, *args, **kwargs):
        try:
            key = f'server-plan:{int(server_id)}'
        except (TypeError, ValueError):
            return 'Invalid server ID.', 400
        connection, cursor = DatabaseManager.get_connection()
        acquired = False
        try:
            cursor.execute('SELECT GET_LOCK(%s, 0)', (key,))
            row = cursor.fetchone()
            acquired = bool(row and row[0] == 1)
            if not acquired:
                return 'Another plan update is in progress. Please try again shortly.', 409
            return view(server_id, *args, **kwargs)
        finally:
            try:
                if acquired:
                    cursor.execute('SELECT RELEASE_LOCK(%s)', (key,))
            finally:
                cursor.close()
                connection.close()
    return wrapped


def _url(server_id):
    return f'{PTERODACTYL_URL.rstrip("/")}/api/application/servers/{int(server_id)}'


def read_bandwidth(server_id):
    """Only call after a fresh ownership check. Cache display data for 10s."""
    def load():
        response = requests.get(f'{_url(server_id)}/bandwidth', headers=HEADERS, timeout=(3, 5))
        if response.status_code == 404:
            return {'available': False}
        response.raise_for_status()
        return {'available': True, **bandwidth_view(response.json()['data'])}
    return display_cache.get_or_load(('server-bandwidth', int(server_id)), load, ttl=10)


def update_plan(server_id, build, policy):
    """Apply quota as well as resources; restore prior policy if build fails.

    The addon exposes separate endpoints, so cross-endpoint updates cannot be
    atomic. Unknown outcomes raise; callers must not report a successful update.
    """
    url = _url(server_id)
    previous = requests.get(f'{url}/bandwidth', headers=HEADERS, timeout=(3, 5))
    if previous.status_code == 404 and policy['bandwidth_limit_gb'] == 0:
        # A stock panel has no addon or fleet bandwidth defaults to clear.
        response = requests.patch(f'{url}/build', headers=HEADERS, json=build, timeout=(3, 10))
        response.raise_for_status()
        display_cache.clear()
        return
    previous.raise_for_status()
    saved = previous.json()['data']
    rollback = {**saved['saved'], 'bandwidth_custom': True}
    attempted = False
    try:
        attempted = True
        response = requests.patch(f'{url}/bandwidth', headers=HEADERS, json=policy, timeout=(3, 10))
        response.raise_for_status()
        applied = response.json()['data']
        effective = applied['effective']
        expected = dict(limit_gb=policy['bandwidth_limit_gb'], speed_mbps=policy['bandwidth_speed_mbps'],
                        overage_action=policy['bandwidth_overage_action'])
        if applied.get('bandwidth_custom') is not True or effective.get('inherited_defaults') or any(effective.get(key) != value for key, value in expected.items()):
            raise ValueError('Bandwidth policy was not applied.')
        if policy['bandwidth_overage_action'] == 'throttle' and effective.get('overage_mbps') != policy['bandwidth_overage_mbps']:
            raise ValueError('Overage speed was not applied.')
        response = requests.patch(f'{url}/build', headers=HEADERS, json=build, timeout=(3, 10))
        response.raise_for_status()
    except Exception:
        if attempted:
            try:
                restored = requests.patch(f'{url}/bandwidth', headers=HEADERS, json=rollback, timeout=(3, 10))
                restored.raise_for_status()
                if not saved['bandwidth_custom']:
                    restored = requests.patch(f'{url}/bandwidth', headers=HEADERS,
                                              json={'bandwidth_custom': False}, timeout=(3, 10))
                    restored.raise_for_status()
            except Exception:
                logger.error('Bandwidth rollback failed for server %s; staff review required', server_id)
        raise
    finally:
        display_cache.clear()
