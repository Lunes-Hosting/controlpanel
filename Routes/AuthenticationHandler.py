from flask import Blueprint, request, render_template, session, flash, current_app
import sys
from threadedreturn import ThreadWithReturnValue
sys.path.append("..")
from scripts import *
import json
from flask_caching import Cache
from flask_limiter import Limiter

# Create a blueprint for the user routes
user = Blueprint('user', __name__)
with current_app.app_context:
    cache = Cache(current_app, config={'CACHE_TYPE': 'simple'})
pterocache = PteroCache()
# Placeholder for the password reset token expiration time (in seconds)
TOKEN_EXPIRATION_TIME = 1800  # 30 minutes



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

        print(1)
        data = request.form
        email = data.get('email')
        password = data.get('password')
        try:
            response = login(email, password)
            if response is None:
                flash("Not correct info please make sure you have an account")
                return redirect(url_for('user.login_user'))
            print(8)
            try:
                _ = json.load(response)
                return response
            except AttributeError:
                session['email'] = email
                print("yes")
                return redirect(url_for('index'))
        except Exception as e:
            print(e)
            pass
    else:
        print(2)
        return render_template("login.html", RECAPTCHA_PUBLIC_KEY=RECAPTCHA_SITE_KEY)


@user.route('/')
def index():
    if 'email' not in session:
        return redirect(url_for("user.login_user"))
    after_request(session, request.environ, True)
    current_credits = get_credits(session['email'])
    servers = list_servers(get_ptero_id(session['email'])[0])
    server_count = len(servers)
    monthly_usage = 0
    for server in servers:
        product = convert_to_product(server)
        monthly_usage += product['price']

    cnx = mysql.connector.connect(
        host=HOST,
        user=USER,
        password=PASSWORD,
        database=DATABASE,
        charset='utf8mb4',
        collation='utf8mb4_unicode_ci'
    )

    cursor = cnx.cursor(buffered=True)
    cursor.execute("SELECT name from users where email = %s", (session['email'],))
    username = cursor.fetchone()
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
        email_thread = threading.Thread(target=send_email, args=(str(email), reset_token, app,), daemon=True)
        email_thread.start()
        flash('An email with instructions to reset your password has been sent.')
        return redirect(url_for('user.login_user'))

    return render_template('reset_password.html')


# Route to confirm the password reset using the token
@user.route('/reset_password/<token>', methods=['GET', 'POST'])
def reset_password_confirm(token):
    print(token)
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')
        tokendone = request.form.get("token")

        # Check if the email exists in your user database
        # Replace this with your own logic to validate the email

        # Retrieve the reset token from the cache using the email as the key
        reset_token = cache.get(email)

        if reset_token and reset_token == tokendone:
            if password == confirm_password:
                # Update the password in your user database
                # Replace this with your own logic to update the password

                # Remove the reset token from the cache
                cache.delete(email)
                salt = bcrypt.gensalt(rounds=10)
                cnx = mysql.connector.connect(
                    host=HOST,
                    user=USER,
                    password=PASSWORD,
                    database=DATABASE,
                    charset='utf8mb4',
                    collation='utf8mb4_unicode_ci'
                )
                password_hash = bcrypt.hashpw(password.encode('utf-8'), salt)
                cursor = cnx.cursor()

                query = f"UPDATE users SET password = %s WHERE email = %s"
                ptero_id = get_ptero_id(email)
                values = (password_hash.decode(), email)
                print(password_hash, email, type(email))
                cursor.execute(query, values)
                info = requests.get(f"{PTERODACTYL_URL}api/application/users/{ptero_id[0]}", headers=HEADERS).json()[
                    'attributes']
                body = {
                    "username": info['username'],
                    "email": info['email'],
                    "first_name": info['first_name'],
                    "last_name": info['last_name'],
                    "password": password
                }

                requests.patch(f"{PTERODACTYL_URL}api/application/users/{ptero_id[0]}", headers=HEADERS,
                               json=body)
                cnx.commit()
                cursor.close()
                cnx.close()

                flash('Your password has been successfully reset.')
                return redirect(url_for('user.login_user'))
            else:
                flash('Passwords do not match.')
        else:
            flash('Invalid or expired reset token.')

    return render_template('reset_password_confirm.html', token=token)



# Modified register_user function
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
        if type(res) == str:
            flash(res + " If this in error please contact support at panel@lunes.host")
            return render_template("register.html", RECAPTCHA_PUBLIC_KEY=RECAPTCHA_SITE_KEY)
        # Generate a verification token
        verification_token = generate_verification_token()

        # Store the verification token in the cache with the email as the key
        cache.set(email, verification_token, timeout=TOKEN_EXPIRATION_TIME)

        # Compose and send the verification email
        email_thread = threading.Thread(target=send_verification_email, args=(email, verification_token, app,),
                                        daemon=True)
        email_thread.start()
        # TEMP REMOVE REQUIRED EMAIL VERIF

        query = f"UPDATE users SET email_verified_at = '{datetime.datetime.now()}' where email = %s"
        use_database(query, (email,))
        # TEMP REMOVE REQUIRED EMAIL VERIF
        flash(
            'A verification email has been sent to your email address. Please check your inbox and spam to verify '
            'your email.')
        return redirect(url_for('index'))
    else:
        return render_template("register.html", RECAPTCHA_PUBLIC_KEY=RECAPTCHA_SITE_KEY)


@user.route("/resend_confirmation_email")

def resend_confirmation_email():
    if 'email' not in session:
        return redirect(url_for("user.login_user"))
    after_request(session=session, request=request.environ, require_login=True)
    verification_token = generate_verification_token()

    # Store the verification token in the cache with the email as the key
    cache.set(session['email'], verification_token, timeout=TOKEN_EXPIRATION_TIME)

    # Compose and send the verification email
    email_thread = threading.Thread(target=send_verification_email, args=(session['email'], verification_token, app,),
                                    daemon=True)
    email_thread.start()
    flash("Sent a verification email")
    return redirect(url_for('index'))


# Route to confirm the email using the token
@user.route('/verify_email/<token>', methods=['GET'])
def verify_email(token):
    if 'email' not in session:
        return redirect(url_for("user.login_user"))
    after_request(session=session, request=request.environ, require_login=True)

    email = session['email']

    # Retrieve the stored verification token from the cache
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

        query = f"UPDATE users SET email_verified_at = '{datetime.datetime.now()}' where email = %s"
        cursor.execute(query, (email,))
        cnx.commit()

        # Remove the verification token from the cache
        cache.delete(email)

        flash('Your email has been successfully verified.')
    else:
        flash('Invalid or expired verification token.')

    return redirect(url_for('index'))


@user.route('/logout', methods=['GET'])
def logout():
    session.clear()
    return redirect(url_for("index"))
