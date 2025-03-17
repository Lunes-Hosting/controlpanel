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
from managers.database_manager import DatabaseManager
from config import PTERODACTYL_URL, PTERODACTYL_ADMIN_KEY
from products import products
from .logging import webhook_log
from .server_manager import suspend_server, unsuspend_server, delete_server
from .user_manager import check_if_user_suspended
from security import safe_requests

# API authentication headers
HEADERS = {
    "Authorization": f"Bearer {PTERODACTYL_ADMIN_KEY}",
    'Accept': 'application/json',
    'Content-Type': 'application/json'
}

def add_credits(email: str, amount: int, set_client: bool = True):
    """
    Adds credits to a user's account.
    
    Process:
    1. Gets current credits from database
    2. Adds specified amount
    3. Updates database
    4. Optionally sets user role to 'client'
    
    Args:
        email: User's email
        amount: Number of credits to add
        set_client: Whether to set user role to 'client'
    
    Returns:
        None
    """
    # Get current credits
    query = "SELECT credits FROM users WHERE email = %s"
    result = DatabaseManager.execute_query(query, (email,))
    
    if result:
        current_credits = float(result[0])
        new_credits = current_credits + amount
        
        # Update credits
        update_query = "UPDATE users SET credits = %s WHERE email = %s"
        DatabaseManager.execute_query(update_query, (float(new_credits), email))
        
        # Optionally set role to client
        if set_client:
            role_query = "UPDATE users SET role = 'client' WHERE email = %s"
            DatabaseManager.execute_query(role_query, (email,))
            
        threading.Thread(target=webhook_log, args=(f"Added {amount} credits to {email}. New balance: {new_credits}", 0)).start()

def remove_credits(email: str, amount: float):
    """
    Removes credits from a user's account.
    
    Process:
    1. Gets current credits
    2. If user has enough credits:
        - Subtracts amount
        - Updates database
    3. If not enough credits:
        - Returns "SUSPEND"
    
    Args:
        email: User's email
        amount: Number of credits to remove
    
    Returns:
        "SUSPEND": If user doesn't have enough credits
        None: If credits successfully removed
    """
    # Get current credits
    query = "SELECT credits FROM users WHERE email = %s"
    result = DatabaseManager.execute_query(query, (email,))
    
    if result:
        current_credits = float(result[0])
        if current_credits >= amount:
            new_credits = current_credits - amount
            
            # Update credits
            update_query = "UPDATE users SET credits = %s WHERE email = %s"
            DatabaseManager.execute_query(update_query, (float(new_credits), email))
            
            return None
        else:
            return "SUSPEND"
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
    response = safe_requests.get(f"{PTERODACTYL_URL}api/application/servers?per_page=100000", headers=HEADERS, timeout=60)
    if response.status_code != 200:
        threading.Thread(target=webhook_log, args=(f"Failed to get servers for credit processing: {response.status_code}", 2)).start()
        return
        
    servers = response.json()
    if 'data' not in servers:
        threading.Thread(target=webhook_log, args=("No servers found for credit processing", 1)).start()
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
            threading.Thread(target=webhook_log, args=(f"Failed to get user {user_id} for credit processing: {user_response.status_code}", 2)).start()
            continue
            
        user_email = user_response.json()['attributes']['email']
        
        # Get current user credits
        user_credits = float(get_credits(user_email))
        remaining_credits = user_credits
        
        # Process each server individually
        for server in servers:
            # Skip suspended servers
            if server['attributes']['suspended']:
                continue
                
            # Get product info based on server specs
            product = convert_to_product(server)
            hourly_cost = float(product['price'])/30.0/24.0
            
            # Check if user has enough credits for this server
            if remaining_credits >= hourly_cost:
                # User can afford this server, deduct credits
                remaining_credits -= hourly_cost
            else:
                # User can't afford this server, suspend it
                server_id = server['attributes']['id']
                server_name = server['attributes']['name']
                threading.Thread(target=webhook_log, args=(f"User {user_email} can't afford server {server_name} (ID: {server_id}). Suspending.", 1)).start()
                threading.Thread(target=suspend_server, args=(server_id,)).start()
        
        # Update user's credits with the remaining amount
        if user_credits > 0:
            # Calculate how many credits were used
            credits_used = user_credits - remaining_credits
            if credits_used > 0:
                # Update database with new credit amount
                update_query = "UPDATE users SET credits = %s WHERE email = %s"
                DatabaseManager.execute_query(update_query, (float(remaining_credits), user_email))

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
    response = safe_requests.get(f"{PTERODACTYL_URL}api/application/servers?per_page=100000", headers=HEADERS, timeout=60)
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
        user_response = safe_requests.get(f"{PTERODACTYL_URL}api/application/users/{user_id}", headers=HEADERS, timeout=60)
        if user_response.status_code != 200:
            threading.Thread(target=webhook_log, args=(f"Failed to get user {user_id} for unsuspension check: {user_response.status_code}", 2)).start()
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
            
            # Get product info based on server specs
            product = convert_to_product(server)
            hourly_cost = float(product['price'])/30.0/24.0
            
            # Check if user has enough credits for this server
            if remaining_credits >= hourly_cost:
                # User can afford this server, unsuspend it
                threading.Thread(target=webhook_log, args=(f"Unsuspending server {server_name} (ID: {server_id}) for user {user_email} (has {remaining_credits:.2f} credits)", 1)).start()
                threading.Thread(target=unsuspend_server, args=(server_id,)).start()
                
                # Deduct credits for this server
                remaining_credits -= hourly_cost


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
    response = safe_requests.get(f"{PTERODACTYL_URL}api/application/servers?per_page=100000", headers=HEADERS, timeout=60)
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
