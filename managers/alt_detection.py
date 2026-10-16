"""Alt account detection and historical IP correlation."""

import ipaddress
import os
import threading
from typing import Optional, Dict, Any, List, Tuple
from managers.database_manager import DatabaseManager
from managers.logging import webhook_log
from managers.alt_settings import alt_detection_enabled
from managers.suspension_manager import set_user_suspension

_schema_lock = threading.Lock()
_schema_ready = False


def _read_enforce_flag() -> bool:
    """Read ALT_ENFORCE from config.py, else the environment. Off by default
    so a missing value (or a missing config module) simply means warn-only
    """
    value = None
    try:
        import config
        value = getattr(config, "ALT_ENFORCE", None)
    except Exception:
        value = None
    if value is None:
        value = os.getenv("ALT_ENFORCE", "0")
    return str(value).strip().lower() in ("1", "true", "yes")


# Warn-only unless enabled on config.
ALT_ENFORCE = _read_enforce_flag()


def ensure_alt_schema():
    """Create the alt-detection tables if they do not already exist."""
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
                # Accounts staff have reviewed and cleared; never flagged or banned.
                DatabaseManager.execute_query("""
                    CREATE TABLE IF NOT EXISTS alt_exemptions (
                        user_id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
                        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """)
                # Pairs already reported to staff, so warnings are sent once.
                DatabaseManager.execute_query("""
                    CREATE TABLE IF NOT EXISTS alt_flags (
                        user_a BIGINT UNSIGNED NOT NULL,
                        user_b BIGINT UNSIGNED NOT NULL,
                        ip VARCHAR(45) NOT NULL,
                        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (user_a, user_b)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """)
                # Audit trail of bans and unbans: who did it, when, and why.
                DatabaseManager.execute_query("""
                    CREATE TABLE IF NOT EXISTS alt_actions (
                        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                        user_id BIGINT UNSIGNED NOT NULL,
                        action VARCHAR(10) NOT NULL,
                        actor VARCHAR(255) NOT NULL,
                        source VARCHAR(20) NOT NULL,
                        ip VARCHAR(45) DEFAULT NULL,
                        other_user_id BIGINT UNSIGNED DEFAULT NULL,
                        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        KEY idx_user (user_id),
                        KEY idx_action (action)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """)
                _schema_ready = True
            except Exception as exc:
                print(f"Failed to ensure alt detection schema: {exc}")


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
    if not alt_detection_enabled() or not user_id or not ip:
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


def log_alt_action(
    user_id: int,
    action: str,
    actor: str = "unknown",
    source: str = "unknown",
    ip: Optional[str] = None,
    other_user_id: Optional[int] = None,
) -> None:
    """Write a ban/unban entry to the audit log.

    action: 'ban' or 'unban'. actor: who did it (staff email, Discord user, or
    'auto-alt-check'). source: 'admin', 'discord' or 'auto'.
    """
    if not user_id:
        return
    ensure_alt_schema()
    try:
        DatabaseManager.execute_query(
            "INSERT INTO alt_actions (user_id, action, actor, source, ip, other_user_id) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (int(user_id), action, str(actor)[:255], source, ip,
             int(other_user_id) if other_user_id else None),
        )
    except Exception as exc:
        print(f"Failed to log {action} for user {user_id}: {exc}")


def unban_history(user_ids: List[int]) -> Optional[Dict[str, Any]]:
    ids = sorted({int(i) for i in user_ids if i})
    if not ids:
        return None
    ensure_alt_schema()
    placeholders = ",".join(["%s"] * len(ids))
    try:
        rows = DatabaseManager.execute_query(
            "SELECT user_id, actor, created_at FROM alt_actions "
            f"WHERE action = 'unban' AND user_id IN ({placeholders}) "
            "ORDER BY created_at DESC, id DESC",
            tuple(ids),
            fetch_all=True,
        ) or []
    except Exception as exc:
        print(f"Failed to load unban history for {ids}: {exc}")
        return None
    if not rows:
        return None
    return {
        "count": len(rows),
        "last_user_id": rows[0][0],
        "last_actor": rows[0][1],
        "last_at": rows[0][2],
    }


