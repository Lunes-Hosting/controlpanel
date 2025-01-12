"""
Main application file for the Pterodactyl Control Panel.
This file initializes the Flask application and sets up all core configurations.

Session Variables:
    - email: str - User's email address
    - random_id: str - Random identifier for rate limiting
    - pterodactyl_id: tuple[int] - User's Pterodactyl panel ID
    - verified: bool - Whether user's email is verified
    - role: str - User's role (admin/client/user)

Configuration:
    - MAX_CONTENT_LENGTH: 10MB file upload limit
    - SESSION_TYPE: filesystem-based session storage
    - MAIL_* configs: Email server settings
    - RECAPTCHA_* configs: Google ReCAPTCHA settings
"""

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
from discord_bot.bot import bot, run_bot
import asyncio

from scripts import *
from cacheext import cache
from threading import Thread
from discord_bot.bot import bot, run_bot

extensions = [
    'discord_bot.cogs.statistics',
    'discord_bot.cogs.users',
    'discord_bot.cogs.funstuff',
]
# Initialize Flask app and extensions
app = Flask(__name__, "/static")
app.config.update(
    MAX_CONTENT_LENGTH=10 * 1024 * 1024,  # 10 MB
    SESSION_PERMANENT=True,
    SESSION_TYPE="filesystem",
    SECRET_KEY=SECRET_KEY,
    SCHEDULER_API_ENABLED=True,
    MAIL_SERVER=MAIL_SERVER,
    MAIL_PORT=MAIL_PORT,
    MAIL_USE_TLS=True,
    MAIL_USERNAME=MAIL_USERNAME,
    MAIL_PASSWORD=MAIL_PASSWORD,
    MAIL_DEFAULT_SENDER=MAIL_DEFAULT_SENDER,
    RECAPTCHA_PUBLIC_KEY=RECAPTCHA_SITE_KEY,
    RECAPTCHA_PRIVATE_KEY=RECAPTCHA_SECRET_KEY
)

# Initialize extensions
cache.init_app(app)
Session(app)
mail = Mail(app)
scheduler = APScheduler()
scheduler.init_app(app)

def rate_limit_key():
    """Generate a unique key for rate limiting based on user's session."""
    return session.get('random_id')

# Configure rate limiting
limiter = Limiter(rate_limit_key, app=app, default_limits=["200 per day", "5000 per hour"])

# Apply rate limits to blueprints
for blueprint, limit in [
    (user, "20 per hour"),
    (servers, "15 per hour"),
    (tickets, "15 per hour"),
    (store, "10 per hour")
]:
    limiter.limit(limit, key_func=rate_limit_key)(blueprint)
    app.register_blueprint(blueprint, 
                         url_prefix=f"/{blueprint.name}" if blueprint.name != "user" else None)

# Register admin blueprint separately (no rate limit)
app.register_blueprint(admin, url_prefix="/admin")

@scheduler.task('interval', id='credit_usage', seconds=3600, misfire_grace_time=900)
def process_credits():
    """Process hourly credit usage for all servers."""
    print("Processing credits...")
    use_credits()
    print("Credit processing complete")

@scheduler.task('interval', id='server_unsuspend', seconds=180, misfire_grace_time=900)
def check_suspensions():
    """Check for servers that can be unsuspended."""
    print("Checking suspensions...")
    check_to_unsuspend()
    print("Suspension check complete")

@scheduler.task('interval', id='sync_users', seconds=500, misfire_grace_time=900)
def sync_user_data():
    """Synchronize user data with Pterodactyl panel."""
    print("Syncing users...")
    sync_users_script()
    pterocache.update_all()
    print("User sync complete")



scheduler.start()

@app.route('/')
def index():
    """Main route - redirects to login if not authenticated."""
    if 'email' not in session:
        return redirect(url_for("user.login_user"))
    after_request(session=session, request=request.environ, require_login=True)


def webhook_log(message: str):
    """
    Send a message to the webhook log.
    """
    resp = requests.post(WEBHOOK_URL,
                         json={"username": "Web Logs", "content": message})
    print(resp.text)


for extension in extensions:
    print(f'Loading {extension}')
    bot.load_extension(extension)
def start_bot_loop():
     asyncio.run(run_bot())

if __name__ == '__main__':
    # Create separate processes for Flask and the Discord bot
    webhook_log("**----------------DASHBOARD HAS STARTED UP----------------**")
    bot_thread = Thread(target=start_bot_loop, daemon=True)
    bot_thread.start()
    app.run(debug=False, host="0.0.0.0", port=1137)
