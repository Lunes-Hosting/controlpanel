"""Single-use verification links bound to the account and its current mailbox."""
import hashlib
import hmac
import secrets
from threading import Lock

from runtime.email_identity import canonical_email
from .database_manager import DatabaseManager
from .payment_manager import _require_transactional_table

_schema_ready = False
_schema_lock = Lock()


class InvalidVerificationToken(ValueError):
    pass


def ensure_verification_schema():
    global _schema_ready
    with _schema_lock:
        if _schema_ready:
            return
        DatabaseManager.execute_query('''CREATE TABLE IF NOT EXISTS email_verification_tokens (
            token_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY,
            user_id BIGINT NOT NULL,
            email_snapshot VARCHAR(191) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
            expires_at DATETIME NOT NULL,
            used_at DATETIME NULL,
            INDEX verification_user (user_id),
            INDEX verification_expiry (expires_at)
        ) ENGINE=InnoDB''')
        _require_transactional_table('email_verification_tokens')
        _schema_ready = True


def _verification_hash(token):
    if not isinstance(token,str) or not 32 <= len(token) <= 128 or not all(
            c.isascii() and (c.isalnum() or c in '-_') for c in token):
        raise InvalidVerificationToken('Invalid or expired verification link.')
    return hashlib.sha256(token.encode('ascii')).hexdigest()


def issue_email_verification(user_id, lifetime=1800):
    ensure_verification_schema()
    with DatabaseManager.transaction() as cursor:
        cursor.execute('SELECT email,email_verified_at FROM users WHERE id=%s FOR UPDATE', (int(user_id),))
        account = cursor.fetchone()
        if not account:
            raise InvalidVerificationToken('Account no longer exists.')
        if account[1] is not None:
            return None
        email = canonical_email(account[0])
        token = secrets.token_urlsafe(48)
        # Resending invalidates previous links; token delivery never trusts a
        # submitted address or a cached session email as the destination.
        cursor.execute('DELETE FROM email_verification_tokens WHERE user_id=%s', (int(user_id),))
        cursor.execute('INSERT INTO email_verification_tokens (token_hash,user_id,email_snapshot,expires_at) '
                       'VALUES (%s,%s,%s,DATE_ADD(UTC_TIMESTAMP(), INTERVAL %s SECOND))',
                       (_verification_hash(token),int(user_id),email,max(60,min(86400,int(lifetime)))))
        return token, account[0]


def validate_email_verification(token, account_id, consume=False):
    digest = _verification_hash(token)
    ensure_verification_schema()
    with DatabaseManager.transaction() as cursor:
        cursor.execute('SELECT t.user_id,t.email_snapshot,u.email FROM email_verification_tokens t '
                       'JOIN users u ON u.id=t.user_id WHERE t.token_hash=%s AND t.used_at IS NULL '
                       'AND t.expires_at > UTC_TIMESTAMP() FOR UPDATE', (digest,))
        row = cursor.fetchone()
        if not row:
            raise InvalidVerificationToken('Invalid or expired verification link.')
        if row[0] != account_id:
            raise PermissionError('Sign in with the account this verification link belongs to.')
        if not hmac.compare_digest(row[1].encode('utf-8'),canonical_email(row[2]).encode('utf-8')):
            raise InvalidVerificationToken('The account email has changed. Request another verification link.')
        if consume:
            cursor.execute('UPDATE users SET email_verified_at=COALESCE(email_verified_at,UTC_TIMESTAMP()) WHERE id=%s', (row[0],))
            cursor.execute('UPDATE email_verification_tokens SET used_at=UTC_TIMESTAMP() WHERE token_hash=%s', (digest,))
        return row[0]
