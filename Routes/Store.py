"""
Store Management Module
====================

This module handles all store-related operations in the control panel,
including product display, payment processing, and credit management.

Templates Used:
-------------
- store.html: Product catalog and pricing
- success.html: Payment confirmation
- cancel.html: Payment cancellation

Database Tables Used:
------------------
- users: Credit balances
- payments: Transaction history
- products: Available items

External Services:
---------------
- Stripe API:
  - Payment processing
  - Checkout sessions
  - Webhooks
- Pterodactyl API:
  - User verification
  - Resource allocation

Session Requirements:
------------------
- email: User's email address
- pterodactyl_id: User's panel ID
- pay_id: Stripe session ID (during checkout)
- price_link: Product ID (during checkout)

Configuration:
------------
- STRIPE_SECRET_KEY: API authentication
- SITE_URL: Return URL base

Payment Flow:
-----------
1. User selects product
2. Stripe checkout initiated
3. Payment processed
4. Credits allocated
5. Transaction logged
"""

from flask import Blueprint, request, render_template, session, flash, redirect, url_for, current_app
import sys
from threadedreturn import ThreadWithReturnValue
sys.path.append("..")
from managers.authentication import login_required
from managers.billing_identity import verified_billing_required, get_stripe_customer
from managers.user_manager import get_ptero_id, get_id
from managers.payment_manager import credit_payment
from managers.invoice_manager import remember_invoice, fulfill_invoice, invoice_snapshot, invoice_subscription
from managers.checkout_manager import ensure_checkout_orders, remember_checkout, fulfill_checkout, PaymentReviewRequired
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from managers.email_manager import send_email
from managers.logging import webhook_log
from managers.database_manager import DatabaseManager
from config import (
    STRIPE_SECRET_KEY,
    STRIPE_WEBHOOK_SECRET,
    SUBSCRIPTION_CREDIT_PRICES,
    HOSTED_URL,
    YOUR_SUCCESS_URL,
    YOUR_CANCEL_URL,
)
from products import products
import stripe

stripe.api_key = STRIPE_SECRET_KEY
stripe.default_http_client = stripe.http_client.RequestsClient(timeout=15)
store = Blueprint('store', __name__)


@store.route("/")
@login_required
@verified_billing_required
def storepage():
    """
    Display the store page with available products.
    
    Templates:
        - store.html: Product catalog
        
    Database Queries:
        - Get user credits
        - Get available products
        
    Process:
        1. Verify authentication
        2. Get user's panel ID
        3. Filter active products
        4. Format pricing display
        
    Returns:
        template: store.html with:
            - products: Available items
            - credits: User balance
            - prices: Formatted costs
            
    Related Functions:
        - get_user_credits(): Gets balance
        - format_price(): Formats display
    """

    if 'pterodactyl_id' in session:
        pass
    else:
        ptero_id = get_ptero_id(session['email'])
        session['pterodactyl_id'] = ptero_id

    products_local = list(products)
    for product in products_local:
        if product['price_link'] is None:
            products_local.remove(product)
    return render_template("store.html", products=products_local)


@store.route('/subscribe/<price_link>', methods=['POST', 'GET'])
@login_required
@verified_billing_required
def create_subscription_checkout_session(price_link: str):
    if price_link not in SUBSCRIPTION_CREDIT_PRICES:
        flash("not valid subscription")
        return redirect(url_for("user.index"))

    credits_per_month = int(SUBSCRIPTION_CREDIT_PRICES[price_link])
    user_email = str(session.get('email', '')).strip().lower()
    if not user_email:
        flash("not valid user")
        return redirect(url_for("user.index"))

    success_url = f"{HOSTED_URL}?subscription=success"
    cancel_url = f"{HOSTED_URL}?subscription=cancel"

    check_session = stripe.checkout.Session.create(
        payment_method_types=['card'],
        allow_promotion_codes=True,
        line_items=[{
            'price': price_link,
            'quantity': 1,
        }],
        mode='subscription',
        success_url=success_url,
        cancel_url=cancel_url,
        customer=get_stripe_customer(session['account_id']),
        subscription_data={
            'metadata': {
                'user_email': user_email,
                'user_id': str(session['account_id']),
                'panel_origin': HOSTED_URL.rstrip('/'),
                'credits_per_month': str(credits_per_month),
                'price_link': price_link,
            }
        },
    )

    return redirect(check_session['url'])


