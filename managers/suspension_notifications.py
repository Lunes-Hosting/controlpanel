"""Durable suspension email delivery, shared by panel, jobs and Discord."""

import hashlib
import threading
from uuid import uuid4
from .database_manager import DatabaseManager

_delivery_lock = threading.Lock()


def queue_suspension_email(cursor, kind, entity_id, email, reason, label=None, source="manual"):
    if not email:
        raise ValueError("Cannot notify a suspension without an account email")
    digest = hashlib.sha256(f"{email}:{reason}".encode()).hexdigest()
    key = f"{kind}:{entity_id}:{digest}"
    subject = "Account suspended" if kind == "user" else "Server suspended"
    target = "Your hosting account" if kind == "user" else f"Your server {label or entity_id} (ID: {entity_id})"
    body = f"{target} was suspended.\n\nReason: {reason}\n\n"
    if source == "billing":
        body += "Add sufficient credits to restore access. Servers suspended for more than 3 days may be deleted.\n\n"
    elif source == "inactivity":
        body += "Log in to the dashboard to restore your free server. Free servers are deleted after 17 days of owner inactivity.\n\n"
    body += "If you believe this is a mistake, please contact support."
    cursor.execute(
        "INSERT IGNORE INTO suspension_email_outbox "
        "(id, dedup_key, entity_type, entity_id, recipient, subject, body) VALUES (%s,%s,%s,%s,%s,%s,%s)",
        (str(uuid4()), key, kind, int(entity_id), email, subject, body),
    )


def clear_suspension_email_keys(cursor, kind, entity_id):
    # A later suspension, even with the same reason, is a new notification.
    cursor.execute("UPDATE suspension_email_outbox SET dedup_key=NULL WHERE entity_type=%s AND entity_id=%s",
                   (kind, int(entity_id)))


def deliver_pending_suspension_emails(sender=None):
    if not _delivery_lock.acquire(blocking=False):
        return
    try:
        if sender is None:
            from .email_manager import send_suspension_email
            sender = send_suspension_email
        for _ in range(20):
            with DatabaseManager.transaction() as cursor:
                cursor.execute(
                    "SELECT id, recipient, subject, body FROM suspension_email_outbox "
                    "WHERE (status='pending' AND (last_attempt IS NULL OR last_attempt < NOW() - INTERVAL 60 SECOND)) "
                    "OR (status='sending' AND last_attempt < NOW() - INTERVAL 10 MINUTE) "
                    "ORDER BY created_at LIMIT 1 FOR UPDATE"
                )
                row = cursor.fetchone()
                if not row:
                    return
                cursor.execute("UPDATE suspension_email_outbox SET status='sending', last_attempt=NOW(), attempts=attempts+1 WHERE id=%s", (row[0],))
            try:
                sender(row[1], row[2], row[3])
            except Exception as exc:
                DatabaseManager.execute_query("UPDATE suspension_email_outbox SET status='pending' WHERE id=%s", (row[0],))
                print(f"Suspension email {row[0]} will retry: {type(exc).__name__}")
            else:
                DatabaseManager.execute_query("UPDATE suspension_email_outbox SET status='sent' WHERE id=%s", (row[0],))
    finally:
        _delivery_lock.release()


def kick_suspension_email_delivery():
    # Delivery failures must not undo an already committed suspension.
    def run():
        try:
            deliver_pending_suspension_emails()
        except Exception as exc:
            print(f"Suspension email delivery will retry: {type(exc).__name__}")
    try:
        threading.Thread(target=run, daemon=True).start()
    except RuntimeError:
        print("Suspension email delivery deferred to the scheduled retry.")


try:
    DatabaseManager.execute_query("""
        CREATE TABLE IF NOT EXISTS suspension_email_outbox (
            id VARCHAR(36) PRIMARY KEY,
            dedup_key VARCHAR(191) UNIQUE DEFAULT NULL,
            entity_type VARCHAR(16) NOT NULL,
            entity_id BIGINT UNSIGNED NOT NULL,
            recipient VARCHAR(320) NOT NULL,
            subject VARCHAR(191) NOT NULL,
            body TEXT NOT NULL,
            status VARCHAR(16) NOT NULL DEFAULT 'pending',
            attempts INT UNSIGNED NOT NULL DEFAULT 0,
            last_attempt TIMESTAMP NULL DEFAULT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            KEY delivery_status (status, last_attempt)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)
except Exception:
    pass
