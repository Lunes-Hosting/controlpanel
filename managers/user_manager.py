"""
User Management Module
=================

This module handles all user-related operations including:
- User identification and information retrieval
- User status management
- User deletion
- Activity tracking

Functions in this module interact with both the local database and the Pterodactyl API
to manage user accounts across the system.
"""

import bcrypt
import requests
import threading
import datetime
from config import PTERODACTYL_URL, PTERODACTYL_ADMIN_KEY
from managers.database_manager import DatabaseManager
from .logging import webhook_log

# API authentication headers
HEADERS = {
    "Authorization": f"Bearer {PTERODACTYL_ADMIN_KEY}",
    'Accept': 'application/json',
    'Content-Type': 'application/json'
}

def get_ptero_id(email: str):
    """
    Gets Pterodactyl ID for a user by their email.
    
    Args:
        email: User's email address
    
    Returns:
        tuple[int]: Tuple containing Pterodactyl ID at index 0
        None: If user not found
    """
    query = "SELECT pterodactyl_id FROM users WHERE email = %s"
    result = DatabaseManager.execute_query(query, (email,))
    return result

def get_id(email: str):
    """
    Gets user ID for a user by their email.
    
    Args:
        email: User's email address
    
    Returns:
        tuple[int]: Tuple containing user ID at index 0
        None: If user not found
    """
    query = "SELECT id FROM users WHERE email = %s"
    result = DatabaseManager.execute_query(query, (email,))
    return result

def get_name(user_id: int):
    """
    Gets username for a user by their ID.
    
    Args:
        user_id: User's ID
    
    Returns:
        tuple[str]: Tuple containing username at index 0
        None: If user not found
    """
    query = "SELECT name FROM users WHERE id = %s"
    result = DatabaseManager.execute_query(query, (user_id,))
    return result

def account_get_information(email: str):
    """
    Gets account information for a user by their email.
    
    Args:
        email: User's email address
    
    Returns:
        tuple: (credits, pterodactyl_id, name, verified, suspended)
    """
    query = "SELECT credits, pterodactyl_id, name, email_verified_at, suspended FROM users WHERE email = %s"
    result = DatabaseManager.execute_query(query, (email,))
    
    if result:
        # Convert email_verified_at to a boolean
        verified = False
        if result[3] is not None:
            verified = True
        
        # Return the 5 expected values
        return (result[0], result[1], result[2], verified, result[4])
    
    return (0, None, None, False, False)

def update_ip(email: str, real_ip: str):
    """
    Updates the ip by getting the header with key "CF-Connecting-IP" default is "localhost".
    
    Args:
        email: User's email
        real_ip: The real IP address of the user.
    
    Returns:
        None
    """
    query = "UPDATE users SET ip = %s WHERE email = %s"
    DatabaseManager.execute_query(query, (real_ip, email))

def update_last_seen(email: str, everyone: bool = False):
    """
    Sets a users last seen to current time in database, if "everyone" is True it updates everyone in database to
    current time.
    
    Args:
        email: User's email
        everyone: Whether to update all users
    
    Returns:
        None
    """
    if everyone:
        query = "UPDATE users SET last_seen = %s"
        DatabaseManager.execute_query(query, (datetime.datetime.now(),))
    else:
        query = "UPDATE users SET last_seen = %s WHERE email = %s"
        DatabaseManager.execute_query(query, (datetime.datetime.now(), email))

def get_last_seen(email: str):
    """
    Returns datetime object of when user with that email was last seen.
    
    Args:
        email: User's email
    
    Returns:
        datetime.datetime: Last seen time
    """
    query = "SELECT last_seen FROM users WHERE email = %s"
    result = DatabaseManager.execute_query(query, (email,))
    return result

def is_admin(email: str):
    """
    Checks if user is an admin.
    
    Args:
        email: User's email
    
    Returns:
        bool: Whether user is an admin
    """
    query = "SELECT role FROM users WHERE email = %s"
    result = DatabaseManager.execute_query(query, (email,))
    return result and result[0] == "admin"

def check_if_user_suspended(pterodactyl_id: str):
    """
    Returns the bool value of if a user is suspended, if user is not found with the pterodactyl id it returns None
    
    Args:
        pterodactyl_id: Pterodactyl user ID
    
    Returns:
        bool: Whether user is suspended
        None: If user not found
    """
    query = "SELECT suspended FROM users WHERE pterodactyl_id = %s"
    result = DatabaseManager.execute_query(query, (pterodactyl_id,))
    return result[0] if result else None

def get_user_verification_status_and_suspension_status(email):
    query = "SELECT email_verified_at, suspended FROM users WHERE email = %s"
    result = DatabaseManager.execute_query(query, (email,))
    return result

def instantly_delete_user(email: str, skip_email: bool = False):
    """
    Deletes a user from both the panel database and Pterodactyl.
    
    Args:
        email: User's email address
        skip_email: If True, won't send deletion email (used during sync)
    
    Returns:
        int: HTTP status code from deletion request
    """
    # Get Pterodactyl ID
    ptero_id = get_ptero_id(email)
    if not ptero_id:
        threading.Thread(target=webhook_log, args=(f"User {email} not found in database", 2)).start()
        return 404
        
    # Try to delete from Pterodactyl first
    response = requests.delete(f"{PTERODACTYL_URL}api/application/users/{ptero_id[0]}", headers=HEADERS, timeout=60)
    
    # If Pterodactyl deletion succeeded or user not found in panel, delete locally
    if response.status_code == 204 or response.status_code == 404:
        # Get user ID for local deletion
        user_id = get_id(email)
        if user_id:
            # Delete user's tickets and comments
            DatabaseManager.execute_query("DELETE FROM ticket_comments WHERE user_id = %s", (user_id[0],))
            DatabaseManager.execute_query("DELETE FROM tickets WHERE user_id = %s", (user_id[0],))
            
            # Finally delete the user
            DatabaseManager.execute_query("DELETE FROM users WHERE id = %s", (user_id[0],))
            
            webhook_log(f"Successfully deleted user {email}", 1, database_log=True)
            
            # Send email notification if not skipped
            if not skip_email:
                from .email_manager import send_email
                from flask import current_app
                message = f"Your account has been deleted from our system. If you believe this was done in error, please contact support."
                threading.Thread(target=send_email, args=(email, "Account Deleted", message, current_app._get_current_object())).start()
                
            return 204
        else:
            webhook_log(f"User {email} not found in database during deletion", 2, database_log=True)
            return 404
    else:
        webhook_log(f"Failed to delete {email} from Pterodactyl - Status: {response.status_code}", 2, database_log=True)
        return response.status_code

def delete_user(pterodactyl_id: int):
    """
    Deletes a user from Pterodactyl by their Pterodactyl ID.
    
    Args:
        pterodactyl_id: Pterodactyl user ID
        
    Returns:
        bool: True if successful, False otherwise
    """
    response = requests.delete(f"{PTERODACTYL_URL}api/application/users/{pterodactyl_id}", headers=HEADERS, timeout=60)
    if response.status_code == 204:
        return True
    return False