@store.route('/portal', methods=['GET'])
@login_required
@verified_billing_required
def billing_portal():
    user_email = str(session.get('email', '')).strip().lower()
    if not user_email:
        flash("not valid user")
        return redirect(url_for("user.index"))

    customer_id = get_stripe_customer(session['account_id'])

    portal_session = stripe.billing_portal.Session.create(
        customer=customer_id,
        return_url=f"{HOSTED_URL}",
    )
    return redirect(portal_session['url'])


def checkout_success_url(base):
    parts = urlsplit(base)
    query = urlencode([(key,value) for key,value in parse_qsl(parts.query,keep_blank_values=True) if key != 'session_id'])
    query = (query + '&' if query else '') + 'session_id={CHECKOUT_SESSION_ID}'
    return urlunsplit((parts.scheme,parts.netloc,parts.path,query,parts.fragment))


@store.route('/checkout/<price_link>', methods=['POST', 'GET'])
@login_required
@verified_billing_required
def create_checkout_session(price_link: str):
    """
    Create a Stripe checkout session for product purchase.
    
    Args:
        price_link: Stripe price ID
        
    API Calls:
        - Stripe: Create checkout session
        
    Database Queries:
        - Get product details
        - Get user information
        
    Process:
        1. Verify authentication
        2. Validate product
        3. Create checkout session
        4. Store session info
        5. Redirect to payment
        
    Session Data:
        Sets:
        - pay_id: Checkout session ID
        - price_link: Product identifier
        
    Returns:
        redirect: To Stripe checkout URL
        
    Related Functions:
        - create_session(): Stripe helper
        - store_session(): Caches info
    """
    if 'pterodactyl_id' in session:
        pass
    else:
        ptero_id = get_ptero_id(session['email'])
        session['pterodactyl_id'] = ptero_id

    product = next((p for p in products if p.get('price_link') == price_link), None)
    if product is None or float(product.get('price') or 0) <= 0:
        return 'Unknown credit package', 400

    ensure_checkout_orders()
    check_session = stripe.checkout.Session.create(
        payment_method_types=['card', 'cashapp', "wechat_pay", "alipay", 'crypto'],
        payment_method_options={
        "wechat_pay": {
          "client": "web"
        
            }
        },
        allow_promotion_codes=True,
        line_items=[{
            'price': price_link,
            'quantity': 1,
        }],
        mode='payment',
        success_url=checkout_success_url(YOUR_SUCCESS_URL),
        cancel_url=YOUR_CANCEL_URL,
        customer=get_stripe_customer(session['account_id'])
    )
    # Save before redirecting the buyer, so reconciliation survives restarts and
    # does not depend on the browser cookie or webhook delivery.
    remember_checkout(check_session['id'], session['account_id'], session['email'], price_link, product['price'])
    session['price_link'] = price_link
    session['pay_id'] = check_session['id']
    return redirect(check_session['url'])


