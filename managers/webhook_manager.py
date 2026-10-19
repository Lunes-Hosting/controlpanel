"""
Outbound Webhook System Module
==============================
Delivers notifications to Discord and HTTP endpoints for:
- Server provisioning completion
- Low credit balance alerts
- Server suspensions
- Unexpected server shutdowns / crashes
- Webhook verification tests

Includes strict anti-spam rate limiting, SSRF protection via DNS inspection,
and Discord 429 backoff management to prevent abuse and panel-wide rate limits.
"""

import collections
import datetime
import ipaddress
import json
import logging
import socket
import threading
import time
import urllib.parse
import requests
from managers.database_manager import DatabaseManager

logger = logging.getLogger(__name__)

DISCORD_HOSTS = ("discord.com", "discordapp.com", "ptb.discord.com", "canary.discord.com")
LUNES_ICON = "https://cdn.discordapp.com/avatars/1054831148557160639/b2c68aac0866e48f36f318d0137ced98.png"

# Anti-spam and rate limiting controls
USER_TEST_COOLDOWN_SECONDS = 30
GLOBAL_MAX_TESTS_PER_MINUTE = 20
MAX_RESPONSE_BYTES = 512

_rate_limit_lock = threading.Lock()
_user_test_timestamps: dict[int, float] = {}
_global_test_timestamps: collections.deque = collections.deque()
_webhook_url_cooldowns: dict[str, float] = {}  # URL -> unix timestamp until which requests are blocked


def ensure_webhook_schema():
    """Ensure the user_webhooks table exists."""
    try:
        DatabaseManager.execute_query("""
            CREATE TABLE IF NOT EXISTS user_webhooks (
                id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
                user_id BIGINT UNSIGNED NOT NULL,
                url VARCHAR(1024) NOT NULL,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                last_delivery_status VARCHAR(64) DEFAULT NULL,
                last_delivery_at TIMESTAMP NULL DEFAULT NULL,
                last_error VARCHAR(1000) DEFAULT NULL,
                last_low_credit_alert_at TIMESTAMP NULL DEFAULT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE KEY uq_user_webhooks_user_id (user_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
        """)
    except Exception as exc:
        logger.warning("Failed to verify user_webhooks table: %s", exc)


def is_discord_webhook(url: str) -> bool:
    """Return True if URL is a Discord webhook."""
    try:
        parsed = urllib.parse.urlparse(url.strip())
        netloc = parsed.netloc.lower()
        return any(host in netloc for host in DISCORD_HOSTS) and "/api/webhooks" in parsed.path.lower()
    except Exception:
        return False


def _verify_safe_ip(hostname: str) -> tuple[bool, str]:
    """Resolve hostname and ensure it does not point to internal/private IP ranges."""
    try:
        addr_info = socket.getaddrinfo(hostname, None)
        for item in addr_info:
            ip_str = item[4][0]
            ip = ipaddress.ip_address(ip_str)
            if (
                ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_multicast
                or ip.is_reserved
                or ip.is_unspecified
            ):
                return False, f"Target IP ({ip}) is in a restricted range."
        return True, ""
    except Exception as exc:
        return False, f"Could not resolve destination host: {exc}"


def validate_webhook_url(url: str) -> tuple[bool, str]:
    """Validate webhook URL format and check for private IP targets."""
    url = (url or "").strip()
    if not url:
        return False, "Please enter a webhook URL."

    if len(url) > 1024:
        return False, "Webhook URL exceeds 1024 characters."

    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return False, "Invalid URL format."

    if parsed.scheme not in ("http", "https"):
        return False, "URL must start with http:// or https://"

    hostname = (parsed.hostname or "").lower()
    if not hostname:
        return False, "Invalid URL: missing domain."

    # DNS check to prevent SSRF against loopback / private IP / cloud metadata
    safe, err = _verify_safe_ip(hostname)
    if not safe:
        return False, err

    return True, ""


