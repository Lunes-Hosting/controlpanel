"""
Authentication Handler Module
===========================

This module handles all authentication-related routes and functionality for the control panel.
It manages user registration, login, password resets, email verification, and account deletion.

Templates Used:
-------------
- login.html: User login form
- register.html: New user registration form
- reset_password.html: Password reset request form
- reset_password_confirm.html: New password form after reset
- account.html: User dashboard/profile page
- delete_account.html: Account deletion confirmation

Database Tables Used:
------------------
- users: Stores user information and credentials
- tickets: For user support tickets
- ticket_comments: For ticket messages
- servers: Links to user's Pterodactyl servers

External Services:
---------------
- Pterodactyl Panel API: For server management
- ReCAPTCHA: For form protection
- SMTP: For sending verification emails

Session Variables:
---------------
- email: User's email address
- pterodactyl_id: User's Pterodactyl panel ID
- verified: Email verification status

Cache Keys:
---------
- [email]: Stores email verification tokens
- reset_[token]: Stores password reset tokens
"""

import asyncio
from hashlib import sha256
from flask import Blueprint, request, render_template, session, flash, current_app, redirect, url_for, jsonify
import sys
import threading
import json
import datetime
import requests
import bcrypt
from runtime.auth_http import auth_requests, verify_turnstile, blocked_email_list

from flask_limiter import Limiter
from runtime.rate_limits import limiter
from security import safe_requests
from urllib.parse import urlparse, urljoin

sys.path.append("..")
from pterocache import *
from managers.authentication import login_required, login, register, requires_manual_server_approval
from managers.signup_signals import (collect_signals, device_token, find_matches,
                                     record_signup, set_device_cookie)
from managers.signup_location import connection_details
from managers.alt_settings import alt_detection_enabled
from managers.account_sessions import establish_session
from managers.suspension_manager import get_user_suspension
from runtime.request_security import client_ip, peer_ip, login_limit_key, login_credentials
from runtime.rate_limits import limiter
from managers.dashboard_inventory import dashboard_inventory
from managers.email_manager import send_email, generate_verification_token, send_verification_email, generate_reset_token, send_reset_email
from managers.user_manager import account_get_information, get_id, get_name, instantly_delete_user, get_ptero_id
from managers.server_manager import improve_list_servers, display_servers, delete_server as manager_delete_server
from managers.credit_manager import convert_to_product
from managers.utils import HEADERS
from managers.logging import webhook_log
from products import products

from cacheext import cache
from managers.database_manager import DatabaseManager
from config import PTERODACTYL_URL, RECAPTCHA_SECRET_KEY, RECAPTCHA_SITE_KEY, SUBSCRIPTION_CREDIT_PRICES

# Create a blueprint for the user routes
user = Blueprint('user', __name__)
TOKEN_EXPIRATION_TIME = 1800  # 30 minutes

pterocache = PteroCache()


@user.context_processor
def _alt_detection_context():
    return {"alt_detection_enabled": alt_detection_enabled()}


@user.after_request
def _signup_cookie(response):
    if request.endpoint == 'user.register_user':
        return set_device_cookie(response)
    return response

def _get_client_ip(req: 'flask.Request') -> str:
    """Resolve client addresses using the deployment's existing proxy headers."""
    return client_ip(req)

# Making sure nobody tries to bounce my favorite person to some sketchy link ~
def is_safe_url(target: str) -> bool:
    """Ensure target URL is a safe internal redirect without protocol-relative tricks."""
    if not target or not isinstance(target, str):
        return False
    target = target.strip()
    if not target or target.lower() in ('none', 'null', 'false'):
        return False
    if '\\' in target:
        return False
    # Must start with exactly one leading slash (prevent //evil.com or ///evil.com)
    if not target.startswith('/') or target.startswith('//'):
        return False
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in ('http', 'https') and ref_url.netloc == test_url.netloc

