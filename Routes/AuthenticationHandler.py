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
from flask import Blueprint, request, render_template, session, flash, current_app, redirect, url_for
import sys
import threading
import json
import datetime
import requests
import bcrypt

from flask_limiter import Limiter
from security import safe_requests

sys.path.append("..")
from pterocache import *
from managers.authentication import login_required, login, register
from managers.email_manager import send_email, generate_verification_token, send_verification_email, generate_reset_token, send_reset_email
from managers.user_manager import account_get_information, get_id, get_name, instantly_delete_user, get_ptero_id
from managers.server_manager import improve_list_servers, delete_server as manager_delete_server
from managers.credit_manager import convert_to_product
from managers.utils import HEADERS
from managers.logging import webhook_log
from products import products

from cacheext import cache
from managers.database_manager import DatabaseManager
from managers.announcement_manager import AnnouncementManager
from config import PTERODACTYL_URL, RECAPTCHA_SECRET_KEY, RECAPTCHA_SITE_KEY

# Create a blueprint for the user routes
user = Blueprint('user', __name__)
TOKEN_EXPIRATION_TIME = 1800  # 30 minutes

pterocache = PteroCache()

def _get_client_ip(req: 'flask.Request') -> str:
    """Best-effort real client IP behind Cloudflare/Proxies.
    Order: CF-Connecting-IP -> X-Forwarded-For (first) -> remote_addr.
    """
    ip = req.headers.get('CF-Connecting-IP')
    if not ip:
        xff = req.headers.get('X-Forwarded-For')
        if xff:
            ip = xff.split(',')[0].strip()
    if not ip:
        ip = req.remote_addr
    return ip

@user.route('/login', methods=['POST', 'GET'])
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
        recaptcha_response = request.form.get('g-recaptcha-response')
        data = {
            'secret': RECAPTCHA_SECRET_KEY,
            'response': recaptcha_response
        }

        response = requests.post('https://challenges.cloudflare.com/turnstile/v0/siteverify', data=data, timeout=60)
        result = response.json()
        if not result['success']:
            flash("Failed captcha please try again")
            return render_template("login.html", RECAPTCHA_PUBLIC_KEY=RECAPTCHA_SITE_KEY)

        data = request.form
        email = data.get('email')
        password = data.get('password')
        ip = _get_client_ip(request)
        try:
            response = login(email, password, ip)
            if response is None:
                flash("Incorrect information. Please ensure you have an account.")
                return redirect(url_for('user.login_user'))

            try:
                _ = json.load(response)
                return response
            except AttributeError:
                session['email'] = email
                # Check for next parameter in form data, URL args, then fall back to session next
                next_page = request.form.get('next') or request.args.get('next') or session.pop("next", url_for("user.index"))
                # Make sure we have a valid URL to redirect to
                if not next_page or next_page == 'None' or next_page == '':
                    next_page = url_for("user.index")
                return redirect(next_page)
        except Exception as e:
            print(e)
            flash("An error occurred during login. Please try again.")
    if 'email' in session:
        # Check for next parameter in form data, URL args, then fall back to session next
        next_page = request.form.get('next') or request.args.get('next') or session.pop("next", url_for("user.index"))
        # Make sure we have a valid URL to redirect to
        if not next_page or next_page == 'None' or next_page == '':
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

    session['suspended'] = suspended

    response = improve_list_servers(ptero_id)
    
    # Extract servers from the response
    servers = []
    if response and 'attributes' in response and 'relationships' in response['attributes']:
        if 'servers' in response['attributes']['relationships']:
            servers = response['attributes']['relationships']['servers']['data']
    
    server_count = len(servers)
    monthly_usage = sum(convert_to_product(server)['price'] for server in servers)

    #username = DatabaseManager.execute_query(
    #    "SELECT name FROM users WHERE email = %s", 
    #    (session['email'],)
    #)

    products_local = tuple(products)
    fixed_list: list[dict] = []
    for product in products_local:
        x = product.get("price_link", None)
        if x is not None:
            fixed_list.append(product)
            #products_local.remove(product)

    # Get active announcements
    announcements = AnnouncementManager.get_active_announcements()

    return render_template(
        "account.html", 
        credits=int(current_credits), 
        server_count=server_count,
        username=username, 
        hash=sha256(session['email'].encode('utf-8')).hexdigest(),
        email=session['email'], 
        monthly_usage=monthly_usage,
        servers=servers,
        products=fixed_list,
        verified=verified,
        suspended=suspended,
        announcements=announcements
    )


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
        data = {
            'secret': RECAPTCHA_SECRET_KEY,
            'response': recaptcha_response
        }

        response = requests.post('https://challenges.cloudflare.com/turnstile/v0/siteverify', data=data, timeout=60)
        result = response.json()
        if not result['success']:
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

        if token_cache == token:
            if password == confirm_password:
                salt = bcrypt.gensalt(rounds=14)
                password_hash = bcrypt.hashpw(password.encode('utf-8'), salt)

                ptero_id = get_ptero_id(email)
                if ptero_id is None:
                    flash('Error: User not found in Pterodactyl panel')
                    return redirect(url_for('user.login_user'))
                
                # Update password in database
                DatabaseManager.execute_query(
                    "UPDATE users SET password = %s WHERE email = %s",
                    (password_hash.decode(), email)
                )

                # Update password in panel
                info = safe_requests.get(f"{PTERODACTYL_URL}api/application/users/{ptero_id[0]}", headers=HEADERS, timeout=60).json()['attributes']
                body = {
                    "username": info['username'],
                    "email": info['email'],
                    "first_name": info['first_name'],
                    "last_name": info['last_name'],
                    "password": password
                }

                requests.patch(f"{PTERODACTYL_URL}api/application/users/{ptero_id[0]}", headers=HEADERS, json=body, timeout=60)

                cache.delete(token)

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
    if request.method == "POST":
        recaptcha_response = request.form.get('g-recaptcha-response')
        data = {
            'secret': RECAPTCHA_SECRET_KEY,
            'response': recaptcha_response
        }

        response = requests.post('https://challenges.cloudflare.com/turnstile/v0/siteverify', data=data, timeout=60)
        result = response.json()
        if not result['success']:
            flash("Failed captcha please try again")
            return render_template("register.html", RECAPTCHA_PUBLIC_KEY=RECAPTCHA_SITE_KEY)
        

        data = request.form
        email = data.get('email')
        password = data.get('password')
        name = data.get('username')
        ip = _get_client_ip(request)

        # Check if 'suspended' key exists in session, if not, initialize it to False
        if 'suspended' not in session:
            session['suspended'] = False

        if session['suspended']:
            flash("Failed to register! If this is an error, please contact support. panel@lunes.host")
            webhook_log(f"Failed to register email {email} ip: {ip} due to alt suspended account", non_embed_message="<@491266830674034699>")
            return render_template("register.html", RECAPTCHA_PUBLIC_KEY=RECAPTCHA_SITE_KEY)

        res = register(email, password, name, ip)
        if isinstance(res, str):
            flash(res + " If this is an error, please contact support.")
            return render_template("register.html", RECAPTCHA_PUBLIC_KEY=RECAPTCHA_SITE_KEY)

        verification_token = generate_verification_token()
        cache.set(verification_token, email, timeout=TOKEN_EXPIRATION_TIME)

        email_thread = threading.Thread(
            target=send_verification_email, 
            args=(email, verification_token, current_app._get_current_object()), 
            daemon=True
        )
        email_thread.start()

        flash('A verification email has been sent. Please check your inbox and spam to verify your email.')
        return redirect(url_for('index'))
    if 'email' in session:
        return redirect(url_for("user.index"))
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
