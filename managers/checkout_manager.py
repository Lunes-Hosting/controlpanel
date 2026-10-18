"""One checkout fulfillment path for webhooks, return pages and reconciliation."""
from threading import Lock
from time import monotonic
import logging
import stripe
from products import products
from .database_manager import DatabaseManager
from .payment_manager import credit_payment

_schema_lock = Lock()
_schema_ready = False


def ensure_checkout_orders():
    global _schema_ready
    if _schema_ready:
        return
    with _schema_lock:
        if _schema_ready:
            return
        DatabaseManager.execute_query('''CREATE TABLE IF NOT EXISTS stripe_checkout_orders (
            pay_id VARCHAR(255) PRIMARY KEY,
            user_id BIGINT NOT NULL,
            user_email VARCHAR(191) NOT NULL,
            price_id VARCHAR(255) NOT NULL,
            credits DECIMAL(20,6) NOT NULL,
            status VARCHAR(16) NOT NULL DEFAULT 'pending',
            next_check DATETIME NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            KEY checkout_retry (status, next_check)
        ) ENGINE=InnoDB''')
        _schema_ready = True


def remember_checkout(pay_id, user_id, email, price_id, credits):
    ensure_checkout_orders()
    DatabaseManager.execute_query(
        'INSERT INTO stripe_checkout_orders (pay_id,user_id,user_email,price_id,credits,next_check) '
        'VALUES (%s,%s,%s,%s,%s,DATE_ADD(NOW(), INTERVAL 2 MINUTE))',
        (pay_id, user_id, email.strip().lower(), price_id, credits))


def checkout_order(pay_id):
    ensure_checkout_orders()
    row = DatabaseManager.execute_query(
        'SELECT user_id,user_email,price_id,credits FROM stripe_checkout_orders WHERE pay_id=%s', (pay_id,))
    return dict(zip(('user_id','email','price_id','credits'),row)) if row else None


def checkout_details(checkout, order=None):
    """Use verified Stripe data and, for new orders, the purchase-time snapshot."""
    if checkout.get('mode') != 'payment' or checkout.get('payment_status') != 'paid':
        return None
    email = str(checkout.get('customer_email') or (checkout.get('customer_details') or {}).get('email') or '').strip().lower()
    items = (checkout.get('line_items') or {}).get('data') or []
    if len(items) != 1 or (checkout.get('line_items') or {}).get('has_more'):
        raise ValueError('Paid checkout has missing or unexpected line items')
    price_id = (items[0].get('price') or {}).get('id')
    quantity = items[0].get('quantity', 1)
    if not price_id or not isinstance(quantity, int) or isinstance(quantity,bool) or quantity < 1 or not email:
        raise ValueError('Paid checkout has invalid purchase details')
    if order:
        if price_id != order['price_id'] or email != order['email'] or quantity != 1:
            raise ValueError('Paid checkout does not match its saved order')
        recipient = DatabaseManager.execute_query('SELECT email FROM users WHERE id=%s', (order['user_id'],))
        if not recipient:
            raise ValueError('Purchase account no longer exists')
        return str(recipient[0]).strip().lower(), order['credits']
    for product in products:
        if product.get('price_link') == price_id:
            return email, float(product['price']) * quantity
    raise ValueError('Paid checkout has no credit mapping')


def fulfill_checkout(pay_id, expected_email=None, expected_account_id=None):
    checkout = stripe.checkout.Session.retrieve(pay_id, expand=['line_items.data.price'])
    if not checkout or checkout.get('id') != pay_id:
        raise ValueError('Unexpected checkout identifier')
    order = checkout_order(pay_id)
    # A logged-in return page may only settle that account's own purchase.
    if expected_email is not None:
        if order:
            if order['user_id'] != expected_account_id:
                raise PermissionError('Checkout belongs to another account')
        else:
            buyer = str(checkout.get('customer_email') or (checkout.get('customer_details') or {}).get('email') or '').strip().lower()
            if buyer != expected_email.strip().lower():
                raise PermissionError('Checkout belongs to another account')
    details = checkout_details(checkout, order)
    if details is None:
        if order and checkout.get('status') == 'expired':
            DatabaseManager.execute_query("UPDATE stripe_checkout_orders SET status='expired' WHERE pay_id=%s AND status='pending'",(pay_id,))
        return None
    email, amount = details
    credited = credit_payment(email, amount, pay_id, 'checkout')
    # If this write fails after credit committed, the ledger makes the retry safe.
    if order:
        DatabaseManager.execute_query("UPDATE stripe_checkout_orders SET status='credited' WHERE pay_id=%s",(pay_id,))
    return credited


def reconcile_pending_checkouts():
    """Retry only purchases recorded by this version, never historical charges."""
    ensure_checkout_orders()
    with DatabaseManager.transaction() as cursor:
        cursor.execute("SELECT pay_id FROM stripe_checkout_orders WHERE status='pending' AND next_check<=NOW() "
                       'ORDER BY next_check,pay_id LIMIT 20 FOR UPDATE', ())
        pay_ids = [row[0] for row in cursor.fetchall()]
        for pay_id in pay_ids:
            cursor.execute('UPDATE stripe_checkout_orders SET next_check=DATE_ADD(NOW(), INTERVAL 5 MINUTE) WHERE pay_id=%s',(pay_id,))
    deadline = monotonic() + 45
    for pay_id in pay_ids:
        if monotonic() >= deadline:
            break
        try:
            fulfill_checkout(pay_id)
        except Exception as exc:
            logging.getLogger(__name__).warning('Checkout %s reconciliation failed (%s); will retry',pay_id,type(exc).__name__)
    return len(pay_ids)
