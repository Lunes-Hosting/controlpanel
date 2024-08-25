from flask import Blueprint, request, render_template, session, flash, current_app, redirect, url_for
import sys
import threading
import json
import datetime
import requests
import bcrypt
import mysql.connector
from cacheext import cache
from flask_limiter import Limiter
sys.path.append("..")
from pterocache import *
from scripts import *

# Create a blueprint for the user routes
user = Blueprint('user', __name__)
TOKEN_EXPIRATION_TIME = 1800  # 30 minutes

pterocache = PteroCache()

@user.route('/login', methods=['POST', 'GET'])
def login_user():
    after_request(session=session, request=request.environ)
    if request.method == "POST":
        recaptcha_response = request.form.get('g-recaptcha-response')
        data = {
            'secret': RECAPTCHA_SECRET_KEY,
            'response': recaptcha_response
        }

        response = requests.post('https://www.google.com/recaptcha/api/siteverify', data=data)
        result = response.json()
        if not result['success']:
            flash("Failed captcha please try again")
            return render_template("login.html", RECAPTCHA_PUBLIC_KEY=RECAPTCHA_SITE_KEY)

        data = request.form
        email = data.get('email')
        password = data.get('password')
        try:
            response = login(email, password)
            if response is None:
                flash("Incorrect information. Please ensure you have an account.")
                return redirect(url_for('user.login_user'))

            try:
                _ = json.load(response)
                return response
            except AttributeError:
                session['email'] = email
                return redirect(url_for('index'))
        except Exception as e:
            print(e)
            flash("An error occurred during login. Please try again.")
    return render_template("login.html", RECAPTCHA_PUBLIC_KEY=RECAPTCHA_SITE_KEY)


@user.route('/')
def index():
    if 'email' not in session:
        return redirect(url_for("user.login_user"))

    after_request(session, request.environ, True)
    current_credits = get_credits(session['email'])
    servers = list_servers(get_ptero_id(session['email'])[0])
    server_count = len(servers)
    monthly_usage = sum(convert_to_product(server)['price'] for server in servers)

    cnx = mysql.connector.connect(
        host=HOST,
        user=USER,
        password=PASSWORD,
        database=DATABASE,
        charset='utf8mb4',
        collation='utf8mb4_unicode_ci'
    )

    cursor = cnx.cursor(buffered=True)
    cursor.execute("SELECT name FROM users WHERE email = %s", (session['email'],))
    username = cursor.fetchone()
    cursor.close()
    cnx.close()

    return render_template("account.html", credits=int(current_credits), server_count=server_count,
                           username=username[0], email=session['email'], monthly_usage=monthly_usage)

# Route to request a password reset (via email)
@user.route('/reset_password', methods=['GET', 'POST'])
def reset_password():
    if request.method == 'POST':
        email = request.form.get('email')

        # Check if the email exists in your user database
        # Replace this with your own logic to validate the email

        # Generate a reset token
        reset_token = generate_reset_token()

        # Store the reset token in the cache with the email as the key
        cache.set(email, reset_token, timeout=TOKEN_EXPIRATION_TIME)
        # Compose and send the reset email
        email_thread = threading.Thread(target=send_reset_email, args=(str(email), reset_token, current_app._get_current_object()), daemon=True)
        email_thread.start()
        flash('An email with instructions to reset your password has been sent.')
        return redirect(url_for('user.login_user'))

    return render_template('reset_password.html')



