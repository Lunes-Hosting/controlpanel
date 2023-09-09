from flask import Flask
from flask_session import Session
from Routes.AuthenticationHandler import *
from Routes.Servers import *
from Routes.Store import *
from flask_apscheduler import APScheduler
from flask import Flask, render_template, request, redirect, url_for, flash
from flask_mail import Mail, Message
from flask_caching import Cache
import random, string
import threading
app = Flask(__name__, "/static")

app.register_blueprint(user)
app.register_blueprint(servers, url_prefix="/servers")
app.register_blueprint(store, url_prefix="/store")
class Config:
    SCHEDULER_API_ENABLED = True


app.config["SESSION_PERMANENT"] = True
app.config["SESSION_TYPE"] = "filesystem"
app.config["SECRET_KEY"] = SECRET_KEY
# app.config['SERVER_NAME'] = 'node2.lunes.host:27112'

# Initialize the session
Session(app)

app.config.from_object(Config())

# initialize scheduler
scheduler = APScheduler()
# if you don't wanna use a config, you can set options here:
# scheduler.api_enabled = True
scheduler.init_app(app)

app.config['MAIL_SERVER'] = MAIL_SERVER
app.config['MAIL_PORT'] = MAIL_PORT
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = MAIL_USERNAME
app.config['MAIL_PASSWORD'] = MAIL_PASSWORD
app.config['MAIL_DEFAULT_SENDER'] = MAIL_DEFAULT_SENDER

mail = Mail(app)

# Configuration for Flask-Caching
cache = Cache(app, config={'CACHE_TYPE': 'simple'})

# Placeholder for the password reset token expiration time (in seconds)
TOKEN_EXPIRATION_TIME = 1800  # 30 minutes

# A dictionary to store the reset tokens temporarily
reset_tokens = {}


# Function to generate a random reset token
def generate_reset_token():
    return ''.join(random.choices(string.ascii_letters + string.digits, k=20))

def send_email(email: str, reset_token: str, app):
    with app.app_context():
        msg = Message('Password Reset Request', recipients=[email])
        msg.body = f'Please click the link below to reset your password:\n\n {HOSTED_URL}reset_password/{reset_token}'
        mail.send(msg)


# Route to request a password reset (via email)
@app.route('/reset_password', methods=['GET', 'POST'])
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
@app.route('/reset_password/<token>', methods=['GET', 'POST'])
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
                    database=DATABASE
                )
                password_hash = bcrypt.hashpw(password.encode('utf-8'), salt)
                cursor = cnx.cursor()
            
                query = f"UPDATE users SET password = %s WHERE email = '{email}'"
                ptero_id = get_ptero_id(email)
                values = (password_hash.decode(),)
                print(password_hash, email, type(email))
                cursor.execute(query, values)
                info = requests.get(f"{PTERODACTYL_URL}api/application/users/{ptero_id[0][0]}", headers=HEADERS).json()['attributes']
                body = {
                    "username": info['username'],
                    "email": info['email'],
                    "first_name": info['first_name'],
                    "last_name": info['last_name'],
                    "password": password
                }
                
                res = requests.patch(f"{PTERODACTYL_URL}api/application/users/{ptero_id[0][0]}", headers=HEADERS, json=body)
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


# Function to generate a verification token
def generate_verification_token():
    return ''.join(random.choices(string.ascii_letters + string.digits, k=20))

# Function to send a verification email
def send_verification_email(email: str, verification_token: str, app):
    with app.app_context():
        print("started emails")
        msg = Message('Email Verification', recipients=[email])
        msg.body = f'Please click the link below to verify your email:\n\n {HOSTED_URL}verify_email/{verification_token}'
        mail.send(msg)
        print(f"sent email to {email}")
# Modified register_user function
@app.route('/register', methods=['POST', 'GET'])
def register_user():
    if request.method == "POST":
        data = request.form
        email = data.get('email')
        password = data.get('password')
        name = data.get('username')
        ip = request.headers.get('Cf-Connecting-Ip', request.remote_addr)
        res = register(email, password, name, ip)
        if type(res) == str:
            flash(res + " If this in error please contact support at owner@lunes.host")
            return render_template('register.html')
        # Generate a verification token
        verification_token = generate_verification_token()

        # Store the verification token in the cache with the email as the key
        cache.set(email, verification_token, timeout=TOKEN_EXPIRATION_TIME)

        # Compose and send the verification email
        email_thread = threading.Thread(target=send_verification_email, args=(email, verification_token, app,), daemon=True)
        email_thread.start()

        flash('A verification email has been sent to your email address. Please check your inbox and spam to verify your email.')
        return redirect(url_for('index'))
    else:
        return render_template("register.html")

@app.route("/resend_confirmation_email")
def resend_confirmation_email():
    if not 'email' in session:
        return redirect(url_for('user.login_user'))
    verification_token = generate_verification_token()

    # Store the verification token in the cache with the email as the key
    cache.set(session['email'], verification_token, timeout=TOKEN_EXPIRATION_TIME)

    # Compose and send the verification email
    email_thread = threading.Thread(target=send_verification_email, args=(session['email'], verification_token, app,), daemon=True)
    email_thread.start()
    flash("Sent a verification email")
    return redirect(url_for('index'))

# Route to confirm the email using the token
@app.route('/verify_email/<token>', methods=['GET'])
def verify_email(token):
    if not 'email' in session:
        return redirect(url_for('user.login_user'))
    
    email = session['email']

    # Retrieve the stored verification token from the cache
    stored_token = cache.get(email)

    if stored_token and stored_token == token:
        cnx = mysql.connector.connect(
        host=HOST,
        user=USER,
        password=PASSWORD,
        database=DATABASE
        )
        cursor = cnx.cursor(buffered=True)
            
        query = f"UPDATE users SET email_verified_at = '{datetime.datetime.now()}' where email = '{email}'"
        cursor.execute(query)
        cnx.commit()
        

        # Remove the verification token from the cache
        cache.delete(email)

        flash('Your email has been successfully verified.')
    else:
        flash('Invalid or expired verification token.')

    return redirect(url_for('index'))


@scheduler.task('interval', id='do_job_1', seconds=3600, misfire_grace_time=900)
def job1():
    print("started job1")
    use_credits()
    print("finished job 2")
    
@scheduler.task('interval', id='do_job_2', seconds=120, misfire_grace_time=900)
def job2():
    print("started job2")
    check_to_unsuspend()
    print("finished job 2")
scheduler.start()

@scheduler.task('interval', id='do_sync_users', seconds=30, misfire_grace_time=900)
def sync_users():
    print("started users sync")
    sync_users_script()
    print("finished job 2")




@app.route('/')
def index():
        if 'email' in session:
            update_last_seen(session['email'])
            print(request.headers, "hmm")
            print(request.environ, "hm2")
            update_ip(session['email'], request.headers.get('Cf-Connecting-Ip'))
            return render_template('index.html')
        else:
            return redirect(url_for('user.login_user'))

# job1()
app.run(debug=False, host="0.0.0.0", port=27112)