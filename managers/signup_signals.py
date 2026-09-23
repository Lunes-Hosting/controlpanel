"""First-party signals collected only when a new account is registered.

The database stores keyed digests rather than browser values. A persistent
cookie and local storage ID are strong matches; the browser profile is only a
review signal because different people can have the same profile.
"""

import hmac
import json
import re
import secrets
import threading
from hashlib import sha256

from flask import current_app, g, request

from managers.database_manager import DatabaseManager


COOKIE_NAME = "signup_device"
COOKIE_AGE = 60 * 60 * 24 * 365 * 2
_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")
_STORAGE_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_schema_lock = threading.Lock()
_schema_ready = False


def ensure_schema():
    """Create an empty, prospective-only table on the first signup request."""
    global _schema_ready
    if _schema_ready:
        return
    with _schema_lock:
        if not _schema_ready:
            DatabaseManager.execute_query("""
                CREATE TABLE IF NOT EXISTS signup_devices (
                    user_id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
                    device_hash CHAR(64) NOT NULL,
                    storage_hash CHAR(64) DEFAULT NULL,
                    fingerprint_hash CHAR(64) DEFAULT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    KEY signup_devices_device (device_hash),
                    KEY signup_devices_storage (storage_hash),
                    KEY signup_devices_fingerprint (fingerprint_hash)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)
            _schema_ready = True


def _digest(kind, value):
    secret = current_app.config["SECRET_KEY"]
    if isinstance(secret, str):
        secret = secret.encode("utf-8")
    return hmac.new(secret, f"{kind}:{value}".encode("utf-8"), sha256).hexdigest()


def device_token():
    """Return a validated cookie or issue a new one for this request."""
    token = request.cookies.get(COOKIE_NAME, "")
    if not _TOKEN_PATTERN.fullmatch(token):
        token = secrets.token_urlsafe(32)
        g.new_signup_device = token
    return token


def set_device_cookie(response):
    token = getattr(g, "new_signup_device", None)
    if token:
        response.set_cookie(
            COOKIE_NAME, token, max_age=COOKIE_AGE, httponly=True,
            secure=current_app.config.get("SESSION_COOKIE_SECURE", True),
            samesite="Lax", path="/",
        )
    return response


def collect_signals(form):
    """Validate bounded browser input; never store its raw values."""
    token = device_token()
    storage = form.get("signup_storage", "")
    storage_hash = _digest("storage", storage) if _STORAGE_PATTERN.fullmatch(storage) else None

    fingerprint_hash = None
    raw = form.get("signup_fingerprint", "")
    if 0 < len(raw) <= 2048:
        try:
            profile = json.loads(raw)
            if (
                isinstance(profile, dict)
                and profile.get("v") == 1
                and isinstance(profile.get("canvas"), str)
                and re.fullmatch(r"[0-9a-f]{8}", profile["canvas"])
                and isinstance(profile.get("screen"), str)
                and 3 <= len(profile["screen"]) <= 32
                and isinstance(profile.get("agent"), str)
                and 8 <= len(profile["agent"]) <= 300
                and all(isinstance(v, (str, int)) for v in profile.values())
            ):
                canonical = json.dumps(profile, sort_keys=True, separators=(",", ":"))
                fingerprint_hash = _digest("profile", canonical)
        except (ValueError, TypeError):
            pass

    return {
        "device_hash": _digest("device", token),
        "storage_hash": storage_hash,
        "fingerprint_hash": fingerprint_hash,
    }


def find_matches(signals):
    """Return exact and profile matches among accounts enrolled since launch."""
    ensure_schema()
    exact = DatabaseManager.execute_query(
        "SELECT user_id FROM signup_devices WHERE device_hash = %s LIMIT 1",
        (signals["device_hash"],),
    )
    if not exact and signals["storage_hash"]:
        exact = DatabaseManager.execute_query(
            "SELECT user_id FROM signup_devices WHERE storage_hash = %s LIMIT 1",
            (signals["storage_hash"],),
        )
    profile = None
    if not exact and signals["fingerprint_hash"]:
        profile = DatabaseManager.execute_query(
            "SELECT user_id FROM signup_devices WHERE fingerprint_hash = %s LIMIT 1",
            (signals["fingerprint_hash"],),
        )
    return {"exact": exact[0] if exact else None,
            "profile": profile[0] if profile else None}


def record_signup(user_id, signals):
    ensure_schema()
    DatabaseManager.execute_query(
        "INSERT INTO signup_devices (user_id, device_hash, storage_hash, fingerprint_hash) "
        "VALUES (%s, %s, %s, %s)",
        (user_id, signals["device_hash"], signals["storage_hash"],
         signals["fingerprint_hash"]),
    )
