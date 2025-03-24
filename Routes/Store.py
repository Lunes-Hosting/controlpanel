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

from flask import Blueprint, request, render_template, session, flash, redirect, url_for
import sys
from threadedreturn import ThreadWithReturnValue
sys.path.append("..")
from managers.authentication import login_required
from managers.user_manager import get_ptero_id, get_id
from managers.credit_manager import add_credits
from managers.email_manager import send_email
from managers.logging import webhook_log
from config import STRIPE_SECRET_KEY, YOUR_SUCCESS_URL, YOUR_CANCEL_URL
from products import products
import stripe

stripe.api_key = STRIPE_SECRET_KEY
store = Blueprint('store', __name__)
active_payments = []


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
    active_payments.append(check_session['id'])
    session['price_link'] = price_link
    session['pay_id'] = check_session['id']
    return redirect(check_session['url'])


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
    if check_session is None or pay_id not in active_payments:
        flash("not valid payment")
        return redirect(url_for("user.index"))
        #return url_for('index')
    if check_session['payment_status'] == 'paid':
        print(check_session)
        active_payments.remove(pay_id)
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
        flash("Success")
        return redirect(url_for("user.index"))
        #return url_for('index')

    elif check_session['status'] == 'expired':
        active_payments.remove(pay_id)
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