def describe_unban_history(history: Optional[Dict[str, Any]]) -> str:
    """One readable line about prior unbans, or '' if there are none."""
    if not history:
        return ""
    times = "time" if history["count"] == 1 else "times"
    return (
        f"Already unbanned {history['count']} {times} before "
        f"(most recent: {history['last_at']} by {history['last_actor']}, "
        f"account ID {history['last_user_id']})."
    )


def _history_block(account_ids: List[int]) -> str:
    """Extra webhook text about prior unbans for the accounts involved."""
    line = describe_unban_history(unban_history(account_ids))
    return f"\n\nUnban history: {line}" if line else ""


def is_exempt(user_id: int) -> bool:
    """True if staff have cleared this account from alt checks."""
    ensure_alt_schema()
    try:
        row = DatabaseManager.execute_query(
            "SELECT 1 FROM alt_exemptions WHERE user_id = %s", (int(user_id),)
        )
        return bool(row)
    except Exception as exc:
        print(f"Failed to check alt exemption for user {user_id}: {exc}")
        return False


def exempt_user(user_id: int, actor: str = "unknown", source: str = "unknown") -> None:
    if not user_id:
        return
    ensure_alt_schema()
    ip = None
    try:
        row = DatabaseManager.execute_query(
            "SELECT ip FROM users WHERE id = %s", (int(user_id),)
        )
        ip = row[0] if row else None
    except Exception as exc:
        print(f"Failed to look up IP for user {user_id}: {exc}")
    try:
        DatabaseManager.execute_query(
            "INSERT IGNORE INTO alt_exemptions (user_id) VALUES (%s)", (int(user_id),)
        )
    except Exception as exc:
        print(f"Failed to exempt user {user_id} from alt checks: {exc}")
    log_alt_action(user_id, "unban", actor=actor, source=source, ip=ip)


def _flag_once(user_id: int, other_id: int, ip: str) -> bool:
    """Record that this account pair was reported. True only the first time."""
    user_a, user_b = sorted((int(user_id), int(other_id)))
    try:
        seen = DatabaseManager.execute_query(
            "SELECT 1 FROM alt_flags WHERE user_a = %s AND user_b = %s", (user_a, user_b)
        )
        if seen:
            return False
        DatabaseManager.execute_query(
            "INSERT IGNORE INTO alt_flags (user_a, user_b, ip) VALUES (%s, %s, %s)",
            (user_a, user_b, ip),
        )
        return True
    except Exception as exc:
        print(f"Failed to record alt flag for {user_id}/{other_id}: {exc}")
        return False


def is_older_account(a_created, a_id: int, b_created, b_id: int) -> bool:
    """Determine which account is older. Lower ID or earlier created_at wins."""
    if a_created and b_created and a_created != b_created:
        try:
            return a_created < b_created
        except TypeError:
            return str(a_created) < str(b_created)
    return int(a_id) < int(b_id)


