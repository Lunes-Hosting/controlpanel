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
from managers.maintenance import sync_users_script
from managers.credit_manager import use_credits, check_to_unsuspend, delete_suspended_users_servers

from Routes.AuthenticationHandler import *
from Routes.Servers import *
from Routes.Store import *
from Routes.admin import admin
from Routes.Tickets import *
from flask_session import Session
from multiprocessing import Process
from discord_bot.bot import bot, run_bot
import asyncio
import importlib
import sys
import datetime
from managers.logging import webhook_log
from cacheext import cache
from threading import Thread
import datetime

if ENABLE_BOT and not DEBUG_FRONTEND_MODE:
    from discord_bot.bot import bot, run_bot

    extensions = [
        'discord_bot.cogs.statistics',
        'discord_bot.cogs.users',
        'discord_bot.cogs.funstuff',
        'discord_bot.cogs.blackjack',
        'discord_bot.cogs.coinflip',
    ]

# Initialize Flask app and extensions
app = Flask(__name__)
app.config.update(
    MAX_CONTENT_LENGTH=10 * 1024 * 1024,  # 10 MB
    SESSION_PERMANENT=True,
    SESSION_TYPE="filesystem",
    PERMANENT_SESSION_LIFETIME=datetime.timedelta(days=31),  # Set session timeout to 7 days
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

# Configure URL scheme for background thread URL generation
if not app.config.get('PREFERRED_URL_SCHEME'):
    app.config['PREFERRED_URL_SCHEME'] = 'https'

# Initialize extensions
cache.init_app(app)
Session(app)
mail = Mail(app)
scheduler = APScheduler()
scheduler.init_app(app)

# Add context processor to make Discord invites available to all templates
@app.context_processor
def inject_discord_invites():
    """
    Make Discord invite link available to all templates.
    """
    return {
        'DISCORD_INVITE': DISCORD_INVITE
    }

def rate_limit_key():
    """Generate a unique key for rate limiting based on user's session."""
    return session.get('random_id')

if not DEBUG_FRONTEND_MODE:
    # Configure rate limiting
    limiter = Limiter(rate_limit_key, app=app, default_limits=["200 per day", "5000 per hour"])


    # Apply rate limits to blueprints
    for blueprint, limit in [
        (user, "20 per hour"),
        (servers, "15 per hour"),
        (tickets, "60 per hour"),
        (store, "10 per hour")
    ]:
        limiter.limit(limit, key_func=rate_limit_key)(blueprint)
        app.register_blueprint(blueprint, 
                            url_prefix=f"/{blueprint.name}" if blueprint.name != "user" else None)

else:
    for blueprint, limit in [
        (user, "20 per hour"),
        (servers, "15 per hour"),
        (tickets, "15 per hour"),
        (store, "10 per hour")
    ]:
        app.register_blueprint(blueprint, 
                            url_prefix=f"/{blueprint.name}" if blueprint.name != "user" else None)
# Register admin blueprint separately (no rate limit)
app.register_blueprint(admin, url_prefix="/admin")


if not DEBUG_FRONTEND_MODE:
# if False:
    @scheduler.task('interval', id='credit_usage', seconds=3600, misfire_grace_time=900)
    def process_credits():
        """Process hourly credit usage for all servers."""
        with app.app_context():
            print("Processing credits...")
            use_credits()
            print("Credit processing complete")

    @scheduler.task('interval', id='server_unsuspend', seconds=60, misfire_grace_time=900)
    def check_suspensions():
        """Check for servers that can be unsuspended."""
        with app.app_context():
            print("Checking suspensions...")
            check_to_unsuspend()
            print("Suspension check complete")
            
    @scheduler.task('interval', id='delete_suspended_servers', seconds=120, misfire_grace_time=900)
    def delete_suspended_servers():
        """Delete servers of suspended users."""
        with app.app_context():
            print("Checking for servers of suspended users...")
            delete_suspended_users_servers()
            print("Suspended users servers check complete")

    @scheduler.task('interval', id='delete_inactive_free_servers', seconds=3600, misfire_grace_time=900)
    def delete_inactive_free_servers_task():
        """Delete free tier servers of users who haven't logged in for 15+ days."""
        with app.app_context():
            print("Checking for inactive free tier servers...")
            from managers.maintenance import delete_inactive_free_servers
            delete_inactive_free_servers()
            print("Inactive free tier servers check complete")

    # Schedule the first run of delete_inactive_free_servers to happen 60 seconds after startup
    @scheduler.task('date', id='initial_delete_inactive_free_servers', run_date=datetime.datetime.now() + datetime.timedelta(seconds=60))
    def initial_delete_inactive_free_servers_task():
        """Initial run of delete_inactive_free_servers shortly after startup."""
        with app.app_context():
            print("Running initial check for inactive free tier servers...")
            from managers.maintenance import delete_inactive_free_servers
            delete_inactive_free_servers()
            print("Initial inactive free tier servers check complete")

    @scheduler.task('interval', id='sync_users', seconds=60, misfire_grace_time=900)
    def sync_user_data():
        """Synchronize user data with Pterodactyl panel."""
        print("Syncing users...")
        sync_users_script()
        pterocache.update_all()
        print("User sync complete")


    scheduler.start()

@app.route('/')
@login_required
def index():
    """Main route - redirects to login if not authenticated."""

if not DEBUG_FRONTEND_MODE:
    # Load bot extensions
    extensions = ['discord_bot.cogs.statistics', 'discord_bot.cogs.users', 'discord_bot.cogs.linking', 'discord_bot.cogs.blackjack', 'discord_bot.cogs.coinflip']

    for extension in extensions:
        print(f'Loading {extension}')
        if extension == 'discord_bot.cogs.users':
            # Special handling for users cog to pass Flask app
            module = importlib.import_module(extension)
            module.setup(bot, app)
        else:
            bot.load_extension(extension)

    def start_bot_loop():
        asyncio.run(run_bot())

if __name__ == '__main__':
    # Create separate processes for Flask and the Discord bot
    webhook_log("**----------------DASHBOARD HAS STARTED UP----------------**")
    
    if ENABLE_BOT and not DEBUG_FRONTEND_MODE:
        bot_thread = Thread(target=start_bot_loop, daemon=True)
        bot_thread.start()
    app.run(debug=DEBUG_FRONTEND_MODE, host="0.0.0.0", port=3040, threaded=True)
