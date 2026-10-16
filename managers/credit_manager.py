"""
Credit Management Module
=================

This module handles all credit-related operations including:
- Credit addition and removal
- Automated credit usage calculation
- Server suspension based on credit status
- Credit-based server unsuspension

Functions in this module interact with both the local database and the Pterodactyl API
to manage the credit system across the platform.
"""

import threading
from threadedreturn import ThreadWithReturnValue
from managers.database_manager import DatabaseManager
from config import PTERODACTYL_URL, PTERODACTYL_ADMIN_KEY
from products import products
from .logging import webhook_log
from .email_manager import send_email
from .server_manager import suspend_server, unsuspend_server, delete_server
from .user_manager import check_if_user_suspended
from .suspension_manager import get_server_suspension
from security import safe_requests
from flask import current_app
import datetime
import requests

# API authentication headers
HEADERS = {
    "Authorization": f"Bearer {PTERODACTYL_ADMIN_KEY}",
    'Accept': 'application/json',
    'Content-Type': 'application/json'
}

def _credit_amount(amount):
    """Reject negative and non-finite amounts before changing a balance."""
    import math
    amount = float(amount)
    if not math.isfinite(amount) or amount < 0:
        raise ValueError("Credit amount must be finite and non-negative")
    return amount


def _add_credits_in_transaction(cursor, email, amount, set_client=True):
    """Apply a credit using the caller's transaction; fail if no account exists."""
    cursor.execute("SELECT id FROM users WHERE email = %s FOR UPDATE", (email,))
    if cursor.fetchone() is None:
        raise ValueError("Credit recipient does not exist")
    cursor.execute("UPDATE users SET credits = credits + %s WHERE email = %s", (amount, email))
    if set_client:
        cursor.execute("UPDATE users SET role = 'client' WHERE email = %s", (email,))


def add_credits(email: str, amount: int, set_client: bool = True):
    """Add credits atomically, including the optional client promotion."""
    amount = _credit_amount(amount)
    with DatabaseManager.transaction() as cursor:
        _add_credits_in_transaction(cursor, email, amount, set_client)
    webhook_log(f"Added {amount} credits to {email}", database_log=True)


def remove_credits(email: str, amount: float):
    """Lock the account until the affordability check and debit commit."""
    amount = _credit_amount(amount)
    with DatabaseManager.transaction() as cursor:
        cursor.execute("SELECT credits FROM users WHERE email = %s FOR UPDATE", (email,))
        result = cursor.fetchone()
        if result is None or float(result[0]) < amount:
            return "SUSPEND"
        cursor.execute("UPDATE users SET credits = credits - %s WHERE email = %s", (amount, email))
    return None


def get_credits(email: str):
    """
    Returns float of amount of credits in database.
    
    Args:
        email: User's email
    
    Returns:
        float: Credits amount
    """
    query = "SELECT credits FROM users WHERE email = %s"
    result = DatabaseManager.execute_query(query, (email,))
    if result:
        return float(result[0])
    return 0.0

def convert_to_product(data):
    """
    Returns Product with matched MEMORY count all other fields ignored.
    
    Args:
        data: Server data
    
    Returns:
        dict: Product information
    """
    memory = data['attributes']['limits']['memory']
    
    # Find matching product based on memory
    for product in products:
        if product['limits']['memory'] == memory:
            return product
            
    # If no exact match, find closest match
    closest_product = min(products, key=lambda p: abs(p['limits']['memory'] - memory))
    return closest_product