def get_user_webhook(user_id: int) -> dict | None:
    """Retrieve webhook settings for a given user ID."""
    try:
        row = DatabaseManager.execute_query(
            "SELECT id, user_id, url, is_active, last_delivery_status, "
            "last_delivery_at, last_error, last_low_credit_alert_at "
            "FROM user_webhooks WHERE user_id = %s",
            (int(user_id),)
        )
        if not row:
            return None

        return {
            "id": row[0],
            "user_id": row[1],
            "url": row[2],
            "is_active": bool(row[3]),
            "last_delivery_status": row[4],
            "last_delivery_at": row[5],
            "last_error": row[6],
            "last_low_credit_alert_at": row[7],
            "is_discord": is_discord_webhook(row[2])
        }
    except Exception as exc:
        logger.error("Error fetching user webhook: %s", exc)
        return None


def save_user_webhook(user_id: int, url: str) -> tuple[bool, str]:
    """Save or update user's outbound webhook URL."""
    url = url.strip()
    valid, err = validate_webhook_url(url)
    if not valid:
        return False, err

    try:
        with DatabaseManager.transaction() as cursor:
            cursor.execute("""
                INSERT INTO user_webhooks (user_id, url, is_active)
                VALUES (%s, %s, 1)
                ON DUPLICATE KEY UPDATE
                    url = VALUES(url),
                    is_active = 1,
                    last_error = NULL
            """, (int(user_id), url))
        return True, "Webhook URL saved successfully."
    except Exception as exc:
        logger.error("Failed to save webhook URL: %s", exc)
        return False, f"Could not save webhook: {str(exc)}"


def delete_user_webhook(user_id: int) -> bool:
    """Delete a user's webhook configuration."""
    try:
        with DatabaseManager.transaction() as cursor:
            cursor.execute("DELETE FROM user_webhooks WHERE user_id = %s", (int(user_id),))
        return True
    except Exception as exc:
        logger.error("Failed to delete webhook: %s", exc)
        return False


def _check_test_rate_limits(user_id: int) -> tuple[bool, str]:
    """Enforce per-user cooldown and global ceiling for test webhook triggers."""
    now = time.time()
    with _rate_limit_lock:
        # 1. Per-user cooldown
        last_test = _user_test_timestamps.get(user_id, 0)
        elapsed = now - last_test
        if elapsed < USER_TEST_COOLDOWN_SECONDS:
            remaining = int(USER_TEST_COOLDOWN_SECONDS - elapsed) + 1
            return False, f"Please wait {remaining} second(s) before sending another test webhook."

        # 2. Global ceiling across all users
        cutoff = now - 60.0
        while _global_test_timestamps and _global_test_timestamps[0] < cutoff:
            _global_test_timestamps.popleft()

        if len(_global_test_timestamps) >= GLOBAL_MAX_TESTS_PER_MINUTE:
            return False, "Network test limit reached. Please wait a few seconds before trying again."

        _user_test_timestamps[user_id] = now
        _global_test_timestamps.append(now)

    return True, ""


