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
from flask import Blueprint, request, render_template, session, flash, current_app, redirect, url_for
import sys
import threading
import json
import datetime
import requests
import bcrypt

from flask_limiter import Limiter
sys.path.append("..")
from pterocache import *
from scripts import *
from cacheext import cache
from managers.database_manager import DatabaseManager
from config import PTERODACTYL_URL, RECAPTCHA_SECRET_KEY, RECAPTCHA_SITE_KEY

# Create a blueprint for the user routes
user = Blueprint('user', __name__)
TOKEN_EXPIRATION_TIME = 1800  # 30 minutes

pterocache = PteroCache()

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
        - after_request(): Updates session data
        - get_ptero_id(): Gets Pterodactyl panel ID
    """
    after_request(session=session, request=request.environ)
    if request.method == "POST":
        recaptcha_response = request.form.get('g-recaptcha-response')
        data = {
            'secret': RECAPTCHA_SECRET_KEY,
            'response': recaptcha_response
        }

        response = requests.post('https://challenges.cloudflare.com/turnstile/v0/siteverify', data=data)
        result = response.json()
        if not result['success']:
            flash("Failed captcha please try again")
            return render_template("login.html", RECAPTCHA_PUBLIC_KEY=RECAPTCHA_SITE_KEY)

        data = request.form
        email = data.get('email')
        password = data.get('password')
        try:
            response = login(email, password)
            if response is None:
                flash("Incorrect information. Please ensure you have an account.")
                return redirect(url_for('user.login_user'))

            try:
                _ = json.load(response)
                return response
            except AttributeError:
                session['email'] = email
                return redirect(url_for('index'))
        except Exception as e:
            print(e)
            flash("An error occurred during login. Please try again.")
    if 'email' in session:
        return redirect(url_for("user.index"))
    return render_template("login.html", RECAPTCHA_PUBLIC_KEY=RECAPTCHA_SITE_KEY)


@user.route('/')
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
        - list_servers(): Gets user's server list
    """
    if 'email' not in session:
        return redirect(url_for("user.login_user"))

    asyncio.run(after_request_async(session, request.environ, True))
    current_credits, ptero_id, username = account_get_information(session["email"])
    #print(current_credits)
    #print(ptero_id)
    #print(username)
    #current_credits = get_credits(session['email']) #use_db
    #servers = improve_list_servers(get_ptero_id(session['email'])[0])
    servers = improve_list_servers(ptero_id)
    server_count = len(servers)
    monthly_usage = sum(convert_to_product(server)['price'] for server in servers)

    #username = DatabaseManager.execute_query(
    #    "SELECT name FROM users WHERE email = %s", 
    #    (session['email'],)
    #)

    return render_template(
        "account.html", 
        credits=int(current_credits), 
        server_count=server_count,
        username=username, 
        email=session['email'], 
        monthly_usage=monthly_usage
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

        response = requests.post('https://challenges.cloudflare.com/turnstile/v0/siteverify', data=data)
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
        tokendone = request.form.get("token")

        reset_token = cache.get(email)
        print(cache.get(email), token)

        if reset_token and reset_token == tokendone:
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
                info = requests.get(f"{PTERODACTYL_URL}api/application/users/{ptero_id[0]}", headers=HEADERS).json()['attributes']
                body = {
                    "username": info['username'],
                    "email": info['email'],
                    "first_name": info['first_name'],
                    "last_name": info['last_name'],
                    "password": password
                }

                requests.patch(f"{PTERODACTYL_URL}api/application/users/{ptero_id[0]}", headers=HEADERS, json=body)

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
    if request.method == "POST":
        recaptcha_response = request.form.get('g-recaptcha-response')
        data = {
            'secret': RECAPTCHA_SECRET_KEY,
            'response': recaptcha_response
        }

        response = requests.post('https://challenges.cloudflare.com/turnstile/v0/siteverify', data=data)
        result = response.json()
        if not result['success']:
            flash("Failed captcha please try again")
            return render_template("register.html", RECAPTCHA_PUBLIC_KEY=RECAPTCHA_SITE_KEY)

        data = request.form
        email = data.get('email')
        password = data.get('password')
        name = data.get('username')
        ip = request.headers.get('Cf-Connecting-Ip', request.remote_addr)

        res = register(email, password, name, ip)
        if isinstance(res, str):
            flash(res + " If this is an error, please contact support.")
            return render_template("register.html", RECAPTCHA_PUBLIC_KEY=RECAPTCHA_SITE_KEY)

        verification_token = generate_verification_token()
        cache.set(email, verification_token, timeout=TOKEN_EXPIRATION_TIME)

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
    if 'email' not in session:
        return redirect(url_for("user.login_user"))

    after_request(session=session, request=request.environ, require_login=True)
    verification_token = generate_verification_token()

    cache.set(session['email'], verification_token, timeout=TOKEN_EXPIRATION_TIME)

    email_thread = threading.Thread(
        target=send_verification_email, 
        args=(session['email'], verification_token, current_app._get_current_object()), 
        daemon=True
    )
    email_thread.start()

    flash("Sent a verification email")
    return redirect(url_for('index'))


@user.route('/verify_email/<token>', methods=['GET'])
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
    if 'email' not in session:
        return redirect(url_for("user.login_user"))

    after_request(session=session, request=request.environ, require_login=True)

    email = session['email']
    stored_token = cache.get(email)

    if stored_token and stored_token == token:
        DatabaseManager.execute_query(
            "UPDATE users SET email_verified_at = %s WHERE email = %s",
            (datetime.datetime.now(), email)
        )

        cache.delete(email)

        flash('Your email has been successfully verified.')
    else:
        flash('Invalid or expired verification token.')

    return redirect(url_for('index'))


@user.route('/logout', methods=['GET'])
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
    session.clear()
    return redirect(url_for("index"))


@user.route('/account/delete', methods=['GET', 'POST'])
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
    if 'email' not in session:
        return redirect(url_for("user.login_user"))
        
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
        webhook_log(f"User `{email}` deleted their account")
        session.clear()
        
        # Get and delete all user's servers
        servers = list_servers(ptero_id)
        for server in servers:
            server_id = server['attributes']['id']
            delete_server(server_id)
        
        send_email(email, "Account Deletion", "Your account has been flagged for deletion. If you do not log back in within 30 days, your account will be permanently deleted.", current_app._get_current_object())
        db.execute_query("INSERT INTO pending_deletions (email, deletion_requested_time) VALUES (%s, %s)", (email, datetime.datetime.now()))
            
        flash("Your account has been flagged for deletion. If you do not log back in within 30 days, your account will be permanently deleted.")

    except Exception as e:
        print(f"Error deleting account: {e}")
        flash("Error deleting account. Please contact support.")
        return redirect(url_for('index'))
        
    return redirect(url_for('user.login_user'))