def use_credits():
    """
    Scheduled task that processes credit usage for all servers.
    
    Process:
    1. Gets all servers
    2. Batch processes servers by user
    3. Makes a single database query per user
    4. Processes credit deductions individually for each server
    5. Suspends servers if needed
    
    Returns:
        None
    """
    # Get all servers from Pterodactyl
    try:
        response = safe_requests.get(f"{PTERODACTYL_URL}api/application/servers?per_page=100000", headers=HEADERS, timeout=60)
    except requests.exceptions.RequestException as e:
        print(f"Skipping credit processing: Pterodactyl panel timeout or connection error ({e})")
        webhook_log(f"Skipping credit processing: Pterodactyl connection error ({e})", database_log=True)
        return

    if response.status_code != 200:
        webhook_log(f"Failed to get servers for credit processing: {response.status_code}", database_log=True)
        return
        
    servers = response.json()
    if 'data' not in servers:
        webhook_log("No servers found for credit processing")
        return
        
    # Group servers by user
    user_servers = {}
    for server in servers['data']:
        user_id = server['attributes']['user']
        if user_id not in user_servers:
            user_servers[user_id] = []
        user_servers[user_id].append(server)
    
    # Process each user's servers
    for user_id, servers in user_servers.items():
        # Get user email from Pterodactyl
        user_response = safe_requests.get(f"{PTERODACTYL_URL}api/application/users/{user_id}", headers=HEADERS, timeout=60)
        if user_response.status_code != 200:
            webhook_log(f"Failed to get user {user_id} for credit processing: {user_response.status_code}", database_log=False)
            continue
            
        user_email = user_response.json()['attributes']['email']
        
        # Process each server individually
        for server in servers:
            # Skip suspended servers
            if server['attributes']['suspended']:
                continue
                
            # Get product info based on server specs
            product = convert_to_product(server)
            hourly_cost = float(product['price'])/30.0/24.0
            
            # Debit against the live, locked balance, including concurrent purchases.
            if remove_credits(user_email, hourly_cost) == "SUSPEND":
                # User can't afford this server, suspend it
                server_id = server['attributes']['id']
                server_name = server['attributes']['name']
                webhook_log(f"User {user_email} can't afford server {server_name} (ID: {server_id}). Suspending.", database_log=True)
                threading.Thread(target=suspend_server, args=(server_id, "Insufficient credits to pay for this server.", "billing")).start()
        