def _build_discord_payload(event_type: str, data: dict) -> dict:
    """Format Discord embed with clean, authentic hosting terminology."""
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    if event_type == "test":
        return {
            "username": "Lunes Hosting",
            "avatar_url": LUNES_ICON,
            "embeds": [{
                "title": "Webhook Connected",
                "description": "Your webhook is working. You will receive alerts here for server provisioning, suspensions, unexpected stops, and low credit warnings.",
                "color": 0x5865F2,
                "fields": [
                    {"name": "Status", "value": "Online", "inline": True},
                    {"name": "Delivery", "value": "Discord Webhook", "inline": True}
                ],
                "footer": {"text": "Lunes Hosting • Control Panel", "icon_url": LUNES_ICON},
                "timestamp": now_iso
            }]
        }

    if event_type == "server_provisioned":
        server_name = data.get("server_name", "Game Server")
        server_id = data.get("server_id", "Unknown")
        node = data.get("node", "Auto-selected")
        memory = data.get("memory", "N/A")
        disk = data.get("disk", "N/A")

        fields = [
            {"name": "Server", "value": f"{server_name} (#{server_id})", "inline": True},
            {"name": "Node", "value": str(node), "inline": True},
        ]
        specs = []
        if memory != "N/A":
            specs.append(f"{memory} MB RAM")
        if disk != "N/A":
            specs.append(f"{disk} MB Disk")
        if specs:
            fields.append({"name": "Specs", "value": " • ".join(specs), "inline": True})

        fields.append({"name": "Status", "value": "🟢 Ready", "inline": True})

        return {
            "username": "Lunes Hosting",
            "avatar_url": LUNES_ICON,
            "embeds": [{
                "title": "Server Ready",
                "description": f"Server **{server_name}** has finished installing and is ready to start.",
                "color": 0x57F287,
                "fields": fields,
                "footer": {"text": "Lunes Hosting • Deployment", "icon_url": LUNES_ICON},
                "timestamp": now_iso
            }]
        }

    if event_type == "server_suspended":
        server_name = data.get("server_name", "Game Server")
        server_id = data.get("server_id", "Unknown")
        reason = data.get("reason", "Insufficient credits to renew server")
        source = data.get("source", "billing")

        fields = [
            {"name": "Server", "value": f"{server_name} (#{server_id})", "inline": True},
            {"name": "Reason", "value": str(reason), "inline": True},
        ]
        if source == "billing":
            fields.append({
                "name": "Action Required",
                "value": "Add credits in your panel store to unsuspend your server.",
                "inline": False
            })
        else:
            fields.append({
                "name": "Action",
                "value": "Contact support if you need assistance with this suspension.",
                "inline": False
            })

        return {
            "username": "Lunes Hosting",
            "avatar_url": LUNES_ICON,
            "embeds": [{
                "title": "Server Suspended",
                "description": f"Server **{server_name}** has been suspended.",
                "color": 0xED4245,
                "fields": fields,
                "footer": {"text": "Lunes Hosting • Billing", "icon_url": LUNES_ICON},
                "timestamp": now_iso
            }]
        }

    if event_type == "server_stopped":
        server_name = data.get("server_name", "Game Server")
        server_id = data.get("server_id", "Unknown")
        reason = data.get("reason", "Process terminated unexpectedly")

        fields = [
            {"name": "Server", "value": f"{server_name} (#{server_id})", "inline": True},
            {"name": "Status", "value": "🔴 Offline", "inline": True},
            {"name": "Details", "value": str(reason), "inline": False},
            {"name": "Action", "value": "Check your console logs or restart the server from your panel.", "inline": False}
        ]

        return {
            "username": "Lunes Hosting",
            "avatar_url": LUNES_ICON,
            "embeds": [{
                "title": "Server Stopped",
                "description": f"Server **{server_name}** stopped unexpectedly or shut down.",
                "color": 0xE67E22,
                "fields": fields,
                "footer": {"text": "Lunes Hosting • Server Monitor", "icon_url": LUNES_ICON},
                "timestamp": now_iso
            }]
        }

    if event_type == "low_credits":
        balance = data.get("balance", 0.0)
        burn_rate = data.get("hourly_cost", 0.0)
        hours_left = data.get("hours_left", 0.0)
        active_servers = data.get("server_count", 1)

        time_str = f"{hours_left:.1f} hours" if hours_left < 48 else f"{hours_left / 24:.1f} days"

        return {
            "username": "Lunes Hosting",
            "avatar_url": LUNES_ICON,
            "embeds": [{
                "title": "Low Balance Notice",
                "description": f"You have **{balance:,.1f} credits** remaining (~{time_str} left at current usage).",
                "color": 0xFEE75C,
                "fields": [
                    {"name": "Current Balance", "value": f"{balance:,.1f} credits", "inline": True},
                    {"name": "Hourly Cost", "value": f"{burn_rate:.2f} credits/hr", "inline": True},
                    {"name": "Estimated Runtime", "value": f"~{time_str}", "inline": True},
                    {"name": "Active Servers", "value": f"{active_servers}", "inline": True},
                    {"name": "Action", "value": "Top up your credits in the dashboard store to avoid server suspension.", "inline": False}
                ],
                "footer": {"text": "Lunes Hosting • Billing", "icon_url": LUNES_ICON},
                "timestamp": now_iso
            }]
        }

    return {
        "username": "Lunes Hosting",
        "avatar_url": LUNES_ICON,
        "embeds": [{
            "title": f"Notification: {event_type.replace('_', ' ').title()}",
            "description": json.dumps(data, indent=2),
            "color": 0x5865F2,
            "timestamp": now_iso
        }]
    }


