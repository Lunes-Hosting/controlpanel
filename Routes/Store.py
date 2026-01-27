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
from managers.user_manager import get_ptero_id, get_id
from managers.credit_manager import add_credits
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
store = Blueprint('store', __name__)


def _ensure_processed_invoice_table_exists() -> None:
    DatabaseManager.execute_query(
        """
        CREATE TABLE IF NOT EXISTS stripe_processed_invoices (
            invoice_id VARCHAR(255) PRIMARY KEY,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def _already_processed_invoice(invoice_id: str) -> bool:
    row = DatabaseManager.execute_query(
        "SELECT invoice_id FROM stripe_processed_invoices WHERE invoice_id = %s",
        (invoice_id,),
    )
    return bool(row)


def _mark_invoice_processed(invoice_id: str) -> None:
    DatabaseManager.execute_query(
        "INSERT INTO stripe_processed_invoices (invoice_id) VALUES (%s)",
        (invoice_id,),
    )

@store.route("/")
@login_required
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
def create_subscription_checkout_session(price_link: str):
    if price_link not in SUBSCRIPTION_CREDIT_PRICES:
        flash("not valid subscription")
        return redirect(url_for("user.index"))

    credits_per_month = int(SUBSCRIPTION_CREDIT_PRICES[price_link])
    user_email = str(session.get('email', '')).strip().lower()
    if not user_email:
        flash("not valid user")
        return redirect(url_for("user.index"))

    success_url = f"{HOSTED_URL}user/?subscription=success"
    cancel_url = f"{HOSTED_URL}user/?subscription=cancel"

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
        customer_email=user_email,
        subscription_data={
            'metadata': {
                'user_email': user_email,
                'credits_per_month': str(credits_per_month),
                'price_link': price_link,
            }
        },
    )

    return redirect(check_session['url'])


@store.route('/portal', methods=['GET'])
@login_required
def billing_portal():
    user_email = str(session.get('email', '')).strip().lower()
    if not user_email:
        flash("not valid user")
        return redirect(url_for("user.index"))

    customers = stripe.Customer.list(email=user_email, limit=1)
    if customers and customers.get('data'):
        customer_id = customers['data'][0]['id']
    else:
        customer = stripe.Customer.create(email=user_email)
        customer_id = customer['id']

    portal_session = stripe.billing_portal.Session.create(
        customer=customer_id,
        return_url=f"{HOSTED_URL}user/",
    )
    return redirect(portal_session['url'])


@store.route('/checkout/<price_link>', methods=['POST', 'GET'])
@login_required
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

    check_session = stripe.checkout.Session.create(
        payment_method_types=['card', 'cashapp', "wechat_pay", "alipay"],
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
        success_url=YOUR_SUCCESS_URL,
        cancel_url=YOUR_CANCEL_URL,
        customer_email=str(session['email']).strip().lower()
    )
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
        if event_type == 'invoice.payment_succeeded':
            invoice_id = str(data_object.get('id') or '').strip()
            if not invoice_id:
                return "", 200

            _ensure_processed_invoice_table_exists()
            if _already_processed_invoice(invoice_id):
                return "", 200

            invoice = stripe.Invoice.retrieve(
                invoice_id,
                expand=['lines.data.price'],
            )

            subscription_id = invoice.get('subscription')
            subscription = None
            if subscription_id:
                subscription = stripe.Subscription.retrieve(subscription_id)

            metadata = (subscription or {}).get('metadata') or {}
            user_email = str(metadata.get('user_email') or '').strip().lower()

            credits_per_month = metadata.get('credits_per_month')
            credits_to_add = None
            try:
                if credits_per_month is not None:
                    credits_to_add = int(credits_per_month)
            except Exception:
                credits_to_add = None

            if credits_to_add is None:
                lines = (invoice.get('lines') or {}).get('data') or []
                if lines:
                    price = lines[0].get('price')
                    price_id = price.get('id') if isinstance(price, dict) else None
                    if price_id:
                        credits_to_add = SUBSCRIPTION_CREDIT_PRICES.get(price_id)
            if credits_to_add is None:
                webhook_log(f"Subscription invoice {invoice_id} missing credit mapping", database_log=True)
                return "", 200

            if not user_email:
                customer_id = invoice.get('customer')
                if customer_id:
                    customer = stripe.Customer.retrieve(customer_id)
                    user_email = str(customer.get('email') or '').strip().lower()

            if not user_email:
                webhook_log(f"Subscription invoice {invoice_id} missing user email", database_log=True)
                return "", 200

            add_credits(user_email, int(credits_to_add))
            _mark_invoice_processed(invoice_id)
            webhook_log(
                f"Subscription payment succeeded: {user_email} credited {int(credits_to_add)} (invoice {invoice_id})",
                database_log=True,
            )
            return "", 200

        if event_type == 'invoice.payment_failed':
            invoice_id = str(data_object.get('id') or '').strip()
            subscription_id = data_object.get('subscription')

            user_email = ''
            if subscription_id:
                subscription = stripe.Subscription.retrieve(subscription_id)
                metadata = (subscription.get('metadata') or {})
                user_email = str(metadata.get('user_email') or '').strip().lower()

            if not user_email:
                customer_id = data_object.get('customer')
                if customer_id:
                    customer = stripe.Customer.retrieve(customer_id)
                    user_email = str(customer.get('email') or '').strip().lower()

            if user_email:
                try:
                    send_email(
                        user_email,
                        "Subscription payment failed",
                        "Your subscription payment failed and your subscription has been canceled. Please update your payment method and resubscribe.",
                        current_app._get_current_object(),
                    )
                except Exception as e:
                    webhook_log(f"Failed sending payment failed email to {user_email}: {e}", database_log=True)

            if subscription_id:
                try:
                    stripe.Subscription.delete(subscription_id)
                except Exception as e:
                    webhook_log(f"Failed canceling subscription {subscription_id}: {e}", database_log=True)

            webhook_log(
                f"Subscription payment failed: invoice={invoice_id} subscription={subscription_id} email={user_email}",
                database_log=True,
            )
            return "", 200

    except Exception as e:
        webhook_log(f"Stripe webhook handler error: {e}", database_log=True)
        return "", 500

    return "", 200


@store.route('/success', methods=['GET'])
@login_required
def success():
    """
    Handle successful payment callback from Stripe.
    
    Templates:
        - success.html: Confirmation page
        
    API Calls:
        - Stripe: Verify payment
        
    Database Queries:
        - Update user credits
        - Log transaction
        
    Process:
        1. Verify session data
        2. Check payment status
        3. Calculate credit amount
        4. Update user balance
        5. Clear session data
        6. Log transaction
        
    Session Requirements:
        - pay_id: Checkout session ID
        - price_link: Product price ID
        
    Returns:
        template: success.html with:
            - amount: Credits added
            - balance: New total
            - transaction: Payment details
            
    Related Functions:
        - verify_payment(): Checks status
        - add_credits(): Updates balance
        - log_payment(): Records transaction
    """
    try:
        pay_id = session['pay_id']
    except KeyError:
        flash("not valid payment")
        return redirect(url_for("user.index"))
        #return url_for('index')
    check_session = stripe.checkout.Session.retrieve(pay_id)
    if check_session is None:
        flash("not valid payment")
        return redirect(url_for("user.index"))
        #return url_for('index')

    # Ensure the Stripe session belongs to the logged-in user
    session_email = str(session.get('email', '')).strip().lower()
    customer_email = str(check_session.get('customer_email', '')).strip().lower()
    if not session_email or session_email != customer_email:
        webhook_log(f"Payment session email mismatch for pay_id {pay_id}: session_email={session_email}, customer_email={customer_email}", database_log=True)
        flash("not valid payment")
        return redirect(url_for("user.index"))

    if check_session['payment_status'] == 'paid':
        print(check_session)
        credits_to_add = None
        for product in products:
            if product['price_link'] == session['price_link']:
                credits_to_add = product['price']
                break
        if credits_to_add is None:
            flash("Failed please open a ticket")
            return redirect(url_for("user.index"))
            #return url_for('index')
        add_credits(check_session['customer_email'], credits_to_add)
        webhook_log(f"**NEW PAYMENT ALERT**: User with email: {check_session['customer_email']} bought {credits_to_add} credits.", database_log=True)
        # Clear session payment identifiers so refresh can't double-credit
        session.pop('pay_id', None)
        session.pop('price_link', None)
        flash("Success")
        return redirect(url_for("user.index"))
        #return url_for('index')

    elif check_session['status'] == 'expired':
        session.pop('pay_id', None)
        session.pop('price_link', None)
        flash("payment link expired")
        return redirect(url_for("user.index"))
        #return url_for('index')


@store.route('/cancel', methods=['GET'])
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
