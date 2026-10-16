"""Serialize account quota checks and creation across all application workers."""

from functools import wraps
from uuid import uuid4
from flask import session
from .database_manager import DatabaseManager


def pending_server_creations(user_id, servers):
    """Reconcile observed servers; keep ambiguous API requests quota-reserved."""
    ids = {server['attributes']['id'] for server in servers}
    external_ids = {server['attributes'].get('external_id') for server in servers}
    rows = DatabaseManager.execute_query(
        "SELECT request_id, server_id FROM server_creation_requests WHERE user_id=%s AND state='pending'",
        (int(user_id),), fetch_all=True,
    ) or []
    pending = 0
    for request_id, server_id in rows:
        if request_id in external_ids or (server_id is not None and server_id in ids):
            DatabaseManager.execute_query("UPDATE server_creation_requests SET state='observed' WHERE request_id=%s", (request_id,))
        else:
            pending += 1
    return pending


def reserve_server_creation(user_id):
    request_id = str(uuid4())
    DatabaseManager.execute_query(
        "INSERT INTO server_creation_requests (request_id, user_id) VALUES (%s, %s)",
        (request_id, int(user_id)),
    )
    return request_id


def finish_server_creation(request_id, server_id=None, failed=False):
    DatabaseManager.execute_query(
        "UPDATE server_creation_requests SET server_id=%s, state=%s WHERE request_id=%s",
        (server_id, "failed" if failed else "pending", request_id),
    )


def release_deleted_server(server_id):
    """A confirmed deletion also releases an unobserved creation reservation."""
    DatabaseManager.execute_query(
        "UPDATE server_creation_requests SET state='deleted' WHERE server_id=%s AND state='pending'",
        (int(server_id),),
    )


def serialized_server_creation(function):
    @wraps(function)
    def locked(*args, **kwargs):
        # The advisory lock belongs to this dedicated connection, not a worker.
        key = f"server-create:{int(session['account_id'])}"
        connection, cursor = DatabaseManager.get_connection()
        acquired = False
        try:
            cursor.execute("SELECT GET_LOCK(%s, 5)", (key,))
            row = cursor.fetchone()
            acquired = bool(row and row[0] == 1)
            if not acquired:
                return "Another server creation is in progress. Please try again shortly.", 409
            return function(*args, **kwargs)
        finally:
            try:
                if acquired:
                    cursor.execute("SELECT RELEASE_LOCK(%s)", (key,))
            finally:
                cursor.close()
                connection.close()
    return locked


DatabaseManager.execute_query("""
    CREATE TABLE IF NOT EXISTS server_creation_requests (
        request_id VARCHAR(36) PRIMARY KEY,
        user_id BIGINT UNSIGNED NOT NULL,
        server_id BIGINT UNSIGNED DEFAULT NULL,
        state VARCHAR(16) NOT NULL DEFAULT 'pending',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        KEY pending_user (user_id, state)
    ) ENGINE=InnoDB
""")
