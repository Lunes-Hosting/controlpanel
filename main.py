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
app.config["SECRET_KEY"] = "LunesHost"
app.config['SERVER_NAME'] = 'betadash.lunes.host'

# Initialize the session
Session(app)

app.config.from_object(Config())

# initialize scheduler
scheduler = APScheduler()
# if you don't wanna use a config, you can set options here:
# scheduler.api_enabled = True
scheduler.init_app(app)

app.config['MAIL_SERVER'] = 'box.courvix.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = 'luneshost@courvix.com'
app.config['MAIL_PASSWORD'] = 'Pinta123!'
app.config['MAIL_DEFAULT_SENDER'] = 'luneshost@courvix.com'

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
    print("started emailer")
    with app.app_context():
        msg = Message('Password Reset Request', recipients=[email])
        msg.body = f'Please click the link below to reset your password:\n\n{url_for("reset_password_confirm", token=reset_token, _external=True)}'
        mail.send(msg)
        print("email sent")

# Route to request a password reset (via email)
@app.route('/reset_password', methods=['GET', 'POST'])
def reset_password():
    if request.method == 'POST':
        print(1)
        email = request.form.get('email')

        # Check if the email exists in your user database
        # Replace this with your own logic to validate the email

        # Generate a reset token
        reset_token = generate_reset_token()

        # Store the reset token in the cache with the email as the key
        cache.set(email, reset_token, timeout=TOKEN_EXPIRATION_TIME)
        print(2, email)
        # Compose and send the reset email
        email_thread = threading.Thread(target=send_email, args=(str(email), reset_token, app,), daemon=True)
        email_thread.start()
        print(reset_token)
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

                values = (password_hash.decode(),)
                print(password_hash, email, type(email))
                cursor.execute(query, values)
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



@app.route('/')
def index():
        if 'email' in session:
            return render_template('index.html')
        else:
            return redirect(url_for('user.login_user'))

# job1()
app.run(debug=True, host="0.0.0.0", port=27112)