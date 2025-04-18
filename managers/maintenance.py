"""
Maintenance Module
=================

This module handles scheduled maintenance tasks including:
- Processing pending user deletions
- Resetting passwords for inactive users
- Other automated maintenance operations

Functions in this module interact with both the local database and the Pterodactyl API
to perform maintenance operations across the system.
"""

import datetime
import threading
import requests
import bcrypt
import secrets
from config import PTERODACTYL_URL, PTERODACTYL_ADMIN_KEY
from managers.database_manager import DatabaseManager
from .logging import webhook_log
from .user_manager import get_ptero_id, update_last_seen
from security import safe_requests

# API authentication headers
HEADERS = {
    "Authorization": f"Bearer {PTERODACTYL_ADMIN_KEY}",
    'Accept': 'application/json',
    'Content-Type': 'application/json'
}

def sync_users_script():
    """
    Handles periodic maintenance tasks:
    1. Process pending deletions after 15 days
    2. Reset passwords for inactive users (180+ days)
    """
    db = DatabaseManager()
    
    # Process pending deletions after 15 days
    results = db.execute_query("SELECT * FROM pending_deletions", fetch_all=True)
    if results:
        for user in results:
            email = user[1]
            request_time = user[2]
            
            if datetime.datetime.now() - request_time > datetime.timedelta(days=15):
                webhook_log(f"Processing pending deletion for {email} after 15 days", database_log=True)
                
                # First verify the user still exists
                user_exists = db.execute_query("SELECT * FROM users WHERE email = %s", (email,))
                if not user_exists:
                    webhook_log(f"User {email} already deleted, cleaning up pending deletion entry", database_log=True)
                    db.execute_query("DELETE FROM pending_deletions WHERE email = %s", (email,))
                    continue
                
                try:
                    # Get Pterodactyl ID before deletion
                    ptero_id = get_ptero_id(email)
                    if not ptero_id:
                        threading.Thread(target=webhook_log, args=(f"User {email} not found in Pterodactyl, cleaning up pending deletion entry", 1)).start()
                        db.execute_query("DELETE FROM pending_deletions WHERE email = %s", (email,))
                        continue
                        
                    # Try to delete from Pterodactyl first
                    response = requests.delete(f"{PTERODACTYL_URL}api/application/users/{ptero_id[0]}", headers=HEADERS, timeout=60)
                    if response.status_code != 204:
                        threading.Thread(target=webhook_log, args=(f"Failed to delete {email} from Pterodactyl - Status: {response.status_code}", 2)).start()
                        continue
                        
                    # If Pterodactyl deletion succeeded, delete locally
                    user_id = db.execute_query("SELECT id FROM users WHERE email = %s", (email,))[0]
                    
                    # Delete user's tickets and comments
                    threading.Thread(target=db.execute_query, args=("DELETE FROM ticket_comments WHERE user_id = %s", (user_id,))).start()
                    threading.Thread(target=db.execute_query, args=("DELETE FROM tickets WHERE user_id = %s", (user_id,))).start()
                    
                    # Finally delete the user
                    threading.Thread(target=db.execute_query, args=("DELETE FROM users WHERE id = %s", (user_id,))).start()
                    
                    # Clean up pending deletion entry
                    threading.Thread(target=db.execute_query, args=("DELETE FROM pending_deletions WHERE email = %s", (email,))).start()
                    
                    webhook_log(f"Successfully processed pending deletion for {email}", database_log=True)
                    
                except Exception as e:
                    webhook_log(f"Error processing pending deletion for {email}: {str(e)}", database_log=True)
    
    # Reset passwords for long-inactive users
    query = f"SELECT last_seen, email FROM users"
    result = db.execute_query(query, fetch_all=True)
    if result:
        for last_seen, email in result:
            if last_seen is not None:
                if datetime.datetime.now() - last_seen > datetime.timedelta(days=180):
                    try:
                        webhook_log(f"Resetting password for inactive user {email}", database_log=True)
                        new_password = secrets.token_hex(32)
                        hashed_password = bcrypt.hashpw(new_password.encode('utf-8'), bcrypt.gensalt(rounds=14))
                        threading.Thread(target=db.execute_query, args=("UPDATE users SET password = %s WHERE email = %s", (hashed_password, email))).start()
                        
                        # Update Pterodactyl password if possible
                        ptero_id = get_ptero_id(email)
                        if ptero_id:
                            info = safe_requests.get(f"{PTERODACTYL_URL}api/application/users/{ptero_id[0]}", headers=HEADERS, timeout=60).json()['attributes']
                            body = {
                                "username": info['username'],
                                "email": info['email'],
                                "first_name": info['first_name'],
                                "last_name": info['last_name'],
                                "password": new_password
                            }
                            requests.patch(f"{PTERODACTYL_URL}api/application/users/{ptero_id[0]}", headers=HEADERS, json=body, timeout=60)
                            update_last_seen(email)
                    except Exception as e:
                        threading.Thread(target=webhook_log, args=(f"Error resetting password for {email}: {str(e)}", 2)).start()
