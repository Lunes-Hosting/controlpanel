"""Commit payment deduplication, balance changes and roles as one unit."""

from mysql.connector import IntegrityError

from .database_manager import DatabaseManager
from .credit_manager import _credit_amount, _add_credits_in_transaction


def credit_payment(email, amount, payment_id, kind):
    """True for a committed credit, False only for an already credited payment.

    Failures roll back the marker and balance together so Stripe can retry.
    Checkout success pages and webhooks share the same checkout ledger.
    """
    table, column = {
        "checkout": ("stripe_processed_checkouts", "pay_id"),
        "invoice": ("stripe_processed_invoices", "invoice_id"),
    }[kind]
    amount = _credit_amount(amount)
    if not payment_id:
        raise ValueError("Missing payment identifier")
    # DDL must run before the transaction: MySQL DDL implicitly commits.
    DatabaseManager.execute_query(
        f"CREATE TABLE IF NOT EXISTS {table} ("
        f"{column} VARCHAR(255) PRIMARY KEY, "
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP) ENGINE=InnoDB"
    )
    # Older versions created these ledgers using the server's default engine.
    # Refuse non-transactional tables rather than silently lose retry safety.
    engines = DatabaseManager.execute_query(
        "SELECT TABLE_NAME, ENGINE FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN (%s, %s)",
        ("users", table), fetch_all=True,
    )
    if len(engines or []) != 2 or any(str(row[1]).lower() != "innodb" for row in engines):
        raise RuntimeError("Payment credits require InnoDB for users and payment ledgers")
    try:
        with DatabaseManager.transaction() as cursor:
            cursor.execute(f"INSERT INTO {table} ({column}) VALUES (%s)", (payment_id,))
            _add_credits_in_transaction(cursor, email, amount)
    except IntegrityError as exc:
        if exc.errno == 1062:
            return False
        raise
    return True
