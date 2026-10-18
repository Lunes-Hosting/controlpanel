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

from flask import Flask, request
from flask_apscheduler import APScheduler
from flask_limiter import Limiter
from rate_limits import limiter
from request_security import client_ip
import config as panel_config
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
from scripts.add_performance_indexes import ensure_performance_indexes
from web_performance import install_performance

# Optional performance upgrades run automatically and never block startup on a
# missing ALTER privilege. All settings continue to have backwards-compatible defaults.
ensure_performance_indexes()

if ENABLE_BOT and not DEBUG_FRONTEND_MODE:
    from discord_bot.bot import bot, run_bot

    extensions = [
        'discord_bot.cogs.statistics',
        'discord_bot.cogs.users',
        'discord_bot.cogs.funstuff',
        'discord_bot.cogs.bump_rewards',
    ]

# Initialize Flask app and extensions
app = Flask(__name__)
app.config.update(
    MAX_CONTENT_LENGTH=10 * 1024 * 1024,  # 10 MB
    SESSION_PERMANENT=True,
    SESSION_TYPE="filesystem",
    PERMANENT_SESSION_LIFETIME=datetime.timedelta(days=31),
    SECRET_KEY=SECRET_KEY,
    RATELIMIT_STORAGE_URI=getattr(panel_config, 'RATELIMIT_STORAGE_URI', 'memory://'),
    RATELIMIT_HEADERS_ENABLED=True,
    RATELIMIT_ENABLED=not DEBUG_FRONTEND_MODE,
    RATELIMIT_DEFAULT='5000 per hour',
    SCHEDULER_API_ENABLED=False,
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_SAMESITE='Lax',
    MAIL_SERVER=MAIL_SERVER,
    MAIL_PORT=MAIL_PORT,
    MAIL_USE_TLS=True,
    MAIL_USERNAME=MAIL_USERNAME,
    MAIL_PASSWORD=MAIL_PASSWORD,
    MAIL_DEFAULT_SENDER=MAIL_DEFAULT_SENDER,
    RECAPTCHA_PUBLIC_KEY=RECAPTCHA_SITE_KEY,
    RECAPTCHA_PRIVATE_KEY=RECAPTCHA_SECRET_KEY
)
app.config["DEBUG_FRONTEND_MODE"] = DEBUG_FRONTEND_MODE

# Configure URL scheme for background thread URL generation
if not app.config.get('PREFERRED_URL_SCHEME'):
    app.config['PREFERRED_URL_SCHEME'] = 'https'

# Initialize extensions
cache.init_app(app)
Session(app)
install_performance(app)
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

@app.context_processor
def inject_user_roles():
    """
    Make user role checks available to all templates.
    """
    from managers.user_manager import is_admin, is_support
    
    if 'email' in session:
        return {
            'is_admin': is_admin(session['email']),
            'is_support': is_support(session['email'])
        }
    return {
        'is_admin': False,
        'is_support': False
    }


def rate_limit_key():
    """Keep existing proxy-address behavior; login has separate peer-based limits."""
    return client_ip()

limiter.init_app(app)

if not DEBUG_FRONTEND_MODE:
    # Apply rate limits to blueprints
    for blueprint, limit in [
        (user, "60 per hour"),
        (servers, "30 per hour"),
        (tickets, "60 per hour"),
        (store, "60 per hour")
    ]:
        limiter.limit(limit, key_func=rate_limit_key, methods=['POST'],
                      exempt_when=lambda: request.endpoint == 'store.stripe_webhook')(blueprint)
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
    @scheduler.task('interval', id='checkout_reconciliation', seconds=60, misfire_grace_time=900)
    def reconcile_checkout_payments():
        from managers.checkout_manager import reconcile_pending_checkouts
        try:
            reconcile_pending_checkouts()
        except Exception as exc:
            print(f"Checkout reconciliation unavailable: {type(exc).__name__}")

    @scheduler.task('interval', id='suspension_emails', seconds=60, misfire_grace_time=900)
    def process_suspension_emails():
        from managers.suspension_notifications import deliver_pending_suspension_emails
        try:
            deliver_pending_suspension_emails()
        except Exception as exc:
            print(f"Suspension email retry error: {type(exc).__name__}")

    @scheduler.task('interval', id='credit_usage', seconds=3600, misfire_grace_time=900)
    def process_credits():
        """Process hourly credit usage for all servers."""
        with app.app_context():
            try:
                print("Processing credits...")
                use_credits()
                print("Credit processing complete")
            except Exception as e:
                print(f"Credit processing error: {e}")

    @scheduler.task('interval', id='server_unsuspend', seconds=60, misfire_grace_time=900)
    def check_suspensions():
        """Check for servers that can be unsuspended."""
        with app.app_context():
            try:
                print("Checking suspensions...")
                check_to_unsuspend()
                print("Suspension check complete")
            except Exception as e:
                print(f"Suspension check error: {e}")
            
    @scheduler.task('interval', id='delete_suspended_servers', seconds=120, misfire_grace_time=900)
    def delete_suspended_servers():
        """Delete servers of suspended users."""
        with app.app_context():
            try:
                print("Checking for servers of suspended users...")
                delete_suspended_users_servers()
                print("Suspended users servers check complete")
            except Exception as e:
                print(f"Suspended users servers check error: {e}")

    @scheduler.task('interval', id='delete_inactive_free_servers', seconds=3600, misfire_grace_time=900)
    def delete_inactive_free_servers_task():
        """Delete free tier servers of users who haven't logged in for 15+ days."""
        with app.app_context():
            print("Checking for inactive free tier servers...")
            from managers.maintenance import delete_inactive_free_servers
            delete_inactive_free_servers()
            print("Inactive free tier servers check complete")

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
    extensions = ['discord_bot.cogs.statistics', 'discord_bot.cogs.users', 'discord_bot.cogs.linking', 'discord_bot.cogs.bump_rewards']

    for extension in extensions:
        print(f'Loading {extension}')
        if extension == 'discord_bot.cogs.users':
            module = importlib.import_module(extension)
            module.setup(bot, app)
        else:
            bot.load_extension(extension)

    def start_bot_loop():
        asyncio.run(run_bot())

if __name__ == '__main__':
    webhook_log("**----------------DASHBOARD HAS STARTED UP----------------**")
    
    if ENABLE_BOT and not DEBUG_FRONTEND_MODE:
        bot_thread = Thread(target=start_bot_loop, daemon=True)
        bot_thread.start()
    app.run(debug=DEBUG_FRONTEND_MODE, host="0.0.0.0", port=3040, threaded=True)
