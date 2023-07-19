from flask import Flask
from flask_session import Session
from Routes.AuthenticationHandler import *
from Routes.Servers import *
app = Flask(__name__, "/static")

app.register_blueprint(user)
app.register_blueprint(servers, url_prefix="/servers")


app.config["SESSION_PERMANENT"] = True
app.config["SESSION_TYPE"] = "filesystem"
app.config["SECRET_KEY"] = "LunesHost"

# Initialize the session
Session(app)

@app.route('/')
def index():
        if 'email' in session:
            return render_template('index.html')
        else:
            return redirect(url_for('user.login_user'))

app.run(debug=True, host="0.0.0.0", port=8080)