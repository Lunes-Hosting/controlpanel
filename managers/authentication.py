"""
Authentication Module
=================

This module handles all authentication-related operations including:
- User login
- User registration
- Password verification

Functions in this module interact with both the local database and the Pterodactyl API
to authenticate and register users across the system.
"""
from threadedreturn import ThreadWithReturnValue
import bcrypt
import requests
import threading
from config import PTERODACTYL_URL, PTERODACTYL_ADMIN_KEY
from managers.database_manager import DatabaseManager
from .logging import webhook_log
from .user_manager import update_ip, update_last_seen
from functools import wraps
from flask import session, redirect, url_for, current_app, render_template, request
from urllib.parse import quote
from managers.email_manager import send_email
from security import safe_requests


# API authentication headers
HEADERS = {
    "Authorization": f"Bearer {PTERODACTYL_ADMIN_KEY}",
    'Accept': 'application/json',
    'Content-Type': 'application/json'
}

def login_required(f):
    """
    Decorator that checks if a user is logged in.
    Redirects to login page if not logged in and passes the original URL
    as a query parameter for redirect after successful login.
    
    Args:
        f: Function to decorate
        
    Returns:
        Function: Decorated function
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'email' not in session:
            # Only pass the path portion of the URL for redirection after login
            next_url = request.path
            return redirect(url_for('user.login_user', next=next_url))
        update_last_seen(session['email'])
        return f(*args, **kwargs)
        
    return decorated_function

def admin_required(f):
    """
    Decorator that checks if a user is an admin.
    Redirects to login page if not logged in or returns error if not admin.
    Passes the original URL as a query parameter for redirect after successful login.
    
    Args:
        f: Function to decorate
        
    Returns:
        Function: Decorated function
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'email' not in session:
            # Only pass the path portion of the URL for redirection after login
            next_url = request.path
            return redirect(url_for('user.login_user', next=next_url))
        
        from .user_manager import is_admin
        if not is_admin(session['email']):
            return render_template('admin/forbidden.html')
        
        return f(*args, **kwargs)
    return decorated_function

def login(email: str, password: str, ip: str):
    """
    Authenticates user login credentials.
    
    Process:
    1. Gets hashed password from database
    2. Verifies password using bcrypt
    3. If matched, returns all user information
    4. Removes user from pending deletions if present
    
    Args:
        email: User's email
        password: Plain text password
        ip: User's IP address
    
    Returns:
        tuple: All user information from database if login successful
        None: If login fails
    """
    # Get user from database
    query = "SELECT * FROM users WHERE email = %s"
    result = DatabaseManager.execute_query(query, (email,))
    
    if result:
        # Get hashed password from database
        hashed_password = result[9]
        # Verify password
        if bcrypt.checkpw(password.encode('utf-8'), hashed_password.encode('utf-8')):
            # Update last seen and IP
            update_last_seen(email)
            update_ip(email, ip)
            
            # Check if user was pending deletion and remove from pending deletions
            pending_query = "SELECT * FROM pending_deletions WHERE email = %s"
            pending_result = DatabaseManager.execute_query(pending_query, (email,))
            
            if pending_result:
                # Remove from pending deletions
                delete_query = "DELETE FROM pending_deletions WHERE email = %s"
                DatabaseManager.execute_query(delete_query, (email,))
                
                # Import email sending function
                
                
                # Send notification email
                email_subject = "Account Deletion Cancelled"
                email_body = "Your account was previously marked for deletion, but since you've logged in, the deletion process has been cancelled. Your account is now fully active again."
                
                # Send email in a separate thread to avoid blocking
                threading.Thread(
                    target=send_email, 
                    args=(email, email_subject, email_body, current_app._get_current_object())
                ).start()
                
                # Log the cancellation
                webhook_log(f"User {email} logged in, cancelling pending account deletion", 0, database_log=True)
            
            # Log successful login
            webhook_log(f"User {email} logged in from {ip}", 0, database_log=True)
            
            return result
    
    # Log failed login attempt
    webhook_log(f"Failed login attempt for {email} from {ip}", 1, database_log=True)
    
    return None

def register(email: str, password: str, name: str, ip: str):
    """
    Registers a new user.
    
    Process:
    1. Validates email and name
    2. Checks if IP already registered
    3. Creates user in Pterodactyl
    4. Creates user in local database with:
        - Hashed password
        - Default 10 credits
        - Stored IP
    
    Args:
        email: User's email
        password: Plain text password
        name: Username
        ip: User's IP address
    
    Returns:
        dict: User object from Pterodactyl API if successful
        str: Error message if registration fails
    """
    
    # Clean and normalize inputs
    email = email.strip().lower()
    name = name.strip().lower()
    salt = bcrypt.gensalt(rounds=14)
    passthread = ThreadWithReturnValue(target=bcrypt.hashpw, args=(password.encode('utf-8'), salt))
    passthread.start()

    # Check for blocked emails
    try:
        resp_emails = safe_requests.get("https://lunes.host/blockedemails.txt", timeout=60)
        blocked_emails = [line.strip() for line in resp_emails.text.splitlines() if line.strip()]
        banned_emails = set(blocked_emails)
            
        if "+" in email or any(banned in email for banned in banned_emails):
            webhook_log(f"Failed to register email {email} with IP {ip} due to email blacklist", non_embed_message="<@491266830674034699>", database_log=True)

            session['suspended'] = True
            return "Temporary emails are not allowed! Contact panel@lunes.host if this is a mistake"
    except Exception as e:
        print(f"Error checking blocked emails: {str(e)}")
    
    webhook_log(f"User with email: {email}, name: {name} ip: {ip} registered", database_log=True)
    
    # Check if IP is already registered
    results = DatabaseManager.execute_query("SELECT * FROM users WHERE ip = %s", (ip,))
    if results is not None:
        return "IP is already registered"

    # Create user in Pterodactyl
    body = {
        "email": email,
        "username": name,
        "first_name": name,
        "last_name": name,
        "password": password
    }

    response = requests.post(f"{PTERODACTYL_URL}api/application/users", headers=HEADERS, json=body, timeout=60)
    data = response.json()

    try:
        error = data['errors'][0]['detail']
        return error
    except KeyError:
        # Get next user ID
        user_id = DatabaseManager.execute_query("SELECT * FROM users ORDER BY id DESC LIMIT 0, 1")[0] + 1
        
        # Insert user into database
        query = ("INSERT INTO users (name, email, password, id, pterodactyl_id, ip, credits) VALUES (%s, %s, %s, %s, %s, %s, %s)")
        password_hash = passthread.join()
        values = (name, email, password_hash, user_id, data['attributes']['id'], ip, 10)
        DatabaseManager.execute_query(query, values)
        
        return data
