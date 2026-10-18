"""Credit subscription invoices from billed lines, with durable retry jobs."""
from threading import Lock
from time import monotonic
import logging
import stripe
from config import SUBSCRIPTION_CREDIT_PRICES
from .database_manager import DatabaseManager
from .payment_manager import credit_payment, payment_recorded
from .credit_manager import _credit_amount
from .logging import webhook_log

_schema_lock = Lock()
_schema_ready = False


class InvoiceReviewRequired(ValueError):
    """An adjustment invoice has no automatic consumable-credit policy."""


def ensure_invoice_jobs():
    global _schema_ready
    if _schema_ready:
        return
    with _schema_lock:
        if not _schema_ready:
            DatabaseManager.execute_query('''CREATE TABLE IF NOT EXISTS stripe_invoice_jobs (
                invoice_id VARCHAR(255) PRIMARY KEY,
                status VARCHAR(16) NOT NULL DEFAULT 'pending',
                next_check DATETIME NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                KEY invoice_retry (status,next_check)
            ) ENGINE=InnoDB''')
            _schema_ready = True


def remember_invoice(invoice_id):
    if not invoice_id or len(invoice_id) > 255:
        raise ValueError('Missing or invalid invoice identifier')
    ensure_invoice_jobs()
    DatabaseManager.execute_query(
        'INSERT INTO stripe_invoice_jobs (invoice_id,next_check) VALUES (%s,DATE_ADD(NOW(), INTERVAL 2 MINUTE)) '
        'ON DUPLICATE KEY UPDATE invoice_id=invoice_id',(invoice_id,))


def invoice_subscription(invoice):
    subscription = invoice.get('subscription') or ((invoice.get('parent') or {}).get('subscription_details') or {}).get('subscription')
    return subscription.get('id') if isinstance(subscription,dict) else subscription


def invoice_snapshot(invoice):
    details = invoice.get('subscription_details') or ((invoice.get('parent') or {}).get('subscription_details') or {})
    return details.get('metadata') or {}


def invoice_credit_details(invoice, subscription=None):
    """Ignore prorations; award only the recurring package actually invoiced."""
    snapshot = invoice_snapshot(invoice)
    current = (subscription or {}).get('metadata') or {}
    lines = (invoice.get('lines') or {}).get('data') or []
    if (invoice.get('lines') or {}).get('has_more'):
        lines = invoice['lines'].auto_paging_iter()
    total = 0
    for line in lines:
        parent = line.get('parent') or {}
        recurring = line.get('type') == 'subscription' or parent.get('type') == 'subscription_item_details'
        details = parent.get('subscription_item_details') or {}
        if recurring and (line.get('proration') or details.get('proration')):
            raise InvoiceReviewRequired('Prorated subscription invoice requires staff review')
        if not recurring or line.get('amount',0) < 0:
            continue
        price = line.get('price') or ((line.get('pricing') or {}).get('price_details') or {}).get('price')
        price_id = price.get('id') if isinstance(price,dict) else price
        quantity = line.get('quantity',1)
        if not price_id or not isinstance(quantity,int) or isinstance(quantity,bool) or quantity < 1:
            raise ValueError('Invalid recurring invoice line')
        amount = None
        # Stripe's invoice metadata snapshot describes the historical purchase.
        # Current subscription metadata is only a legacy fallback for that price.
        for metadata in (snapshot,current):
            if metadata.get('price_link') == price_id and metadata.get('credits_per_month') is not None:
                amount = metadata['credits_per_month']
                break
        if amount is None:
            amount = SUBSCRIPTION_CREDIT_PRICES.get(price_id)
        if amount is None or _credit_amount(amount) <= 0:
            raise ValueError('Recurring invoice line has no valid credit mapping')
        total += _credit_amount(amount) * quantity
    if not total:
        return None
    metadata = snapshot or current
    if metadata.get('user_id'):
        recipient = DatabaseManager.execute_query('SELECT email FROM users WHERE id=%s',(int(metadata['user_id']),))
        if not recipient:
            raise ValueError('Invoice account no longer exists')
        email = recipient[0]
    else:
        email = metadata.get('user_email') or invoice.get('customer_email')
        if not email and invoice.get('customer'):
            email = stripe.Customer.retrieve(invoice['customer']).get('email')
    if not email:
        raise ValueError('Invoice recipient unavailable')
    return str(email).strip().lower(),total


def fulfill_invoice(invoice_id):
    if payment_recorded(invoice_id,'invoice'):
        DatabaseManager.execute_query("UPDATE stripe_invoice_jobs SET status='credited' WHERE invoice_id=%s",(invoice_id,))
        return False
    invoice = stripe.Invoice.retrieve(invoice_id)
    if not invoice or invoice.get('id') != invoice_id:
        raise ValueError('Unexpected invoice identifier')
    if invoice.get('status') != 'paid':
        return None
    subscription_id = invoice_subscription(invoice)
    if not subscription_id:
        DatabaseManager.execute_query("UPDATE stripe_invoice_jobs SET status='ignored' WHERE invoice_id=%s",(invoice_id,))
        return None
    # Prefer immutable invoice data; no live lookup of a changed/deleted plan.
    subscription = None if invoice_snapshot(invoice) else stripe.Subscription.retrieve(subscription_id)
    try:
        details = invoice_credit_details(invoice,subscription)
    except InvoiceReviewRequired:
        DatabaseManager.execute_query("UPDATE stripe_invoice_jobs SET status='review' WHERE invoice_id=%s",(invoice_id,))
        webhook_log(f"Invoice {invoice_id} requires staff review: prorated adjustment was not automatically converted into a full credit package.",database_log=True)
        return None
    if details is None:
        DatabaseManager.execute_query("UPDATE stripe_invoice_jobs SET status='ignored' WHERE invoice_id=%s",(invoice_id,))
        return None
    credited = credit_payment(*details,invoice_id,'invoice')
    DatabaseManager.execute_query("UPDATE stripe_invoice_jobs SET status='credited' WHERE invoice_id=%s",(invoice_id,))
    return credited


def reconcile_pending_invoices():
    ensure_invoice_jobs()
    with DatabaseManager.transaction() as cursor:
        cursor.execute("SELECT invoice_id FROM stripe_invoice_jobs WHERE status='pending' AND next_check<=NOW() "
                       'ORDER BY next_check,invoice_id LIMIT 20 FOR UPDATE',())
        ids = [row[0] for row in cursor.fetchall()]
        for invoice_id in ids:
            cursor.execute('UPDATE stripe_invoice_jobs SET next_check=DATE_ADD(NOW(), INTERVAL 5 MINUTE) WHERE invoice_id=%s',(invoice_id,))
    deadline = monotonic()+45
    for invoice_id in ids:
        if monotonic() >= deadline:
            break
        try:
            fulfill_invoice(invoice_id)
        except Exception as exc:
            logging.getLogger(__name__).warning('Invoice %s credit retry failed (%s)',invoice_id,type(exc).__name__)
    return len(ids)
