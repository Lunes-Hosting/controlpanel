"""Commit payment deduplication, balance changes and roles as one unit."""

from mysql.connector import IntegrityError
from threading import Lock

from .database_manager import DatabaseManager
from .credit_manager import _credit_amount, _add_credits_in_transaction


def _payment_table(kind):
    table, column = {
        "checkout": ("stripe_processed_checkouts", "pay_id"),
        "invoice": ("stripe_processed_invoices", "invoice_id"),
    }[kind]
    # DDL must run before the transaction: MySQL DDL implicitly commits.
    DatabaseManager.execute_query(
        f"CREATE TABLE IF NOT EXISTS {table} ("
        f"{column} VARCHAR(255) PRIMARY KEY, "
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP) ENGINE=InnoDB"
    )
    _require_transactional_table(table)
    return table, column


def _require_transactional_table(table):
    # Older versions created these ledgers using the server's default engine.
    # Refuse non-transactional tables rather than silently lose retry safety.
    engines = DatabaseManager.execute_query(
        "SELECT TABLE_NAME, ENGINE FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN (%s, %s)",
        ("users", table), fetch_all=True,
    )
    if len(engines or []) != 2 or any(str(row[1]).lower() != "innodb" for row in engines):
        raise RuntimeError("Payment credits require InnoDB for users and payment ledgers")


def payment_recorded(payment_id, kind):
    table, column = _payment_table(kind)
    return DatabaseManager.execute_query(f"SELECT {column} FROM {table} WHERE {column}=%s", (payment_id,)) is not None


def credit_payment(email, amount, payment_id, kind):
    """Commit the credit and replay marker together; failures remain retryable."""
    amount = _credit_amount(amount)
    if not payment_id:
        raise ValueError("Missing payment identifier")
    table, column = _payment_table(kind)
    try:
        with DatabaseManager.transaction() as cursor:
            cursor.execute(f"INSERT INTO {table} ({column}) VALUES (%s)", (payment_id,))
            _add_credits_in_transaction(cursor, email, amount)
    except IntegrityError as exc:
        if exc.errno == 1062:
            return False
        raise
    return True


_usage_lock = Lock()
_usage_schema_ready = False


def bill_server_hour(email, amount, server_id, billing_hour):
    """Charge at most once per server/UTC hour across scheduler processes."""
    global _usage_schema_ready
    amount = _credit_amount(amount)
    server_id = int(server_id)
    if server_id <= 0:
        raise ValueError('Invalid server identifier')
    if not _usage_schema_ready:
        with _usage_lock:
            if not _usage_schema_ready:
                DatabaseManager.execute_query('''CREATE TABLE IF NOT EXISTS server_credit_billing (
                    server_id BIGINT PRIMARY KEY, last_hour CHAR(13) NOT NULL
                ) ENGINE=InnoDB''')
                _require_transactional_table('server_credit_billing')
                _usage_schema_ready = True
    with DatabaseManager.transaction() as cursor:
        cursor.execute('SELECT credits FROM users WHERE email=%s FOR UPDATE',(email,))
        balance = cursor.fetchone()
        if balance is None:
            raise ValueError('Billing account not found')
        cursor.execute('SELECT last_hour FROM server_credit_billing WHERE server_id=%s FOR UPDATE',(server_id,))
        previous = cursor.fetchone()
        if previous and previous[0] >= billing_hour:
            return 'already_charged'
        if float(balance[0]) < amount:
            return 'SUSPEND'
        if previous:
            cursor.execute('UPDATE server_credit_billing SET last_hour=%s WHERE server_id=%s',(billing_hour,server_id))
        else:
            cursor.execute('INSERT INTO server_credit_billing (server_id,last_hour) VALUES (%s,%s)',(server_id,billing_hour))
        cursor.execute('UPDATE users SET credits = credits - %s WHERE email=%s',(amount,email))
    return 'charged'
