"""Alt account detection and historical IP correlation."""

import ipaddress
import threading
from typing import Optional, Dict, Any, List, Tuple
from managers.database_manager import DatabaseManager
from managers.logging import webhook_log

_schema_lock = threading.Lock()
_schema_ready = False


def ensure_alt_schema():
    """Create user_ip_history table if it does not already exist."""
    global _schema_ready
    if _schema_ready:
        return
    with _schema_lock:
        if not _schema_ready:
            try:
                DatabaseManager.execute_query("""
                    CREATE TABLE IF NOT EXISTS user_ip_history (
                        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                        user_id BIGINT UNSIGNED NOT NULL,
                        ip VARCHAR(45) NOT NULL,
                        first_seen TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        last_seen TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        UNIQUE KEY user_ip_unique (user_id, ip),
                        KEY idx_ip (ip),
                        KEY idx_user (user_id)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """)
                _schema_ready = True
            except Exception as exc:
                print(f"Failed to ensure user_ip_history schema: {exc}")


_PRIVATE_NETS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
)


def is_global_ip(ip: str) -> bool:
    """Return True if the IP address is a valid public/routable IP."""
    if not ip:
        return False
    try:
        addr = ipaddress.ip_address(str(ip).strip())
        if addr.is_loopback or addr.is_link_local or addr.is_unspecified or addr.is_multicast:
            return False
        if any(addr in net for net in _PRIVATE_NETS):
            return False
        return True
    except (ValueError, TypeError):
        return False


def record_user_ip(user_id: int, ip: str) -> None:
    """Record or refresh an IP address associated with a user account."""
    if not user_id or not ip:
        return
    ensure_alt_schema()
    clean_ip = str(ip).strip()
    try:
        DatabaseManager.execute_query("""
            INSERT INTO user_ip_history (user_id, ip, first_seen, last_seen)
            VALUES (%s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON DUPLICATE KEY UPDATE last_seen = CURRENT_TIMESTAMP
        """, (int(user_id), clean_ip))
    except Exception as exc:
        print(f"Failed to record IP history for user {user_id} ({clean_ip}): {exc}")


def is_older_account(a_created, a_id: int, b_created, b_id: int) -> bool:
    """Determine which account is older. Lower ID or earlier created_at wins."""
    if a_created and b_created and a_created != b_created:
        try:
            return a_created < b_created
        except TypeError:
            return str(a_created) < str(b_created)
    return int(a_id) < int(b_id)


def get_client_ip(req) -> str:
    """Best-effort real client IP behind Cloudflare/Proxies.
    Order: CF-Connecting-IP -> X-Forwarded-For (first) -> remote_addr.
    """
    if req is None:
        return ""
    ip = req.headers.get("CF-Connecting-IP")
    if not ip:
        xff = req.headers.get("X-Forwarded-For")
        if xff:
            ip = xff.split(",")[0].strip()
    if not ip:
        ip = getattr(req, "remote_addr", "") or ""
    return str(ip).strip()


def check_login_ip(
    user_id: int,
    email: str,
    name: str,
    role: str,
    created_at,
    ip: str,
    action: str = "login",
) -> Dict[str, Any]:
    """Check if the user's IP has been historically used by other accounts.
    
    If an older account (main) used this IP, the current account is an alt and is suspended.
    If the current account is older (main), any newer accounts sharing this IP are suspended.
    Staff roles ('admin', 'support') are never banned.
    Non-global IPs (localhost, private subnets) are ignored.
    """
    clean_ip = str(ip).strip() if ip else ""
    record_user_ip(user_id, clean_ip)

    if not is_global_ip(clean_ip):
        return {"allowed": True}

    if role in ("admin", "support"):
        return {"allowed": True}

    ensure_alt_schema()

    query = """
        SELECT DISTINCT u.id, u.name, u.email, u.role, u.created_at, u.suspended, u.ip
        FROM users u
        LEFT JOIN user_ip_history h ON u.id = h.user_id
        WHERE (h.ip = %s OR u.ip = %s) AND u.id != %s
    """
    try:
        matches = DatabaseManager.execute_query(query, (clean_ip, clean_ip, int(user_id)), fetch_all=True) or []
    except Exception as exc:
        print(f"Error querying alt matches for IP {clean_ip}: {exc}")
        return {"allowed": True}

    for match in matches:
        other_id, other_name, other_email, other_role, other_created, other_suspended, other_ip = match
        other_id = int(other_id)

        # Do not cross-ban staff accounts
        if other_role in ("admin", "support"):
            continue

        action_title = "server creation" if action == "server_creation" else "login"
        if is_older_account(other_created, other_id, created_at, user_id):
            # Other account is older -> OTHER is MAIN, CURRENT is ALT.
            # Suspend the current account.
            try:
                DatabaseManager.execute_query(
                    "UPDATE users SET suspended = 1 WHERE id = %s",
                    (int(user_id),)
                )
            except Exception as exc:
                print(f"Failed to suspend alt user {user_id}: {exc}")

            action_reason = (
                "Alt account attempted to create server from IP historically used by main account."
                if action == "server_creation"
                else "Alt account logged in from IP historically used by main account."
            )
            webhook_log(
                f"Alt account detected and suspended on {action_title}:\n\n"
                f"Suspended Alt Account:\n"
                f"- ID: {user_id}\n"
                f"- Username: {name}\n"
                f"- Full Email: {email}\n"
                f"- Current IP: {clean_ip}\n"
                f"- Registered: {created_at}\n\n"
                f"Active Main Account:\n"
                f"- ID: {other_id}\n"
                f"- Username: {other_name}\n"
                f"- Full Email: {other_email}\n"
                f"- Main Known IP: {other_ip or clean_ip}\n"
                f"- Registered: {other_created}\n\n"
                f"Reason: {action_reason}",
                status=2,
                database_log=True,
            )

            return {
                "allowed": False,
                "reason": "alt_suspended",
                "main_account": {
                    "id": other_id,
                    "name": other_name,
                    "email": other_email,
                    "ip": other_ip or clean_ip,
                },
            }
        else:
            # Current account is older -> CURRENT is MAIN, OTHER is ALT.
            # Suspend other account if not already suspended.
            if not other_suspended:
                try:
                    DatabaseManager.execute_query(
                        "UPDATE users SET suspended = 1 WHERE id = %s",
                        (other_id,)
                    )
                except Exception as exc:
                    print(f"Failed to suspend alt user {other_id}: {exc}")

                main_action_reason = (
                    "Main account created server; newer alt account sharing this IP was suspended."
                    if action == "server_creation"
                    else "Main account logged in; newer alt account sharing this IP was suspended."
                )
                webhook_log(
                    f"Alt account detected and suspended on {action_title}:\n\n"
                    f"Suspended Alt Account:\n"
                    f"- ID: {other_id}\n"
                    f"- Username: {other_name}\n"
                    f"- Full Email: {other_email}\n"
                    f"- Shared IP: {clean_ip}\n"
                    f"- Registered: {other_created}\n\n"
                    f"Active Main Account:\n"
                    f"- ID: {user_id}\n"
                    f"- Username: {name}\n"
                    f"- Full Email: {email}\n"
                    f"- Current IP: {clean_ip}\n"
                    f"- Registered: {created_at}\n\n"
                    f"Reason: {main_action_reason}",
                    status=2,
                    database_log=True,
                )

    return {"allowed": True}
