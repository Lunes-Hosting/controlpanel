"""One durable free-account decision shared by panel and Discord reviews."""
from threading import Lock

from .database_manager import DatabaseManager

_schema_lock = Lock()
_schema_ready = False


def ensure_review_schema():
    global _schema_ready
    with _schema_lock:
        if _schema_ready:
            return
        DatabaseManager.execute_query('''CREATE TABLE IF NOT EXISTS account_reviews (
            user_id BIGINT UNSIGNED PRIMARY KEY,
            decision VARCHAR(16) NOT NULL,
            reviewer VARCHAR(120) NOT NULL,
            ticket_id VARCHAR(36) NULL,
            reviewed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB''')
        DatabaseManager.execute_query('''CREATE TABLE IF NOT EXISTS account_review_messages (
            message_id BIGINT UNSIGNED PRIMARY KEY,
            user_id BIGINT UNSIGNED NOT NULL,
            channel_id BIGINT UNSIGNED NOT NULL,
            ticket_id VARCHAR(36) NULL,
            INDEX review_user (user_id)
        ) ENGINE=InnoDB''')
        engines = DatabaseManager.execute_query(
            'SELECT TABLE_NAME, ENGINE FROM information_schema.TABLES '
            'WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME IN (%s,%s)',
            ('users','account_reviews'), fetch_all=True)
        if len(engines or []) != 2 or any(str(row[1]).lower() != 'innodb' for row in engines):
            raise RuntimeError('Account reviews require transactional InnoDB tables.')
        _schema_ready = True


def _review_state(account, saved):
    state = saved[0] if saved else ('pending' if account[3] == 'sketchy' else 'not_required')
    if account[3] == 'client':
        state = 'purchased'
    elif account[3] != 'sketchy' and state == 'pending':
        state = 'not_required'
    return dict(user_id=account[0], name=account[1], email=account[2], role=account[3],
                status=state, pending=state == 'pending' and account[3] == 'sketchy',
                reviewer=saved[1] if saved else None)


def get_account_review(user_id):
    ensure_review_schema()
    row = DatabaseManager.execute_query(
        'SELECT u.id,u.name,u.email,u.role,r.decision,r.reviewer '
        'FROM users u LEFT JOIN account_reviews r ON r.user_id=u.id WHERE u.id=%s', (int(user_id),))
    return _review_state(row[:4],row[4:] if row[4] else None) if row else None


def decide_account_review(user_id, decision, reviewer, ticket_id=None):
    if decision not in ('approved','denied'):
        raise ValueError('Choose approve or deny.')
    if not reviewer or len(reviewer) > 120:
        raise ValueError('A reviewer is required.')
    ensure_review_schema()
    with DatabaseManager.transaction() as cursor:
        cursor.execute('SELECT id,name,email,role FROM users WHERE id=%s FOR UPDATE', (int(user_id),))
        account = cursor.fetchone()
        if not account:
            raise LookupError('Account no longer exists.')
        if ticket_id is not None:
            cursor.execute('SELECT user_id FROM tickets WHERE id=%s', (str(ticket_id),))
            ticket = cursor.fetchone()
            if not ticket or ticket[0] != account[0]:
                raise PermissionError('Ticket does not belong to this account.')
        cursor.execute('SELECT decision,reviewer FROM account_reviews WHERE user_id=%s', (account[0],))
        state = _review_state(account,cursor.fetchone())
        if not state['pending']:
            return dict(state, changed=False)
        cursor.execute('INSERT INTO account_reviews (user_id,decision,reviewer,ticket_id) VALUES (%s,%s,%s,%s)',
                       (account[0],decision,reviewer,ticket_id))
        if decision == 'approved':
            cursor.execute("UPDATE users SET role='member' WHERE id=%s AND role='sketchy'", (account[0],))
        return dict(state, status=decision, pending=False, reviewer=reviewer, changed=True)


def track_review_message(user_id, channel_id, message_id, ticket_id=None):
    ensure_review_schema()
    DatabaseManager.execute_query(
        'INSERT INTO account_review_messages (user_id,channel_id,message_id,ticket_id) '
        'VALUES (%s,%s,%s,%s)', (int(user_id),int(channel_id),int(message_id),ticket_id))


def review_messages(user_id=None):
    ensure_review_schema()
    where = ' WHERE user_id=%s' if user_id is not None else ''
    return DatabaseManager.execute_query(
        'SELECT user_id,channel_id,message_id,ticket_id FROM account_review_messages'+where,
        (int(user_id),) if user_id is not None else (), fetch_all=True) or []
