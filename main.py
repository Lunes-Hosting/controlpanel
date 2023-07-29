from flask import Flask
from flask_session import Session
from Routes.AuthenticationHandler import *
from Routes.Servers import *
from Routes.Store import *
from Routes.Tickets import *
from flask_apscheduler import APScheduler
app = Flask(__name__, "/static")

app.register_blueprint(user)
app.register_blueprint(servers, url_prefix="/servers")
app.register_blueprint(store, url_prefix="/store")
app.register_blueprint(tickets, url_prefix="/ticket")
class Config:
    SCHEDULER_API_ENABLED = True


app.config["SESSION_PERMANENT"] = True
app.config["SESSION_TYPE"] = "filesystem"
app.config["SECRET_KEY"] = "LunesHost"

# Initialize the session
Session(app)

app.config.from_object(Config())

# initialize scheduler
scheduler = APScheduler()
# if you don't wanna use a config, you can set options here:
# scheduler.api_enabled = True
scheduler.init_app(app)

@scheduler.task('interval', id='do_job_1', seconds=3600, misfire_grace_time=900)
def job1():
    print("started job1")
    use_credits()
    print("finished job 2")
    
@scheduler.task('interval', id='do_job_2', seconds=60, misfire_grace_time=900)
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
app.run(debug=False, host="0.0.0.0", port=8080)