@user.route('/reset_password/<token>', methods=['GET', 'POST'])
def reset_password_confirm(token):
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')
        tokendone = request.form.get("token")

        reset_token = cache.get(email)
        print(cache.get(email), token)

        if reset_token and reset_token == tokendone:
            if password == confirm_password:
                salt = bcrypt.gensalt(rounds=10)
                password_hash = bcrypt.hashpw(password.encode('utf-8'), salt)

                cnx = mysql.connector.connect(
                    host=HOST,
                    user=USER,
                    password=PASSWORD,
                    database=DATABASE,
                    charset='utf8mb4',
                    collation='utf8mb4_unicode_ci'
                )
                cursor = cnx.cursor()

                query = "UPDATE users SET password = %s WHERE email = %s"
                ptero_id = get_ptero_id(email)
                values = (password_hash.decode(), email)
                cursor.execute(query, values)

                info = requests.get(f"{PTERODACTYL_URL}api/application/users/{ptero_id[0]}", headers=HEADERS).json()['attributes']
                body = {
                    "username": info['username'],
                    "email": info['email'],
                    "first_name": info['first_name'],
                    "last_name": info['last_name'],
                    "password": password
                }

                requests.patch(f"{PTERODACTYL_URL}api/application/users/{ptero_id[0]}", headers=HEADERS, json=body)

                cnx.commit()
                cursor.close()
                cnx.close()

                cache.delete(email)

                flash('Your password has been successfully reset.')
                return redirect(url_for('user.login_user'))
            else:
                flash('Passwords do not match.')
        else:
            flash('Invalid or expired reset token.')

    return render_template('reset_password_confirm.html', token=token)


@user.route('/register', methods=['POST', 'GET'])
def register_user():
    if request.method == "POST":
        recaptcha_response = request.form.get('g-recaptcha-response')
        data = {
            'secret': RECAPTCHA_SECRET_KEY,
            'response': recaptcha_response
        }

        response = requests.post('https://www.google.com/recaptcha/api/siteverify', data=data)
        result = response.json()
        if not result['success']:
            flash("Failed captcha please try again")
            return render_template("register.html", RECAPTCHA_PUBLIC_KEY=RECAPTCHA_SITE_KEY)

        data = request.form
        email = data.get('email')
        password = data.get('password')
        name = data.get('username')
        ip = request.headers.get('Cf-Connecting-Ip', request.remote_addr)

        res = register(email, password, name, ip)
        if isinstance(res, str):
            flash(res + " If this is an error, please contact support.")
            return render_template("register.html", RECAPTCHA_PUBLIC_KEY=RECAPTCHA_SITE_KEY)

        verification_token = generate_verification_token()

        cache.set(email, verification_token, timeout=TOKEN_EXPIRATION_TIME)

        email_thread = threading.Thread(target=send_verification_email, args=(email, verification_token, current_app._get_current_object()), daemon=True)
        email_thread.start()

        query = "UPDATE users SET email_verified_at = %s WHERE email = %s"
        use_database(query, (datetime.datetime.now(), email))

        flash('A verification email has been sent. Please check your inbox and spam to verify your email.')
        return redirect(url_for('index'))

    return render_template("register.html", RECAPTCHA_PUBLIC_KEY=RECAPTCHA_SITE_KEY)


@user.route("/resend_confirmation_email")
def resend_confirmation_email():
    if 'email' not in session:
        return redirect(url_for("user.login_user"))

    after_request(session=session, request=request.environ, require_login=True)
    verification_token = generate_verification_token()

    cache.set(session['email'], verification_token, timeout=TOKEN_EXPIRATION_TIME)

    email_thread = threading.Thread(target=send_verification_email, args=(session['email'], verification_token, current_app._get_current_object()), daemon=True)
    email_thread.start()

    flash("Sent a verification email")
    return redirect(url_for('index'))


@user.route('/verify_email/<token>', methods=['GET'])
def verify_email(token):
    if 'email' not in session:
        return redirect(url_for("user.login_user"))

    after_request(session=session, request=request.environ, require_login=True)

    email = session['email']
    stored_token = cache.get(email)

    if stored_token and stored_token == token:
        cnx = mysql.connector.connect(
            host=HOST,
            user=USER,
            password=PASSWORD,
            database=DATABASE,
            charset='utf8mb4',
            collation='utf8mb4_unicode_ci'
        )
        cursor = cnx.cursor(buffered=True)

        query = "UPDATE users SET email_verified_at = %s WHERE email = %s"
        cursor.execute(query, (datetime.datetime.now(), email))
        cnx.commit()

        cache.delete(email)
        cursor.close()
        cnx.close()

        flash('Your email has been successfully verified.')
    else:
        flash('Invalid or expired verification token.')

    return redirect(url_for('index'))


@user.route('/logout', methods=['GET'])
def logout():
    session.clear()
    return redirect(url_for("index"))
