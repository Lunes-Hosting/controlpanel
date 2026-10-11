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
from flask import current_app
from config import PTERODACTYL_URL, PTERODACTYL_ADMIN_KEY
from managers.database_manager import DatabaseManager
from .logging import webhook_log
from .user_manager import get_ptero_id, update_last_seen
from .email_manager import send_email
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
    cutoff_15d = datetime.datetime.now() - datetime.timedelta(days=15)
    pending_deletions = db.execute_query(
        "SELECT id, email, deletion_requested_time FROM pending_deletions WHERE deletion_requested_time IS NOT NULL AND deletion_requested_time < %s LIMIT 50",
        (cutoff_15d,),
        fetch_all=True
    )
    if pending_deletions:
        for pending in pending_deletions:
            pending_id = pending[0]
            email = pending[1]
            request_time = pending[2]
            
            webhook_log(f"Processing pending deletion for {email} after 15 days", database_log=True)
            
            try:
                # First verify the user still exists locally
                user_exists = db.execute_query("SELECT id FROM users WHERE email = %s", (email,))
                if not user_exists:
                    webhook_log(f"User {email} already deleted, cleaning up pending deletion entry", database_log=True)
                    db.execute_query("DELETE FROM pending_deletions WHERE email = %s", (email,))
                    continue
                
                user_id = user_exists[0]
                
                # Check Pterodactyl ID before deletion
                ptero_id_result = get_ptero_id(email)
                ptero_id = ptero_id_result[0] if (ptero_id_result and ptero_id_result[0]) else None
                
                ptero_success = False
                if not ptero_id:
                    webhook_log(f"User {email} not found in Pterodactyl, proceeding with local deletion", status=1, database_log=True)
                    ptero_success = True
                else:
                    try:
                        response = requests.delete(f"{PTERODACTYL_URL}api/application/users/{ptero_id}", headers=HEADERS, timeout=60)
                        if response.status_code in [204, 404]:
                            # 204 = Successfully deleted, 404 = Already deleted on Pterodactyl
                            ptero_success = True
                        else:
                            webhook_log(f"Failed to delete {email} from Pterodactyl - Status: {response.status_code}", status=2, database_log=True)
                    except Exception as req_err:
                        webhook_log(f"Network error deleting {email} from Pterodactyl: {str(req_err)}", status=2, database_log=True)
                
                if ptero_success:
                    # Synchronously delete local user data and clean up pending deletion entry
                    db.execute_query("DELETE FROM ticket_comments WHERE user_id = %s", (user_id,))
                    db.execute_query("DELETE FROM tickets WHERE user_id = %s", (user_id,))
                    db.execute_query("DELETE FROM users WHERE id = %s", (user_id,))
                    db.execute_query("DELETE FROM pending_deletions WHERE email = %s", (email,))
                    webhook_log(f"Successfully processed pending deletion for {email}", database_log=True)
                    
            except Exception as e:
                webhook_log(f"Error processing pending deletion for {email}: {str(e)}", status=2, database_log=True)
    
    # Reset passwords for long-inactive users (180+ days)
    cutoff_180d = datetime.datetime.now() - datetime.timedelta(days=180)
    inactive_users = db.execute_query(
        "SELECT last_seen, email FROM users WHERE last_seen IS NOT NULL AND last_seen < %s LIMIT 50",
        (cutoff_180d,),
        fetch_all=True
    )
    if inactive_users:
        for last_seen, email in inactive_users:
            try:
                webhook_log(f"Resetting password for inactive user {email}", database_log=True)
                new_password = secrets.token_hex(32)
                hashed_password = bcrypt.hashpw(new_password.encode('utf-8'), bcrypt.gensalt(rounds=14))
                db.execute_query("UPDATE users SET password = %s WHERE email = %s", (hashed_password, email))
                
                # Update Pterodactyl password if user has a Pterodactyl account
                ptero_id_result = get_ptero_id(email)
                ptero_id = ptero_id_result[0] if (ptero_id_result and ptero_id_result[0]) else None
                if ptero_id:
                    try:
                        response = safe_requests.get(f"{PTERODACTYL_URL}api/application/users/{ptero_id}", headers=HEADERS, timeout=60)
                        if response.status_code == 200:
                            response_data = response.json()
                            if 'attributes' in response_data:
                                info = response_data['attributes']
                                body = {
                                    "username": info['username'],
                                    "email": info['email'],
                                    "first_name": info['first_name'],
                                    "last_name": info['last_name'],
                                    "password": new_password
                                }
                                patch_response = requests.patch(f"{PTERODACTYL_URL}api/application/users/{ptero_id}", headers=HEADERS, json=body, timeout=60)
                                if patch_response.status_code in [200, 201, 204]:
                                    webhook_log(f"Successfully reset password for inactive user {email} in Pterodactyl", database_log=True)
                                else:
                                    webhook_log(f"Failed to update Pterodactyl password for {email}: Status {patch_response.status_code}", status=2, database_log=True)
                            else:
                                webhook_log(f"Missing 'attributes' in Pterodactyl API response for user {email}", status=2, database_log=True)
                        elif response.status_code == 404:
                            webhook_log(f"Failed to get user info from Pterodactyl for {email}: Status 404", status=1, database_log=True)
                        else:
                            webhook_log(f"Failed to get user info from Pterodactyl for {email}: Status {response.status_code}", status=2, database_log=True)
                    except Exception as ptero_err:
                        webhook_log(f"Error communicating with Pterodactyl for {email}: {str(ptero_err)}", status=2, database_log=True)
                
                # Update last_seen so the user is marked processed and not reset again for 180 days
                update_last_seen(email)
                
            except Exception as e:
                webhook_log(f"Error resetting password for {email}: {str(e)}", status=2, database_log=True)
                try:
                    update_last_seen(email)
                except Exception:
                    pass