@store.route('/stripe_webhook', methods=['POST'])
def stripe_webhook():
    payload = request.data
    sig_header = request.headers.get('Stripe-Signature')

    if not STRIPE_WEBHOOK_SECRET:
        webhook_log("Stripe webhook secret not configured", database_log=True)
        return "", 500

    try:
        event = stripe.Webhook.construct_event(
            payload=payload,
            sig_header=sig_header,
            secret=STRIPE_WEBHOOK_SECRET,
        )
    except Exception as e:
        webhook_log(f"Stripe webhook signature verification failed: {e}", database_log=True)
        return "", 400

    event_type = event.get('type')
    data_object = (event.get('data') or {}).get('object') or {}

    try:
        if event_type in ('checkout.session.completed', 'checkout.session.async_payment_succeeded'):
            pay_id = str(data_object.get('id') or '').strip()
            if not pay_id:
                raise ValueError('Checkout event missing its session identifier')
            credited = fulfill_checkout(pay_id)
            if credited:
                webhook_log(f"PAYMENT SUCCESS: credits committed for checkout {pay_id}", database_log=True)
            return "", 200

        if event_type in ('invoice.payment_succeeded', 'invoice.paid'):
            invoice_id = str(data_object.get('id') or '').strip()
            remember_invoice(invoice_id)
            if fulfill_invoice(invoice_id):
                webhook_log(f"Subscription credits committed for invoice {invoice_id}", database_log=True)
            return "", 200

        if event_type == 'invoice.payment_failed':
            invoice_id = str(data_object.get('id') or '').strip()
            invoice = stripe.Invoice.retrieve(invoice_id)
            # Events can arrive out of order. A recovered payment is not a new failure.
            if invoice.get('status') == 'paid':
                remember_invoice(invoice_id)
                fulfill_invoice(invoice_id)
                return "", 200
            if invoice.get('status') != 'open':
                return "", 200
            metadata = invoice_snapshot(invoice)
            subscription_id = invoice_subscription(invoice)
            if not metadata and subscription_id:
                metadata = stripe.Subscription.retrieve(subscription_id).get('metadata') or {}
            email = metadata.get('user_email') or invoice.get('customer_email')
            if email:
                send_email(email, "Subscription payment failed",
                           "Your subscription payment could not be completed. Please update your payment method in the billing portal. Stripe will follow the subscription's configured retry policy.",
                           current_app._get_current_object())
            # Do not delete subscriptions on a transient failure; this prevents
            # Stripe's normal retry/payment recovery and can race a successful payment.
            webhook_log(f"Subscription payment needs attention: invoice={invoice_id}", database_log=True)
            return "", 200

    except Exception as e:
        webhook_log(f"Stripe webhook handler error: {e}", database_log=True)
        return "", 500

    return "", 200


@store.route('/success', methods=['GET'])
@login_required
@verified_billing_required
def success():
    # Each Stripe return URL carries its own ID; starting another purchase must
    # not make this callback settle the newer purchase instead.
    pay_id = request.args.get('session_id') or session.get('pay_id')
    if not pay_id or len(pay_id) > 255:
        flash("Invalid payment session")
        return redirect(url_for("user.index"))
    try:
        credited = fulfill_checkout(pay_id, expected_email=session.get('email', ''),
                                    expected_account_id=session.get('account_id'))
    except PaymentReviewRequired:
        flash("This payment requires staff review because it was refunded or disputed. Please contact support.")
        return redirect(url_for("user.index"))
    except PermissionError:
        flash("Invalid payment")
        return redirect(url_for("user.index"))
    except Exception as exc:
        webhook_log(f"Checkout credit failed for session {pay_id}: {type(exc).__name__}", database_log=True)
        return "Payment processing is temporarily unavailable. Please refresh this page to retry.", 503
    if credited is None:
        flash("Payment is still awaiting confirmation. Credits will be added automatically when it is confirmed.")
        return redirect(url_for("user.index"))
    # Do not erase a different, newer checkout still open in another tab.
    if session.get('pay_id') == pay_id:
        session.pop('pay_id', None)
        session.pop('price_link', None)
    flash("Payment successful! Credits added." if credited else "This payment has already been processed.")
    return redirect(url_for("user.index"))


@store.route('/cancel', methods=['GET'])
@login_required
@verified_billing_required
def cancel():
    """
    Handle cancelled payment callback from Stripe.
    
    Templates:
        - cancel.html: Cancellation page
        
    Process:
        1. Clear session data
        2. Log cancellation
        3. Show message
        
    Session Data:
        Clears:
        - pay_id
        - price_link
        
    Returns:
        template: cancel.html with:
            - message: Cancellation notice
            
    Related Functions:
        - clear_session(): Removes data
        - log_cancel(): Records event
    """
    #return render_template('cancel.html')
    return redirect(url_for("user.index"))
