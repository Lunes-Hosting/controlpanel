from flask import Blueprint, request, render_template, session, flash
import sys
from threadedreturn import ThreadWithReturnValue
sys.path.append("..")
from scripts import *
from products import products
import stripe

stripe.api_key = STRIPE_SECRET_KEY
store = Blueprint('store', __name__)
active_payments = []


@store.route("/")
def storepage():
    if 'email' not in session:
        return redirect(url_for("user.login_user"))
    after_request(session, request.environ, True)

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
def create_checkout_session(price_link: str):
    if 'email' not in session:
        return redirect(url_for("user.login_user"))
    after_request(session, request.environ, True)

    if 'pterodactyl_id' in session:
        pass
    else:
        ptero_id = get_ptero_id(session['email'])
        session['pterodactyl_id'] = ptero_id

    check_session = stripe.checkout.Session.create(
        payment_method_types=['card', 'cashapp'],
        allow_promotion_codes=True,
        line_items=[{
            'price': price_link,
            'quantity': 1,
        }],
        mode='payment',
        success_url=YOUR_SUCCESS_URL,
        cancel_url=YOUR_CANCEL_URL,
        customer_email=session['email']
    )
    active_payments.append(check_session['id'])
    session['price_link'] = price_link
    session['pay_id'] = check_session['id']
    return redirect(check_session['url'])


@store.route('/success', methods=['GET'])
def success():
    try:
        pay_id = session['pay_id']
    except KeyError:
        flash("not valid payment")
        return url_for('index')
    check_session = stripe.checkout.Session.retrieve(pay_id)
    if check_session is None or pay_id not in active_payments:
        flash("not valid payment")
        return url_for('index')
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
            return url_for('index')
        add_credits(check_session['customer_email'], credits_to_add)
        flash("Success")
        return url_for('index')

    elif check_session['status'] == 'expired':
        active_payments.remove(pay_id)
        flash("payment link expired")
        return url_for('index')


@store.route('/cancel', methods=['GET'])
def cancel():
    # Handle the case when the customer cancels the payment
    return render_template('cancel.html')