@user.route('/login', methods=['POST', 'GET'])
@limiter.limit('20 per minute; 120 per hour', key_func=login_limit_key, methods=['POST'])
@limiter.limit('300 per minute; 3000 per hour', key_func=peer_ip, methods=['POST'])
def login_user():
    """
    Handle user login via form submission.
    
    Methods:
        GET: Display login form
        POST: Process login attempt
        
    Form Data:
        - email: User's email
        - password: User's password
        - g-recaptcha-response: ReCAPTCHA token
        
    Templates:
        - login.html: Shows login form with ReCAPTCHA
        
    Session:
        Sets: email, pterodactyl_id
        
    Returns:
        GET: template: login.html with ReCAPTCHA key
        POST: redirect: To dashboard on success or login page with error
        
    Related Functions:
        - get_ptero_id(): Gets Pterodactyl panel ID
    """
    if request.method == "POST":
        credentials = login_credentials(request)
        if credentials is None:
            return "Invalid login request. Enter a valid email and password.", 400
        email, password = credentials
        recaptcha_response = request.form.get('g-recaptcha-response')
        if not DEBUG_FRONTEND_MODE and not recaptcha_response:
            return "Complete the login verification and try again.", 400

        if DEBUG_FRONTEND_MODE is True:
            # Skip reCAPTCHA verification in debug mode
            pass
        else:
            try:
                verified_captcha = verify_turnstile(recaptcha_response, RECAPTCHA_SECRET_KEY)
            except (requests.RequestException, ValueError):
                return "Login verification temporarily unavailable. Please try again.", 503
            if not verified_captcha:
                flash("Failed captcha please try again")
                return render_template("login.html", RECAPTCHA_PUBLIC_KEY=RECAPTCHA_SITE_KEY)

        ip = _get_client_ip(request)
        try:
            response = login(email, password, ip)
            if response in ("suspended", "alt_suspended"):
                account = get_id(email)
                reason = get_user_suspension(account[0]) if account else "A reason was not recorded."
                flash(f"Your account is suspended. Reason: {reason} If you believe this is a mistake, please contact support.")
                return redirect(url_for('user.login_user'))
            if response is None:
                flash("Incorrect information. Please ensure you have an account.")
                return redirect(url_for('user.login_user'))

            if response:
                raw_next = request.form.get('next') or request.args.get('next') or session.get("next")
                establish_session(email, response)
                # Keeping your front door locked tight against open redirects, cutie.
                if raw_next and is_safe_url(raw_next):
                    next_page = raw_next
                else:
                    next_page = url_for("user.index")
                return redirect(next_page)
        except Exception as e:
            print(e)
            flash("An error occurred during login. Please try again.")
    if 'email' in session:
        # Check for next parameter in form data, URL args, then fall back to session next
        raw_next = request.form.get('next') or request.args.get('next') or session.pop("next", None)
        if raw_next and is_safe_url(raw_next):
            next_page = raw_next
        else:
            next_page = url_for("user.index")
        return redirect(next_page)
    return render_template("login.html", RECAPTCHA_PUBLIC_KEY=RECAPTCHA_SITE_KEY)


@user.route('/')
@login_required
def index():
    """
    Display user account dashboard.
    
    Session Requirements:
        - email: User must be logged in
        
    Templates:
        - account.html: Shows user profile and stats
        
    Database Queries:
        - Get user credits
        - Get server count
        - Calculate monthly usage
        
    Returns:
        template: account.html with:
            - credits: User's current credits
            - server_count: Number of servers owned
            - username: User's name
            - email: User's email
            - monthly_usage: Total monthly credit usage
            
    Related Functions:
        - get_credits(): Gets user's credit balance
        - improve_list_servers(): Gets user's server list
    """
    current_credits, ptero_id, username, verified, suspended = account_get_information(session["email"])

    if session.get('suspended') != suspended:
        session['suspended'] = suspended
    state = dashboard_inventory(session['account_id'], ptero_id)
    inventory = dashboard_server_page(state, request.args.get('server_page', 1, type=int))

    #username = DatabaseManager.execute_query(
    #    "SELECT name FROM users WHERE email = %s", 
    #    (session['email'],)
    #)

    products_local = tuple(products)
    fixed_list: list[dict] = []
    for product in products_local:
        x = product.get("price_link", None)
        if x is not None:
            fixed_list.append({key: product[key] for key in ('price', 'price_link', 'nickname') if key in product})
            #products_local.remove(product)

    subscription_credit_options = sorted(
        ((price_link, int(credits)) for price_link, credits in (SUBSCRIPTION_CREDIT_PRICES or {}).items()),
        key=lambda x: x[1],
    )

    from managers.webhook_manager import get_user_webhook
    account_id = session.get('account_id')
    user_webhook = get_user_webhook(account_id) if account_id else None

    return render_template(
        "account.html", 
        credits=int(current_credits), 
        username=username, 
        hash=sha256(session['email'].encode('utf-8')).hexdigest(),
        email=session['email'], 
        products=fixed_list,
        subscription_credit_options=subscription_credit_options,
        verified=verified,
        suspended=suspended,
        user_webhook=user_webhook,
        **inventory,
    )