def _build_generic_payload(event_type: str, user_id: int, data: dict) -> dict:
    """Format raw JSON payload for HTTP endpoints."""
    return {
        "event": event_type,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "user_id": user_id,
        "data": data
    }


def _execute_delivery(webhook_id: int, user_id: int, url: str, is_discord: bool, payload: dict) -> tuple[bool, str]:
    """
    POST payload to webhook URL with timeout, size limits, and 429 rate limit backoff.
    """
    now = time.time()
    # Check if this specific URL is in a backoff period from a Discord 429
    with _rate_limit_lock:
        blocked_until = _webhook_url_cooldowns.get(url, 0)
        if now < blocked_until:
            wait_sec = int(blocked_until - now) + 1
            return False, f"Destination webhook is currently rate-limited. Retry in {wait_sec}s."

    headers = {
        "Content-Type": "application/json",
        "User-Agent": "LunesHosting-Webhook/1.0"
    }

    try:
        # Tight connect and read timeouts so slow endpoints never tie up threads
        response = requests.post(url, json=payload, headers=headers, timeout=(3.0, 5.0), stream=True)
        status_text = f"HTTP {response.status_code}"

        # Discord rate limit response handling (429)
        if response.status_code == 429:
            retry_after = 5.0
            try:
                data = response.json()
                retry_after = float(data.get("retry_after", response.headers.get("Retry-After", 5.0)))
            except Exception:
                pass
            with _rate_limit_lock:
                _webhook_url_cooldowns[url] = time.time() + min(retry_after, 60.0)

            err_msg = f"Rate limited by destination endpoint (retry after {retry_after:.1f}s)"
            _record_db_status(user_id, status_text, err_msg)
            return False, err_msg

        # Read only up to MAX_RESPONSE_BYTES to prevent memory bloat/infinite streams
        content_preview = ""
        for chunk in response.iter_content(chunk_size=MAX_RESPONSE_BYTES):
            content_preview = chunk.decode("utf-8", errors="replace")[:MAX_RESPONSE_BYTES]
            break

        if response.status_code in (200, 201, 204):
            _record_db_status(user_id, status_text, None)
            return True, f"Delivered successfully ({status_text})"
        else:
            err_msg = f"Delivery failed ({status_text}): {content_preview[:200]}"
            _record_db_status(user_id, status_text, err_msg)
            return False, err_msg

    except Exception as exc:
        err_msg = f"Connection error: {str(exc)[:200]}"
        _record_db_status(user_id, "ERROR", err_msg)
        return False, err_msg


def _record_db_status(user_id: int, status_text: str, err_msg: str | None):
    """Save last delivery status and errors to user_webhooks."""
    try:
        with DatabaseManager.transaction() as cursor:
            cursor.execute("""
                UPDATE user_webhooks
                SET last_delivery_status = %s,
                    last_delivery_at = NOW(),
                    last_error = %s
                WHERE user_id = %s
            """, (status_text, (err_msg[:500] if err_msg else None), user_id))
    except Exception:
        pass


