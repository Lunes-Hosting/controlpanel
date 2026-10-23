"""Persistent, single-use reset tokens bound to immutable account IDs."""

import hashlib
import hmac
import secrets
from threading import Lock

import bcrypt

from .database_manager import DatabaseManager
from .payment_manager import _require_transactional_table

_schema_lock = Lock()
_schema_ready = False


class InvalidResetToken(ValueError):
    pass


def ensure_reset_schema():
    global _schema_ready
    with _schema_lock:
        if _schema_ready:
            return
        DatabaseManager.execute_query('''
            CREATE TABLE IF NOT EXISTS password_reset_tokens (
                token_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY,
                user_id BIGINT NOT NULL,
                identity_stamp CHAR(64) NOT NULL,
                expires_at DATETIME NOT NULL,
                used_at DATETIME NULL,
                INDEX reset_token_expiry (expires_at)
            ) ENGINE=InnoDB
        ''')
        _require_transactional_table('password_reset_tokens')
        _schema_ready = True


def _identity_stamp(user_id, email, password_hash):
    return hashlib.sha256(f'{user_id}:{email}:{password_hash}'.encode('utf-8')).hexdigest()


def _token_hash(token):
    if not isinstance(token, str) or not 32 <= len(token) <= 128 or not all(c.isascii() and (c.isalnum() or c in '-_') for c in token):
        raise InvalidResetToken('Invalid or expired reset token.')
    return hashlib.sha256(token.encode('ascii')).hexdigest()


def issue_password_reset(account, lifetime):
    ensure_reset_schema()
    token = secrets.token_urlsafe(48)
    lifetime = max(60, min(86400, int(lifetime)))
    DatabaseManager.execute_query(
        'DELETE FROM password_reset_tokens WHERE expires_at < UTC_TIMESTAMP()')
    DatabaseManager.execute_query(
        'INSERT INTO password_reset_tokens (token_hash, user_id, identity_stamp, expires_at) '
        'VALUES (%s, %s, %s, DATE_ADD(UTC_TIMESTAMP(), INTERVAL %s SECOND))',
        (_token_hash(token), account[0], _identity_stamp(account[0], account[7], account[9]), lifetime))
    return token


def apply_password_reset(token, password, update_panel):
    token_hash = _token_hash(token)
    ensure_reset_schema()
    with DatabaseManager.transaction() as cursor:
        cursor.execute(
            'SELECT t.user_id, t.identity_stamp, u.email, u.password, u.pterodactyl_id '
            'FROM password_reset_tokens t JOIN users u ON u.id=t.user_id '
            'WHERE t.token_hash=%s AND t.used_at IS NULL AND t.expires_at > UTC_TIMESTAMP() FOR UPDATE',
            (token_hash,))
        row = cursor.fetchone()
        if not row or not hmac.compare_digest(str(row[1]), _identity_stamp(row[0], row[2], row[3])):
            raise InvalidResetToken('Invalid or expired reset token.')
        if row[4] is None:
            raise InvalidResetToken('Account is unavailable for password reset.')
        password_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt(rounds=14)).decode('ascii')
        # Keep the token retryable if the remote password update fails. The
        # locked token/account rows serialize simultaneous submissions.
        update_panel(row[4], row[2], password)
        cursor.execute('UPDATE users SET password=%s WHERE id=%s', (password_hash, row[0]))
        cursor.execute('UPDATE password_reset_tokens SET used_at=UTC_TIMESTAMP() WHERE token_hash=%s', (token_hash,))
