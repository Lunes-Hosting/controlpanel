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

import bcrypt
import requests
import threading
import datetime
from config import PTERODACTYL_URL, PTERODACTYL_ADMIN_KEY
from managers.database_manager import DatabaseManager
from .logging import webhook_log
from .user_manager import update_ip, update_last_seen
from functools import wraps
from flask import session, redirect, url_for

# API authentication headers
HEADERS = {
    "Authorization": f"Bearer {PTERODACTYL_ADMIN_KEY}",
    'Accept': 'application/json',
    'Content-Type': 'application/json'
}

def login_required(f):
    """
    Decorator that checks if a user is logged in.
    Redirects to login page if not logged in.
    
    Args:
        f: Function to decorate
        
    Returns:
        Function: Decorated function
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'email' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    """
    Decorator that checks if a user is an admin.
    Redirects to login page if not logged in or returns error if not admin.
    
    Args:
        f: Function to decorate
        
    Returns:
        Function: Decorated function
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'email' not in session:
            return redirect(url_for('login'))
        
        from .user_manager import is_admin
        if not is_admin(session['email']):
            return "You are not authorized to access this page."
        
        return f(*args, **kwargs)
    return decorated_function

def login(email: str, password: str, ip: str):
    """
    Authenticates user login credentials.
    
    Process:
    1. Gets hashed password from database
    2. Verifies password using bcrypt
    3. If matched, returns all user information
    
    Args:
        email: User's email
        password: Plain text password
    
    Returns:
        tuple: All user information from database if login successful
        None: If login fails
    """
    # Get user from database
    query = "SELECT * FROM users WHERE email = %s"
    result = DatabaseManager.execute_query(query, (email,))
    
    if result:
        # Get hashed password from database
        hashed_password = result[2]  # Assuming password is at index 2
        
        # Verify password
        if bcrypt.checkpw(password.encode('utf-8'), hashed_password.encode('utf-8')):
            # Update last seen and IP
            update_last_seen(email)
            update_ip(email, ip)
            
            # Log successful login
            threading.Thread(target=webhook_log, args=(f"User {email} logged in from {ip}", 0)).start()
            
            return result
    
    # Log failed login attempt
    threading.Thread(target=webhook_log, args=(f"Failed login attempt for {email} from {ip}", 1)).start()
    
    return None

def register(email: str, password: str, name: str, ip: str):
    """
    Registers a new user in both Pterodactyl and local database.
    
    Process:
    1. Checks for banned emails
    2. Checks if IP already registered
    3. Creates user in Pterodactyl
    4. Creates user in local database with:
        - Hashed password
        - Default 25 credits
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
    # Check for banned email domains
    banned_domains = ["gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "aol.com", "icloud.com"]
    email_domain = email.split('@')[-1]
    
    if email_domain in banned_domains:
        return f"Email domain {email_domain} is not allowed. Please use a business or school email."
    
    # Check if email already exists
    query = "SELECT * FROM users WHERE email = %s"
    result = DatabaseManager.execute_query(query, (email,))
    
    if result:
        return "Email already registered"
    
    # Check if IP already registered (limit 3 accounts per IP)
    ip_query = "SELECT COUNT(*) FROM users WHERE ip = %s"
    ip_result = DatabaseManager.execute_query(ip_query, (ip,))
    
    if ip_result and ip_result[0] >= 3:
        return "Maximum number of accounts reached for this IP address"
    
    # Create user in Pterodactyl
    user_data = {
        "username": name,
        "email": email,
        "first_name": name,
        "last_name": name,
        "password": password,
        "root_admin": False,
        "language": "en"
    }
    
    response = requests.post(f"{PTERODACTYL_URL}api/application/users", headers=HEADERS, json=user_data, timeout=60)
    
    if response.status_code == 201:
        user = response.json()
        pterodactyl_id = user['attributes']['id']
        
        # Hash password for local storage
        hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt(rounds=14))
        
        # Create user in local database
        insert_query = """
            INSERT INTO users (name, email, password, pterodactyl_id, credits, role, ip, last_seen)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """
        
        DatabaseManager.execute_query(
            insert_query,
            (name, email, hashed_password.decode('utf-8'), pterodactyl_id, 25, 'user', ip, datetime.datetime.now())
        )
        
        # Log successful registration
        threading.Thread(target=webhook_log, args=(f"New user registered: {email} from {ip}", 0)).start()
        
        return user
    else:
        # Log failed registration
        try:
            error = response.json()['errors'][0]['detail']
        except:
            error = f"Status code: {response.status_code}"
            
        threading.Thread(target=webhook_log, args=(f"Failed to register user {email}: {error}", 2)).start()
        
        return f"Registration failed: {error}"
