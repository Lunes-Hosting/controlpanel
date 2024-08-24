from flask import Blueprint, request, render_template, session, flash
import sys
from threadedreturn import ThreadWithReturnValue
sys.path.append("..")
from scripts import *
import json

# Create a blueprint for the user routes
user = Blueprint('user', __name__)


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


@user.route('/logout', methods=['GET'])
def logout():
    session.clear()
    return redirect(url_for("index"))