def dashboard_server_page(state, page=1):
    data = state['data']
    count = data['server_count'] if data is not None else None
    total_pages = max(1, ((count or 0) + 19) // 20)
    page = min(total_pages, max(1, page))
    start = (page - 1) * 20
    return dict(servers=(data['servers'][start:start + 20] if data else []),
                server_count=count, monthly_usage=data['monthly_usage'] if data else None,
                servers_ready=data is not None, inventory_state=state,
                current_server_page=page, total_server_pages=total_pages)


@user.route('/dashboard/servers')
@login_required
def dashboard_servers_data():
    if request.headers.get('X-Dashboard-Account') != str(session['account_id']):
        return jsonify(error='Account changed. Reload the dashboard.'), 409
    current_credits, ptero_id, _, verified, suspended = account_get_information(session['email'])
    state = dashboard_inventory(session['account_id'], ptero_id)
    context = dashboard_server_page(state, request.args.get('server_page', 1, type=int))
    response = jsonify(
        html=render_template('partials/dashboard_servers.html', verified=verified, **context),
        ready=context['servers_ready'], server_count=context['server_count'],
        monthly_usage=context['monthly_usage'], credit_width=min(100, (context['server_count'] or 0) * 12),
        refreshing=state['refreshing'], error=state['error'], credits=int(current_credits),
    )
    response.headers['Cache-Control'] = 'private, no-store'
    return response, 200 if context['servers_ready'] else (503 if state['error'] else 202)


# Route to request a password reset (via email)
@user.route('/reset_password', methods=['GET', 'POST'])
def reset_password():
    """
    Handle password reset request.
    
    Methods:
        GET: Display reset request form
        POST: Process reset request
        
    Form Data:
        - email: User's email address
        - g-recaptcha-response: ReCAPTCHA token
        
    Templates:
        - reset_password.html: Password reset request form
        
    Process:
        1. Validate email exists in database
        2. Generate secure reset token
        3. Cache token with email for 30 minutes
        4. Send reset email with token link
        
    Returns:
        GET: template: reset_password.html
        POST: redirect: To login page with confirmation
        
    Related Functions:
        - send_reset_email(): Sends password reset email
        - generate_reset_token(): Creates secure token
    """
    if request.method == 'POST':
        recaptcha_response = request.form.get('g-recaptcha-response')

        try:
            verified_captcha = verify_turnstile(recaptcha_response, RECAPTCHA_SECRET_KEY)
        except (requests.RequestException, ValueError):
            return "Verification temporarily unavailable. Please try again.", 503
        if not verified_captcha:
            flash("Failed captcha please try again")
            return render_template("reset_password.html", RECAPTCHA_PUBLIC_KEY=RECAPTCHA_SITE_KEY)

        email = request.form.get('email')

        # Generate a reset token
        reset_token = generate_reset_token()

        # Store the reset token in the cache with the email as the key
        cache.set(email, reset_token, timeout=TOKEN_EXPIRATION_TIME)
        # Compose and send the reset email
        email_thread = threading.Thread(target=send_reset_email, args=(str(email), reset_token, current_app._get_current_object()), daemon=True)
        email_thread.start()
        flash('An email with instructions to reset your password has been sent.')
        return redirect(url_for('user.login_user'))

    return render_template('reset_password.html', RECAPTCHA_PUBLIC_KEY=RECAPTCHA_SITE_KEY)


@user.route('/reset_password/<token>', methods=['GET', 'POST'])
def reset_password_confirm(token):
    """
    Handle password reset confirmation.
    
    Methods:
        GET: Display password reset form
        POST: Process new password
        
    Args:
        token: Reset token from email
        
    Form Data:
        - email: User's email
        - password: New password
        - confirm_password: Password confirmation
        - token: Reset token
        
    Templates:
        - reset_password_confirm.html: New password form
        
    Process:
        1. Validate token matches cached token
        2. Update password in local database
        3. Update password in Pterodactyl panel
        4. Clear reset token from cache
        
    Returns:
        GET: template: reset_password_confirm.html
        POST: redirect: To login page on success
        
    Related Functions:
        - update_password(): Updates password in database
        - update_panel_password(): Updates Pterodactyl password
    """
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')

        token_cache = cache.get(email)

        if token_cache and token and token_cache == token:
            if password == confirm_password:
                salt = bcrypt.gensalt(rounds=14)
                password_hash = bcrypt.hashpw(password.encode('utf-8'), salt)

                ptero_id = get_ptero_id(email)
                if ptero_id is None:
                    flash('Error: User not found in Pterodactyl panel')
                    return redirect(url_for('user.login_user'))
                
                # Update password in panel
                try:
                    info_response = auth_requests.get(f"{PTERODACTYL_URL}api/application/users/{ptero_id[0]}", headers=HEADERS, timeout=(3, 10))
                    info_response.raise_for_status()
                    info = info_response.json()['attributes']
                    body = {
                        "username": info['username'],
                        "email": info['email'],
                        "first_name": info['first_name'],
                        "last_name": info['last_name'],
                        "password": password
                    }

                    panel_response = auth_requests.patch(f"{PTERODACTYL_URL}api/application/users/{ptero_id[0]}", headers=HEADERS, json=body, timeout=(3, 10))
                    panel_response.raise_for_status()

                except (requests.RequestException, ValueError, KeyError):
                    flash('The hosting panel is temporarily unavailable. Your reset link is still valid; please try again.')
                    return render_template('reset_password_confirm.html', token=token), 503

                DatabaseManager.execute_query(
                    "UPDATE users SET password = %s WHERE email = %s",
                    (password_hash.decode(), email)
                )

                # Wiping the token by email the second it's used so nobody gets to replay your keys.
                cache.delete(email)

                flash('Your password has been successfully reset.')
                return redirect(url_for('user.login_user'))
            else:
                flash('Passwords do not match.')
        else:
            flash('Invalid or expired reset token.')

    return render_template('reset_password_confirm.html', token=token)


@user.route('/register', methods=['POST', 'GET'])
def register_user():
    """
    Handle user registration.
    
    Methods:
        GET: Display registration form
        POST: Process registration
        
    Form Data:
        - email: User's email
        - username: Desired username
        - password: Desired password
        - confirm_password: Password confirmation
        - g-recaptcha-response: ReCAPTCHA token
        
    Templates:
        - register.html: Registration form with ReCAPTCHA
        
    Process:
        1. Validate form data and ReCAPTCHA
        2. Create user in Pterodactyl panel
        3. Create user in local database
        4. Generate verification token
        5. Send verification email
        
    Returns:
        GET: template: register.html
        POST: redirect: To login page on success
        
    Related Functions:
        - register(): Creates user accounts
        - send_verification_email(): Sends email verification
        - generate_verification_token(): Creates secure token
    """
    if request.method == "GET":
        blocked_email_list.preload()

    if request.method == "POST":
        recaptcha_response = request.form.get('g-recaptcha-response')

        try:
            verified_captcha = verify_turnstile(recaptcha_response, RECAPTCHA_SECRET_KEY)
        except (requests.RequestException, ValueError):
            return "Verification temporarily unavailable. Please try again.", 503
        if not verified_captcha:
            flash("Failed captcha please try again")
            return render_template("register.html", RECAPTCHA_PUBLIC_KEY=RECAPTCHA_SITE_KEY)
        

        data = request.form
        email = (data.get('email') or '').strip().lower()
        password = data.get('password') or ''
        name = (data.get('username') or '').strip()
        ip = _get_client_ip(request)
        if not email or not password or not name:
            flash("Please fill in all registration fields.")
            return render_template("register.html", RECAPTCHA_PUBLIC_KEY=RECAPTCHA_SITE_KEY)

        # Enrollment starts with this release. Existing users are not scanned,
        # changed, or subjected to a new login-time check.
        signals = collect_signals(data)
        try:
            matches = find_matches(signals)
        except Exception as exc:
            # A deployment without CREATE TABLE permission should not stop
            # the existing registration protections from operating.
            webhook_log(f"Signup device lookup unavailable: {exc}", database_log=True)
            matches = {'exact': None, 'profile': None}
        if matches['exact']:
            main_acc = DatabaseManager.execute_query(
                "SELECT id, name, email, ip FROM users WHERE id = %s",
                (matches['exact'],),
            )
            main_email = main_acc[2] if main_acc else "Unknown"
            main_ip = main_acc[3] if main_acc else "Unknown"
            main_name = main_acc[1] if main_acc else "Unknown"
            webhook_log(
                f"Signup blocked (Alt Detected):\n\n"
                f"Blocked Alt Account:\n"
                f"- Full Email: {email}\n"
                f"- Signup IP: {ip}\n"
                f"- Username: {name}\n\n"
                f"Matched Main Account:\n"
                f"- ID: {matches['exact']}\n"
                f"- Username: {main_name}\n"
                f"- Full Email: {main_email}\n"
                f"- Main Known IP: {main_ip}\n"
                f"- Reason: Device token/storage match",
                status=2,
                database_log=True,
            )
            return render_template(
                "register.html", alt_detected=True,
                connection=connection_details(request.headers, ip),
            ), 409

        # Check if 'suspended' key exists in session, if not, initialize it to False
        if 'suspended' not in session:
            session['suspended'] = False

        if alt_detection_enabled() and session['suspended']:
            flash("Failed to register! If this is an error, please contact support. panel@lunes.host")
            webhook_log(f"Failed to register email {email} ip: {ip} due to alt suspended account")
            return render_template("register.html", RECAPTCHA_PUBLIC_KEY=RECAPTCHA_SITE_KEY)

        profile_review = bool(matches['profile'])
        try:
            res = register(email, password, name, ip, needs_review=profile_review)
        except (requests.RequestException, ValueError):
            flash('Account creation could not be confirmed. Please try signing in, or contact support if it did not complete.')
            return render_template("register.html", RECAPTCHA_PUBLIC_KEY=RECAPTCHA_SITE_KEY), 503
        if isinstance(res, str):
            flash(res + " If this is an error, please contact support.")
            return render_template("register.html", RECAPTCHA_PUBLIC_KEY=RECAPTCHA_SITE_KEY)

        user_id = get_id(email)[0]
        try:
            record_signup(user_id, signals)
        except Exception as exc:
            # The account already exists in both panels. Keep registration usable
            # and log the missing signal so staff can fix the database failure.
            webhook_log(f"Could not record signup device for account ID {user_id}: {exc}",
                        database_log=True)

        from managers.alt_detection import record_user_ip
        try:
            record_user_ip(user_id, ip)
        except Exception as exc:
            pass

        verification_token = generate_verification_token()
        cache.set(verification_token, email, timeout=TOKEN_EXPIRATION_TIME)

        email_thread = threading.Thread(
            target=send_verification_email, 
            args=(email, verification_token, current_app._get_current_object()), 
            daemon=True
        )
        email_thread.start()

        if requires_manual_server_approval(email) or profile_review:
            approval_message = (
                "Your free account requires manual approval before creating a server. "
                "Your account has been sent to our staff for review. You can wait for "
                "approval, or purchase credits to become a client and create a server immediately."
            )
            threading.Thread(
                target=send_email,
                args=(
                    email,
                    "Manual approval required for free server creation",
                    approval_message,
                    current_app._get_current_object(),
                ),
                daemon=True,
            ).start()

            # Safely hand this request from Flask's thread to the Discord bot.
            from discord_bot.account_approval import queue_account_review
            if user_id:
                main_account_info = None
                if profile_review and matches.get('profile'):
                    main_acc = DatabaseManager.execute_query(
                        "SELECT id, name, email, ip FROM users WHERE id = %s",
                        (matches['profile'],),
                    )
                    if main_acc:
                        main_account_info = {
                            "id": main_acc[0],
                            "name": main_acc[1],
                            "email": main_acc[2],
                            "ip": main_acc[3],
                        }
                queue_account_review(
                    user_id=user_id,
                    name=name,
                    email=email,
                    reason=(f"Browser profile matches account ID {matches['profile']}"
                            if profile_review else "Non-Gmail email domain"),
                    alt_ip=ip,
                    main_account_info=main_account_info,
                )
            flash(
                'A verification email has been sent. Your free account is also waiting '
                'for manual approval before it can create a server.'
            )
        else:
            flash('A verification email has been sent. Please check your inbox and spam to verify your email.')
        return redirect(url_for('index'))
    if 'email' in session:
        return redirect(url_for("user.index"))
    device_token()
    return render_template("register.html", RECAPTCHA_PUBLIC_KEY=RECAPTCHA_SITE_KEY)


@user.route("/resend_confirmation_email")
@login_required
def resend_confirmation_email():
    """
    Resend email verification link.
    
    Session Requirements:
        - email: User must be logged in
        
    Process:
        1. Generate new verification token
        2. Cache token with email
        3. Send verification email
        
    Returns:
        redirect: To account page with confirmation
        
    Related Functions:
        - generate_verification_token(): Creates new token
        - send_verification_email(): Sends verification email
    """

    verification_token = generate_verification_token()

    cache.set(verification_token, session["email"], timeout=TOKEN_EXPIRATION_TIME )

    email_thread = threading.Thread(
        target=send_verification_email, 
        args=(session['email'], verification_token, current_app._get_current_object()), 
        daemon=True
    )
    email_thread.start()

    flash("Sent a verification email")
    return redirect(url_for('index'))


@user.route('/verify_email/<token>', methods=['GET', 'POST'])
@login_required
def verify_email(token):
    """
    Verify user's email address.
    
    Args:
        token: Email verification token
        
    Process:
        1. Get email from cache using token
        2. Verify token matches and is valid
        3. Update user's verified status
        4. Clear verification token from cache
        5. Log verification in webhook
        
    Returns:
        redirect: To account page with status message
        
    Related Functions:
        - webhook_log(): Logs verification event
    """

    # Require CAPTCHA before verifying email
    if request.method == 'GET':
        return render_template('verify_email.html', RECAPTCHA_PUBLIC_KEY=RECAPTCHA_SITE_KEY, token=token)

    # POST: validate Turnstile/Recaptcha
    recaptcha_response = request.form.get('g-recaptcha-response')
    data = {
        'secret': RECAPTCHA_SECRET_KEY,
        'response': recaptcha_response
    }
    response = requests.post('https://challenges.cloudflare.com/turnstile/v0/siteverify', data=data, timeout=60)
    result = response.json()
    if not result.get('success'):
        flash('Failed captcha please try again')
        return render_template('verify_email.html', RECAPTCHA_PUBLIC_KEY=RECAPTCHA_SITE_KEY, token=token)

    email = cache.get(token)
    if email:
        DatabaseManager.execute_query(
            "UPDATE users SET email_verified_at = %s WHERE email = %s",
            (datetime.datetime.now(), email)
        )
        cache.delete(token)
        flash('Your email has been successfully verified.')
    else:
        flash('Invalid or expired verification token.')
    return redirect(url_for('index'))

@user.route('/logout', methods=['GET'])
@login_required
def logout():
    """
    Log out current user.
    
    Process:
        1. Clear all session data
        2. Redirect to login page
        
    Returns:
        redirect: To login page
        
    Session:
        Clears: All session data
    """
    temp_suspended = session.get('suspended', False)
    session.clear()
    session['suspended'] = temp_suspended
    return redirect(url_for("index"))


@user.route('/account/delete', methods=['GET', 'POST'])
@login_required
def delete_account():
    """
    Handle user account self-deletion.
    
    Methods:
        GET: Display deletion confirmation page
        POST: Process account deletion
        
    Session Requirements:
        - email: User must be logged in
        
    Templates:
        - delete_account.html: Deletion confirmation page
        
    Process:
        1. Verify user is logged in
        2. Get user's Pterodactyl ID
        3. Delete all user's servers
        4. Delete user from Pterodactyl panel
        5. Delete user's tickets and comments
        6. Delete user from database
        7. Clear session data
        8. Log deletion in webhook
        
    Returns:
        GET: template: delete_account.html
        POST: redirect: To login page with confirmation
        
    Related Functions:
        - delete_server(): Removes servers
        - delete_user(): Removes from Pterodactyl
        - webhook_log(): Logs account deletion
    """
        
    if request.method == "GET":
        return render_template("delete_account.html")
        
    try:
        db = DatabaseManager()
        # Get user info
        user_info = db.execute_query(
            "SELECT email, pterodactyl_id FROM users WHERE email = %s", 
            (session['email'],)
        )
        if not user_info:
            flash("User not found")
            return redirect(url_for('index'))
            
        email, ptero_id = user_info
        webhook_log(f"User `{email}` deleted their account", database_log=True)
        temp_suspended = session.get('suspended', False)
        session.clear()
        session['suspended'] = temp_suspended
        
        # Get and delete all user's servers
        print(email, ptero_id)
        servers = improve_list_servers(ptero_id)
        print(servers)
        
        # Process servers using the known structure
        try:
            servers_data = servers['attributes']['relationships']['servers']['data']
            for server in servers_data:
                server_id = server['attributes']['id']
                print(f"Deleting server {server_id}")
                result = manager_delete_server(server_id)
                print(f"Server {server_id} deletion result: {result}")
        except Exception as e:
            print(f"Error processing servers: {str(e)}")
            flash(f"Error deleting servers: {str(e)}")
        
        send_email(email, "Account Deletion", "Your account has been flagged for deletion. If you do not log back in within 15 days, your account will be permanently deleted.", current_app._get_current_object())
        webhook_log(f"USER Account of {email} is Flagged for Deletion!", 0, database_log=True)
        db.execute_query("INSERT INTO pending_deletions (email, deletion_requested_time) VALUES (%s, %s)", (email, datetime.datetime.now()))
            
        flash("Your account has been flagged for deletion. If you do not log back in within 15 days, your account will be permanently deleted.")

    except Exception as e:
        print(f"Error deleting account: {e}")
        webhook_log(f"Couldn't delete account of {email}. Error -> {e}", 2)
        flash("Error deleting account. Please contact support.")
        return redirect(url_for('index'))
        
    return redirect(url_for('user.login_user'))


@user.route('/webhook/save', methods=['POST'])
@login_required
@limiter.limit("10 per minute; 30 per hour")
def save_webhook():
    account_id = session.get('account_id')
    if not account_id:
        flash("Invalid session. Please log in again.")
        return redirect(url_for('user.index'))

    url = request.form.get('url', '').strip()

    from managers.webhook_manager import save_user_webhook
    success, message = save_user_webhook(account_id, url)

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json:
        return jsonify(success=success, message=message), (200 if success else 400)

    flash(message)
    return redirect(url_for('user.index') + "#WebhooksArea")


@user.route('/webhook/test', methods=['POST'])
@login_required
@limiter.limit("5 per minute; 20 per hour")
def test_webhook():
    account_id = session.get('account_id')
    if not account_id:
        return jsonify(success=False, message="Invalid session."), 401

    from managers.webhook_manager import test_user_webhook
    success, message = test_user_webhook(account_id)

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json:
        return jsonify(success=success, message=message), (200 if success else 400)

    flash(message)
    return redirect(url_for('user.index') + "#WebhooksArea")


@user.route('/webhook/delete', methods=['POST'])
@login_required
def delete_webhook():
    account_id = session.get('account_id')
    if not account_id:
        return redirect(url_for('user.index'))

    from managers.webhook_manager import delete_user_webhook
    deleted = delete_user_webhook(account_id)

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json:
        return jsonify(success=deleted, message="Webhook removed."), 200

    flash("Webhook configuration removed.")
    return redirect(url_for('user.index') + "#WebhooksArea")