def check_login_ip(
    user_id: int,
    email: str,
    name: str,
    role: str,
    created_at,
    ip: str,
) -> Dict[str, Any]:
    """Check if the logging-in user's IP has been historically used by other accounts.
    
    If an older account (main) used this IP, the current account is an alt and is suspended.
    If the current account is older (main), any newer accounts sharing this IP are suspended.
    Staff roles ('admin', 'support') are never banned.
    Non-global IPs (localhost, private subnets) are ignored.
    """
    if not alt_detection_enabled():
        return {"allowed": True}

    clean_ip = str(ip).strip() if ip else ""
    record_user_ip(user_id, clean_ip)

    if not is_global_ip(clean_ip):
        return {"allowed": True}

    if role in ("admin", "support"):
        return {"allowed": True}

    ensure_alt_schema()

    if is_exempt(user_id):
        return {"allowed": True}

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

    all_ids = [int(user_id)] + [int(m[0]) for m in matches]

    for match in matches:
        other_id, other_name, other_email, other_role, other_created, other_suspended, other_ip = match
        other_id = int(other_id)

        # Do not cross-flag staff accounts
        if other_role in ("admin", "support"):
            continue

        if is_older_account(other_created, other_id, created_at, user_id):
            # Other account is older -> OTHER is MAIN, CURRENT is ALT.
            if not ALT_ENFORCE:
                if _flag_once(user_id, other_id, clean_ip):
                    webhook_log(
                        f"Possible alt account (NOT suspended - shared IPs can be "
                        f"households or VPNs, please review):\n\n"
                        f"Logged-in Account:\n"
                        f"- ID: {user_id}\n"
                        f"- Username: {name}\n"
                        f"- Full Email: {email}\n"
                        f"- Login IP: {clean_ip}\n"
                        f"- Registered: {created_at}\n\n"
                        f"Older Account On Same IP:\n"
                        f"- ID: {other_id}\n"
                        f"- Username: {other_name}\n"
                        f"- Full Email: {other_email}\n"
                        f"- Main Known IP: {other_ip or clean_ip}\n"
                        f"- Registered: {other_created}"
                        f"{_history_block(all_ids)}",
                        status=1,
                        database_log=True,
                    )
                continue

            try:
                set_user_suspension(user_id, True,
                    f"Automatic alternate-account detection: shared login IP with account {other_id}.",
                    source="alt_detection", actor="auto-alt-check")
            except Exception as exc:
                print(f"Failed to suspend alt user {user_id}: {exc}")
            log_alt_action(user_id, "ban", actor="auto-alt-check", source="auto",
                           ip=clean_ip, other_user_id=other_id)

            webhook_log(
                f"Alt account detected and suspended on login:\n\n"
                f"Suspended Alt Account:\n"
                f"- ID: {user_id}\n"
                f"- Username: {name}\n"
                f"- Full Email: {email}\n"
                f"- Login IP: {clean_ip}\n"
                f"- Registered: {created_at}\n\n"
                f"Active Main Account:\n"
                f"- ID: {other_id}\n"
                f"- Username: {other_name}\n"
                f"- Full Email: {other_email}\n"
                f"- Main Known IP: {other_ip or clean_ip}\n"
                f"- Registered: {other_created}\n\n"
                f"Reason: Alt account logged in from IP historically used by main account."
                f"{_history_block(all_ids)}",
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
            if is_exempt(other_id):
                continue

            if not ALT_ENFORCE:
                if _flag_once(user_id, other_id, clean_ip):
                    webhook_log(
                        f"Possible alt account (NOT suspended - shared IPs can be "
                        f"households or VPNs, please review):\n\n"
                        f"Newer Account On Same IP:\n"
                        f"- ID: {other_id}\n"
                        f"- Username: {other_name}\n"
                        f"- Full Email: {other_email}\n"
                        f"- Shared IP: {clean_ip}\n"
                        f"- Registered: {other_created}\n\n"
                        f"Logged-in Older Account:\n"
                        f"- ID: {user_id}\n"
                        f"- Username: {name}\n"
                        f"- Full Email: {email}\n"
                        f"- Login IP: {clean_ip}\n"
                        f"- Registered: {created_at}"
                        f"{_history_block(all_ids)}",
                        status=1,
                        database_log=True,
                    )
                continue

            if not other_suspended:
                try:
                    set_user_suspension(other_id, True,
                        f"Automatic alternate-account detection: shared login IP with account {user_id}.",
                        source="alt_detection", actor="auto-alt-check")
                except Exception as exc:
                    print(f"Failed to suspend alt user {other_id}: {exc}")
                log_alt_action(other_id, "ban", actor="auto-alt-check", source="auto",
                               ip=clean_ip, other_user_id=user_id)

                webhook_log(
                    f"Alt account detected and suspended on login:\n\n"
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
                    f"- Login IP: {clean_ip}\n"
                    f"- Registered: {created_at}\n\n"
                    f"Reason: Main account logged in; newer alt account sharing this IP was suspended."
                    f"{_history_block(all_ids)}",
                    status=2,
                    database_log=True,
                )

    return {"allowed": True}
