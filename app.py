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

from flask import Flask, request, session, redirect, url_for, flash
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


@app.before_request
def check_session_ip():
    """
    Catch alt accounts live while browsing any page on the dashboard.
    If a logged-in user changes IP (e.g. VPN turns off to reveal home IP),
    verify their IP against historical IPs of other accounts.
    """
    if request.endpoint == 'static' or request.path.startswith('/static/'):
        return None
    if 'email' not in session:
        return None
    if request.endpoint in ('user.login_user', 'user.logout_user'):
        return None

    from managers.alt_detection import get_client_ip, check_login_ip
    from managers.database_manager import DatabaseManager

    client_ip = get_client_ip(request)
    if not client_ip:
        return None

    # Performance optimization: if IP hasn't changed since last check, skip DB query
    if session.get('last_checked_ip') == client_ip:
        return None

    try:
        user_row = DatabaseManager.execute_query(
            "SELECT id, name, role, created_at, suspended FROM users WHERE email = %s",
            (session['email'],)
        )
        if not user_row:
            return None

        user_id, name, role, created_at, suspended = user_row[0], user_row[1], user_row[2], user_row[3], user_row[4]
        if suspended:
            session.clear()
            flash("Your account has been suspended for breaking our TOS. If you believe this is a mistake, please contact support on Discord.")
            return redirect(url_for('user.login_user'))

        ip_check = check_login_ip(
            user_id=int(user_id),
            email=session['email'],
            name=name,
            role=role,
            created_at=created_at,
            ip=client_ip,
            action="live_session",
        )
        if not ip_check.get("allowed", True):
            session.clear()
            flash("Your account has been suspended for breaking our TOS. If you believe this is a mistake, please contact support on Discord.")
            return redirect(url_for('user.login_user'))

        session['last_checked_ip'] = client_ip
    except Exception as exc:
        print(f"Error checking live session IP: {exc}")

    return None


def rate_limit_key():
    """Generate a rate limit key from client IP + session identifier.
    Using both means:
    - Session cycling (clearing cookies) doesn't fully reset the limit,
      because the IP component of the key stays the same.
    - Different users on the same IP (NAT) are still somewhat separated
      by their session component.
    """
    ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    ip = ip.split(',')[0].strip() if ip else request.remote_addr
    
    session_id = session.get('random_id', 'anonymous')
    return f"{ip}:{session_id}"

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
    extensions = ['discord_bot.cogs.statistics', 'discord_bot.cogs.users', 'discord_bot.cogs.linking', 'discord_bot.cogs.blackjack', 'discord_bot.cogs.coinflip', 'discord_bot.cogs.bump_rewards']

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
