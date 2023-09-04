from flask import Blueprint, request, render_template, redirect, url_for, session, jsonify, flash
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
    if not 'email' in session:
        return redirect(url_for('user.login_user'))
        
    if 'pterodactyl_id' in session:
        id = session['pterodactyl_id']
    else:
        id = get_ptero_id(session['email'])
        session['pterodactyl_id'] = id
        
    update_last_seen(session['email'])
    update_ip(session['email'], request.environ.get('REMOTE_ADDR', request.remote_addr))
    
    products_local = products
    for product in products_local:
        if product['price_link'] is None:
            products_local.remove(product)
    print(products_local)
    return render_template("store.html", products=products_local)


@store.route('/checkout/<price_link>', methods=['POST', 'GET'])
def create_checkout_session(price_link: str):
    if not 'email' in session:
        return redirect(url_for('user.login_user'))
        
    if 'pterodactyl_id' in session:
        id = session['pterodactyl_id']
    else:
        id = get_ptero_id(session['email'])
        session['pterodactyl_id'] = id
        
    update_last_seen(session['email'])
    update_ip(session['email'], request.environ.get('REMOTE_ADDR', request.remote_addr))
    
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
        flash("not valid payment")
        return url_for('index')
    check_session = stripe.checkout.Session.retrieve(id)
    if check_session is None or id not in active_payments:
        flash("not valid payment")
        return url_for('index')
    if check_session['payment_status'] =='paid':
        print(check_session)
        active_payments.remove(id)
        for product in products:
            if product['price_link'] == session['price_link']:
                credits_to_add = product['price']
        add_credits(check_session['customer_email'], credits_to_add)
        flash("Success")
        return url_for('index')
        
    elif check_session['status'] == 'expired':
        active_payments.remove(id)
        flash("payment link expired")
        return url_for('index') 
    
    
@store.route('/cancel', methods=['GET'])
def cancel():
    # Handle the case when the customer cancels the payment
    return render_template('cancel.html')