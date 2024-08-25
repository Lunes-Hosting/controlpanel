from flask import Flask
from flask_apscheduler import APScheduler
from flask_limiter import Limiter
from flask_mail import Mail, Message

from Routes.AuthenticationHandler import *
from Routes.Servers import *
from Routes.Store import *
from Routes.Admin import *
from Routes.Tickets import *
from flask_session import Session
from multiprocessing import Process
from bot import enable_bot
import asyncio
import random
from scripts import *
#This imports the bot's code ONLY if the user wishes to use it


app = Flask(__name__, "/static")
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024  # Limit to 16 MB

def rate_limit_key():
    # Replace 'user_id' with the actual key used to store the user identifier in the session
    user_identifier = session.get('random_id', None)
    print(user_identifier, 1)
    return user_identifier


limiter = Limiter(rate_limit_key, app=app, default_limits=["200 per day", "50 per hour"])

limiter.limit("20/hour", key_func=rate_limit_key)(user)
limiter.limit("15/hour", key_func=rate_limit_key)(servers)
limiter.limit("15/hour", key_func=rate_limit_key)(tickets)
limiter.limit("10/hour", key_func=rate_limit_key)(store)

app.register_blueprint(user)
app.register_blueprint(servers, url_prefix="/servers")
app.register_blueprint(store, url_prefix="/store")
app.register_blueprint(admin, url_prefix="/admin")
app.register_blueprint(tickets, url_prefix="/tickets")


class Config:
    SCHEDULER_API_ENABLED = True


app.config["SESSION_PERMANENT"] = True
app.config["SESSION_TYPE"] = "filesystem"
app.config["SECRET_KEY"] = SECRET_KEY
# Initialize the session
Session(app)

app.config.from_object(Config())

# initialize scheduler
scheduler = APScheduler()
# if you don't want to use a config, you can set options here:
# scheduler.api_enabled = True
scheduler.init_app(app)

app.config['MAIL_SERVER'] = MAIL_SERVER
app.config['MAIL_PORT'] = MAIL_PORT
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = MAIL_USERNAME
app.config['MAIL_PASSWORD'] = MAIL_PASSWORD
app.config['MAIL_DEFAULT_SENDER'] = MAIL_DEFAULT_SENDER
app.config['RECAPTCHA_PUBLIC_KEY'] = RECAPTCHA_SITE_KEY
app.config['RECAPTCHA_PRIVATE_KEY'] = RECAPTCHA_SECRET_KEY

mail = Mail(app)

# Configuration for Flask-Caching




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

job_has_run = False

@scheduler.task('interval', id='do_run_job', seconds=5, misfire_grace_time=900)
def run_job():
    global job_has_run
    if not job_has_run:
        

        asyncio.run(enable_bot())

        job_has_run = True






@scheduler.task('interval', id='do_sync_users', seconds=1000, misfire_grace_time=900)
def sync_users():
    print("started users sync")
    sync_users_script()
    pterocache.update_all()
    print("finished job 2")
    

scheduler.start()
@app.route('/')
def index():
    if 'email' not in session:
        return redirect(url_for("user.login_user"))
    after_request(session=session, request=request.environ, require_login=True)




if __name__ == '__main__':
    # Create separate processes for Flask and the Discord bot
    app.run(debug=False, host="0.0.0.0", port=1137)
    webhook_log("**----------------DASHBOARD HAS STARTED UP----------------**")

def webhook_log(message: str):
    resp = requests.post(WEBHOOK_URL,
                         json={"username": "Web Logs", "content": message})
    print(resp.text)
