"""Verified billing access and Stripe customer ownership by immutable user ID."""
from functools import wraps
import hashlib
from threading import Lock

import stripe
from flask import flash, redirect, session, url_for
from config import HOSTED_URL
from runtime.email_identity import canonical_email

from .database_manager import DatabaseManager

_schema_lock = Lock()
_schema_ready = False


def verified_billing_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        row = DatabaseManager.execute_query(
            'SELECT email_verified_at FROM users WHERE id=%s AND suspended=FALSE',
            (session.get('account_id'),))
        if not row or not row[0]:
            flash('Please verify your email address before accessing billing.')
            return redirect(url_for('user.index'))
        return view(*args, **kwargs)
    return wrapped


def ensure_billing_identity_schema():
    global _schema_ready
    with _schema_lock:
        if _schema_ready:
            return
        column = DatabaseManager.execute_query("SHOW COLUMNS FROM users LIKE 'stripe_customer_id'")
        if not column:
            try:
                DatabaseManager.execute_query(
                    'ALTER TABLE users ADD COLUMN stripe_customer_id VARCHAR(255) '
                    'CHARACTER SET ascii COLLATE ascii_bin NULL UNIQUE')
            except Exception as exc:
                # Another worker may have completed the same migration.
                if getattr(exc, 'errno', None) != 1060:
                    raise
        _schema_ready = True


def get_stripe_customer(user_id):
    ensure_billing_identity_schema()
    with DatabaseManager.transaction() as cursor:
        cursor.execute('SELECT email, email_verified_at, suspended, stripe_customer_id '
                       'FROM users WHERE id=%s FOR UPDATE', (user_id,))
        row = cursor.fetchone()
        if not row or not row[1] or row[2]:
            raise ValueError('Verified, active account required for billing.')
        if row[3]:
            return row[3]
        # Recover legacy subscription ownership only from metadata written by
        # this application, never from a matching customer email address.
        subscriptions = stripe.Subscription.search(query=f"metadata['user_id']:'{int(user_id)}'", limit=100)
        customers = set()
        origin = HOSTED_URL.rstrip('/')
        for subscription in subscriptions.auto_paging_iter():
            metadata = subscription.get('metadata') or {}
            if str(metadata.get('user_id')) != str(user_id):
                raise ValueError('Subscription ownership mismatch.')
            if metadata.get('panel_origin') not in (None, origin):
                continue
            try:
                if canonical_email(metadata.get('user_email')) != canonical_email(row[0]):
                    continue
            except ValueError:
                continue
            customer = subscription.get('customer')
            if isinstance(customer, str) and customer.startswith('cus_'):
                customers.add(customer)
        if len(customers) > 1:
            raise ValueError('Multiple legacy billing customers require review.')
        if customers:
            customer_id = customers.pop()
        else:
            customer = stripe.Customer.create(
                email=row[0], metadata={'panel_user_id': str(user_id), 'panel_origin': origin},
                idempotency_key=f'panel-customer-{hashlib.sha256(origin.encode()).hexdigest()[:32]}-{int(user_id)}')
            customer_id = customer['id']
        if not isinstance(customer_id, str) or not customer_id.startswith('cus_'):
            raise ValueError('Invalid billing customer.')
        cursor.execute('UPDATE users SET stripe_customer_id=%s WHERE id=%s', (customer_id, user_id))
        return customer_id