def dispatch_webhook(user_id: int, event_type: str, data: dict, force: bool = False, async_send: bool = True) -> tuple[bool, str]:
    """Dispatch an event to a user's outbound webhook."""
    config = get_user_webhook(user_id)
    if not config or not config["is_active"]:
        return False, "No active webhook configured."

    if config["is_discord"]:
        payload = _build_discord_payload(event_type, data)
    else:
        payload = _build_generic_payload(event_type, user_id, data)

    if async_send:
        thread = threading.Thread(
            target=_execute_delivery,
            args=(config["id"], user_id, config["url"], config["is_discord"], payload),
            daemon=True
        )
        thread.start()
        return True, "Webhook queued."
    else:
        return _execute_delivery(config["id"], user_id, config["url"], config["is_discord"], payload)


def test_user_webhook(user_id: int) -> tuple[bool, str]:
    """Send a test ping with strict anti-spam rate limiting."""
    config = get_user_webhook(user_id)
    if not config:
        return False, "No webhook configured. Save a webhook URL first."

    # Enforce anti-spam test rate limit
    allowed, rate_msg = _check_test_rate_limits(user_id)
    if not allowed:
        return False, rate_msg

    return dispatch_webhook(user_id, "test", {}, force=True, async_send=False)


# --- Event Helpers ---

def notify_server_provisioned(user_id: int, server_info: dict):
    """Notify user that a server finished provisioning."""
    if not user_id:
        return
    try:
        dispatch_webhook(user_id, "server_provisioned", server_info, async_send=True)
    except Exception as exc:
        logger.error("Error sending server_provisioned webhook: %s", exc)


def notify_server_suspended(user_id: int, server_id: int, server_name: str, reason: str, source: str = "billing"):
    """Notify user specifically that their server was suspended."""
    if not user_id:
        return
    data = {
        "server_id": server_id,
        "server_name": server_name,
        "reason": reason,
        "source": source
    }
    try:
        dispatch_webhook(user_id, "server_suspended", data, async_send=True)
    except Exception as exc:
        logger.error("Error sending server_suspended webhook: %s", exc)


def notify_server_stopped(user_id: int, server_id: int, server_name: str, reason: str = "Process stopped unexpectedly"):
    """Notify user specifically that their server stopped unexpectedly / shut down."""
    if not user_id:
        return
    data = {
        "server_id": server_id,
        "server_name": server_name,
        "reason": reason
    }
    try:
        dispatch_webhook(user_id, "server_stopped", data, async_send=True)
    except Exception as exc:
        logger.error("Error sending server_stopped webhook: %s", exc)


def notify_low_credits(user_id: int, balance: float, hourly_cost: float, hours_left: float, server_count: int):
    """Notify user of low credit balance (deduplicated: max once every 12 hours)."""
    if not user_id:
        return

    try:
        config = get_user_webhook(user_id)
        if not config:
            return

        last_alert = config.get("last_low_credit_alert_at")
        if last_alert:
            if hasattr(last_alert, "tzinfo") and last_alert.tzinfo:
                last_alert = last_alert.replace(tzinfo=None)
            if (datetime.datetime.utcnow() - last_alert) < datetime.timedelta(hours=12):
                return

        data = {
            "balance": balance,
            "hourly_cost": hourly_cost,
            "hours_left": hours_left,
            "server_count": server_count
        }

        try:
            with DatabaseManager.transaction() as cursor:
                cursor.execute(
                    "UPDATE user_webhooks SET last_low_credit_alert_at = NOW() WHERE user_id = %s",
                    (int(user_id),)
                )
        except Exception:
            pass

        dispatch_webhook(user_id, "low_credits", data, async_send=True)
    except Exception as exc:
        logger.error("Error sending low_credits webhook: %s", exc)


try:
    ensure_webhook_schema()
except Exception:
    pass
