from flask import Blueprint, request, render_template, redirect, url_for, session, jsonify
import sys
sys.path.append("..") 
from scripts import *
from products import products
import json
import stripe
stripe.api_key = STRIPE_SECRET_KEY
store = Blueprint('store', __name__)
active_payments = []

@store.route("/")
def storepage():
    products_local = products
    for product in products_local:
        if product['price_link'] is None:
            products_local.remove(product)
    print(products_local)
    return render_template("store.html", products=products_local)


@store.route('/checkout/<price_link>', methods=['POST', 'GET'])
def create_checkout_session(price_link: str):
    check_session = stripe.checkout.Session.create(
    payment_method_types=['card', 'cashapp'],
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
        id = session['pay_id']
    except KeyError:
        return "not valid payment"
    check_session = stripe.checkout.Session.retrieve(id)
    if check_session is None or id not in active_payments:
        return "not valid payment"
    if check_session['payment_status'] =='paid':
        print(check_session)
        active_payments.remove(id)
        for product in products:
            if product['price_link'] == session['price_link']:
                credits_to_add = product['price']
        add_credits(check_session['customer_email'], credits_to_add)
        return "got moneys"
        
    elif check_session['status'] == 'expired':
        active_payments.remove(id)
        return "payment link expired"
    
@store.route('/cancel', methods=['GET'])
def cancel():
    # Handle the case when the customer cancels the payment
    return render_template('cancel.html')