from flask import Flask
from flask_session import Session
from Routes.AuthenticationHandler import *
app = Flask(__name__, "/static")
app.register_blueprint(user)


app.config["SESSION_PERMANENT"] = True
app.config["SESSION_TYPE"] = "filesystem"
app.config["SECRET_KEY"] = "LunesHost"

# Initialize the session
Session(app)

@app.route('/')
def index():
    try:
        if session['email']:
            return render_template('index.html')
        else:
            return "No email for u"
    except KeyError:
        return redirect(url_for('user.login_user'))
app.run(debug=True, host="0.0.0.0", port=8080)