def check_to_unsuspend():
    """
    Scheduled task that checks for servers that can be unsuspended.
    
    Process:
    1. Gets all servers
    2. Groups suspended servers by user
    3. For each user, checks which servers they can afford
    4. Unsuspends only the affordable servers
    
    Returns:
        None
    """
    # Get all servers from Pterodactyl
    try:
        response = safe_requests.get(f"{PTERODACTYL_URL}api/application/servers?per_page=100000", headers=HEADERS, timeout=60)
    except requests.exceptions.RequestException as e:
        print(f"Skipping suspension check: Pterodactyl panel timeout or connection error ({e})")
        return

    if response.status_code != 200:
        threading.Thread(target=webhook_log, args=(f"Failed to get servers for unsuspension check: {response.status_code}", 2)).start()
        return
        
    servers = response.json()
    if 'data' not in servers:
        return
    
    # Group suspended servers by user
    user_suspended_servers = {}
    
    # Process all servers to find suspended ones
    for server in servers['data']:
        user_id = server['attributes']['user']
        
        # Only process suspended servers for unsuspension check
        if server['attributes']['suspended']:
            if user_id not in user_suspended_servers:
                user_suspended_servers[user_id] = []
            user_suspended_servers[user_id].append(server)
    
    # Process each user's suspended servers
    for user_id, suspended_servers in user_suspended_servers.items():
        # Get user email from Pterodactyl
        try:
            user_response = safe_requests.get(f"{PTERODACTYL_URL}api/application/users/{user_id}", headers=HEADERS, timeout=60)
        except requests.exceptions.RequestException as e:
            print(f"Skipping user {user_id} during unsuspension check: {e}")
            continue

        if user_response.status_code != 200:
            threading.Thread(target=webhook_log, args=(f"Failed to get user {user_id} for unsuspension check: {user_response.status_code}", 2)).start()
            continue
            
        account_suspended = check_if_user_suspended(user_id)
        if account_suspended is None or bool(account_suspended):
            continue
        user_email = user_response.json()['attributes']['email']
        user_credits = float(get_credits(user_email))
        
        # Sort servers by cost (cheapest first) to maximize number of servers that can be unsuspended
        sorted_servers = sorted(suspended_servers, key=lambda s: convert_to_product(s)['price'])
        
        # Try to unsuspend as many servers as possible
        remaining_credits = user_credits
        for server in sorted_servers:
            server_id = server['attributes']['id']
            server_name = server['attributes']['name']
            suspension = get_server_suspension(server_id)
            if not suspension or not suspension["active"] or suspension["source"] not in ("billing", "inactivity"):
                continue

            # Get product info based on server specs
            product = convert_to_product(server)
            hourly_cost = float(product['price'])/30.0/24.0
            
            # If this is a free-tier server (price=0), and the owner hasn't logged in for 15+ days,
            # do NOT unsuspend here. This respects the inactivity suspension policy handled in maintenance.
            try:
                if int(product['price']) == 0:
                    last_seen_result = DatabaseManager.execute_query(
                        "SELECT last_seen FROM users WHERE pterodactyl_id = %s",
                        (user_id,)
                    )
                    if last_seen_result and last_seen_result[0] is not None:
                        last_seen = last_seen_result[0]
                        if datetime.datetime.now() - last_seen > datetime.timedelta(days=15):
                            # Skip unsuspension due to inactivity
                            continue
            except Exception:
                # Fail-open to existing behavior if any error occurs
                pass

            # Check if user has enough credits for this server
            if remaining_credits >= hourly_cost:
                # User can afford this server, unsuspend it
                webhook_log(f"Unsuspending server {server_name} (ID: {server_id}) for user {user_email} (has {remaining_credits:.2f} credits)", database_log=True)
                threading.Thread(target=unsuspend_server, args=(server_id,), kwargs={"automatic": True}).start()
                
                # Deduct credits for this server
                remaining_credits -= hourly_cost
            elif suspension["source"] == "billing":
                # Only billing suspensions use the billing deletion deadline.
                try:
                    suspended_at = server['attributes']['updated_at']
                    suspension_time = datetime.datetime.strptime(suspended_at, "%Y-%m-%dT%H:%M:%S+00:00")
                    suspension_duration = datetime.datetime.now() - suspension_time

                    if suspension_duration.days > 3:
                        webhook_log(f"Deleting server {server_name} (ID: {server_id}) due to suspension for more than 3 days", database_log=True)
                        threading.Thread(target=delete_server, args=(server_id,)).start()
                except (ValueError, KeyError) as e:
                    threading.Thread(target=webhook_log, args=(f"Error processing suspension duration for server {server_id}: {str(e)}", 1)).start()

def delete_suspended_users_servers():
    """
    Scheduled task that deletes servers of suspended users.
    
    Process:
    1. Gets all servers
    2. Checks if each server's owner is suspended
    3. Deletes servers of suspended users
    
    Returns:
        None
    """
    # Get all servers from Pterodactyl
    try:
        response = safe_requests.get(f"{PTERODACTYL_URL}api/application/servers?per_page=100000", headers=HEADERS, timeout=60)
    except requests.exceptions.RequestException as e:
        print(f"Skipping suspended users check: Pterodactyl panel timeout or connection error ({e})")
        return

    if response.status_code != 200:
        threading.Thread(target=webhook_log, args=(f"Failed to get servers for suspension check: {response.status_code}", 2)).start()
        return
        
    servers = response.json()
    if 'data' not in servers:
        return
    
    # Cache user suspension status
    suspension_status = {}
    
    # Process all servers to check for suspended users
    for server in servers['data']:
        user_id = server['attributes']['user']
        server_id = server['attributes']['id']
        server_name = server['attributes']['name']
        
        # Check user suspension only once per user
        if user_id not in suspension_status:
            suspension_status[user_id] = check_if_user_suspended(user_id)
        
        # Delete servers of suspended users
        if suspension_status[user_id]:
            threading.Thread(target=delete_server, args=(server_id,)).start()
            threading.Thread(target=webhook_log, args=(f"Server {server_name} (ID: {server_id}) deleted due to user suspension", 1)).start()