def delete_inactive_free_servers():
    """
    Handles lifecycle of free tier servers for users who haven't logged in recently.
    
    Process:
    1. Gets all servers from Pterodactyl
    2. Identifies free tier servers based on specs
    3. Checks last login time of server owners
    4. If inactivity > 15 days, suspend the server (grace period)
    5. If inactivity > 17 days, delete the server
    6. Logs actions
    """
    db = DatabaseManager()
    
    # Get all servers from Pterodactyl
    response = safe_requests.get(f"{PTERODACTYL_URL}api/application/servers?per_page=100000", headers=HEADERS, timeout=60)
    if response.status_code != 200:
        webhook_log(f"Failed to get servers for inactive free tier check: {response.status_code}", database_log=True)
        return
        
    servers = response.json()
    if 'data' not in servers:
        webhook_log("No servers found for inactive free tier check")
        return
    
    # Process each server
    for server in servers['data']:
        try:
            user_id = server['attributes']['user']
            server_id = server['attributes']['id']
            server_name = server['attributes']['name']

            
            # Get product info based on server specs
            from managers.credit_manager import convert_to_product
            product = convert_to_product(server)
            
            # Check if this is a free tier server (price = 0)
            if int(product['price']) == 0:
                # Get user's last seen time
                last_seen_result = db.execute_query("SELECT last_seen FROM users WHERE pterodactyl_id = %s", (user_id,))
                
                if last_seen_result and last_seen_result[0]:
                    last_seen = last_seen_result[0]
                    inactivity = datetime.datetime.now() - last_seen

                    # Delete after 17+ days of inactivity
                    if inactivity > datetime.timedelta(days=17):
                        from managers.server_manager import delete_server
                        delete_server(server_id)
                        webhook_log(f"Deleted free tier server {server_name} (ID: {server_id}) due to owner inactivity (17+ days)", database_log=True)

                    # Suspend after 15+ days of inactivity (grace period), only if not already suspended
                    elif inactivity > datetime.timedelta(days=15):
                        if not server['attributes'].get('suspended', False):
                            from managers.server_manager import suspend_server
                            suspend_server(server_id)
                            webhook_log(f"Suspended free tier server {server_name} (ID: {server_id}) due to owner inactivity (15+ days)", database_log=True)

                            # Send suspension email to the owner
                            try:
                                email_result = db.execute_query(
                                    "SELECT email FROM users WHERE pterodactyl_id = %s",
                                    (user_id,)
                                )
                                if email_result and email_result[0]:
                                    user_email = email_result[0]
                                    message = (
                                        "Your free-tier server has been suspended due to inactivity (no login for over 15 days).\n\n"
                                        "To keep your server, please log in to your account within the next 2 days. "
                                        "Servers inactive for 17+ days are automatically deleted.\n\n"
                                        "If you believe this was a mistake or need assistance, please contact support."
                                    )
                                    send_email(
                                        user_email,
                                        "Server Suspended Due to Inactivity",
                                        message,
                                        current_app._get_current_object()
                                    )
                                    webhook_log(
                                        f"Suspension email sent to {user_email} for server {server_name} (ID: {server_id})",
                                        database_log=True
                                    )
                            except Exception as e:
                                webhook_log(
                                    f"Failed to send suspension email for server {server_name} (ID: {server_id}): {str(e)}",
                                    database_log=True
                                )
        except Exception as e:
            webhook_log(f"Error processing server for inactive free tier check: {str(e)}", database_log=True)
