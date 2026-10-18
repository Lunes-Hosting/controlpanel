"""One checkout fulfillment path for webhooks, return pages and reconciliation."""
from threading import Lock
from time import monotonic
import logging
import stripe
from products import products
from .database_manager import DatabaseManager
from .payment_manager import credit_payment, payment_recorded
from .credit_manager import _credit_amount

_schema_lock = Lock()
_schema_ready = False


class PaymentReviewRequired(ValueError):
    """Payment was reversed before fulfillment; staff must resolve the order."""


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
    free_completed = (checkout.get('payment_status') == 'no_payment_required' and
                      checkout.get('status') == 'complete' and checkout.get('amount_total') == 0)
    if checkout.get('mode') != 'payment' or (checkout.get('payment_status') != 'paid' and not free_completed):
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
        amount = _credit_amount(order['credits'])
        if amount <= 0:
            raise ValueError('Saved checkout has no positive credit amount')
        return str(recipient[0]).strip().lower(), amount
    for product in products:
        if product.get('price_link') == price_id:
            amount = _credit_amount(product['price']) * quantity
            if amount <= 0:
                raise ValueError('Checkout package has no positive credit amount')
            return email, amount
    raise ValueError('Paid checkout has no credit mapping')


def fulfill_checkout(pay_id, expected_email=None, expected_account_id=None):
    checkout = stripe.checkout.Session.retrieve(pay_id, expand=['line_items.data.price','payment_intent.latest_charge'])
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
    if payment_recorded(pay_id,'checkout'):
        if order:
            DatabaseManager.execute_query("UPDATE stripe_checkout_orders SET status='credited' WHERE pay_id=%s",(pay_id,))
        return False
    intent = checkout.get('payment_intent') or {}
    charge = intent.get('latest_charge') if isinstance(intent,dict) else None
    if isinstance(charge,str):
        charge = stripe.Charge.retrieve(charge)
    if charge and (charge.get('refunded') or charge.get('amount_refunded',0) > 0 or charge.get('disputed')):
        if order:
            DatabaseManager.execute_query("UPDATE stripe_checkout_orders SET status='review' WHERE pay_id=%s AND status='pending'",(pay_id,))
        raise PaymentReviewRequired('Refunded or disputed payment needs staff review before crediting')
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
