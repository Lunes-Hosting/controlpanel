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
import requests
from managers.database_manager import DatabaseManager
from config import PTERODACTYL_URL, PTERODACTYL_ADMIN_KEY
from products import products
from .logging import webhook_log
from .server_manager import suspend_server, unsuspend_server

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
        current_credits = int(result[0])
        new_credits = current_credits + amount
        
        # Update credits
        update_query = "UPDATE users SET credits = %s WHERE email = %s"
        DatabaseManager.execute_query(update_query, (new_credits, email))
        
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
        current_credits = int(result[0])
        if current_credits >= amount:
            new_credits = current_credits - amount
            
            # Update credits
            update_query = "UPDATE users SET credits = %s WHERE email = %s"
            DatabaseManager.execute_query(update_query, (new_credits, email))
            
            return None
        else:
            return "SUSPEND"
    return None

def get_credits(email: str):
    """
    Returns int of amount of credits in database.
    
    Args:
        email: User's email
    
    Returns:
        int: Credits amount
    """
    query = "SELECT credits FROM users WHERE email = %s"
    result = DatabaseManager.execute_query(query, (email,))
    if result:
        return int(result[0])
    return 0

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
        if product['memory'] == memory:
            return product
            
    # If no exact match, find closest match
    closest_product = min(products, key=lambda p: abs(p['memory'] - memory))
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
    response = requests.get(f"{PTERODACTYL_URL}api/application/servers", headers=HEADERS, timeout=60)
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
        user_response = requests.get(f"{PTERODACTYL_URL}api/application/users/{user_id}", headers=HEADERS, timeout=60)
        if user_response.status_code != 200:
            threading.Thread(target=webhook_log, args=(f"Failed to get user {user_id} for credit processing: {user_response.status_code}", 2)).start()
            continue
            
        user_email = user_response.json()['attributes']['email']
        
        # Calculate total credits needed for all servers
        total_credits_needed = 0
        for server in servers:
            # Skip suspended servers
            if server['attributes']['suspended']:
                continue
                
            # Get product info based on server specs
            product = convert_to_product(server)
            total_credits_needed += product['price_per_hour']
        
        # Remove credits if needed
        if total_credits_needed > 0:
            result = remove_credits(user_email, total_credits_needed)
            
            # If user doesn't have enough credits, suspend all servers
            if result == "SUSPEND":
                threading.Thread(target=webhook_log, args=(f"User {user_email} out of credits. Suspending servers.", 1)).start()
                for server in servers:
                    if not server['attributes']['suspended']:
                        server_id = server['attributes']['id']
                        threading.Thread(target=suspend_server, args=(server_id,)).start()

def check_to_unsuspend():
    """
    Scheduled task that checks for servers that can be unsuspended.
    
    Process:
    1. Gets all servers
    2. For each suspended server:
        - Checks if owner has required credits
        - Checks if owner qualifies for free tier
        - Unsuspends if either condition met
    
    Returns:
        None
    """
    # Get all servers from Pterodactyl
    response = requests.get(f"{PTERODACTYL_URL}api/application/servers", headers=HEADERS, timeout=60)
    if response.status_code != 200:
        threading.Thread(target=webhook_log, args=(f"Failed to get servers for unsuspension check: {response.status_code}", 2)).start()
        return
        
    servers = response.json()
    if 'data' not in servers:
        return
        
    # Check each suspended server
    for server in servers['data']:
        # Skip servers that are not suspended
        if not server['attributes']['suspended']:
            continue
            
        server_id = server['attributes']['id']
        user_id = server['attributes']['user']
        
        # Get user email from Pterodactyl
        user_response = requests.get(f"{PTERODACTYL_URL}api/application/users/{user_id}", headers=HEADERS, timeout=60)
        if user_response.status_code != 200:
            threading.Thread(target=webhook_log, args=(f"Failed to get user {user_id} for unsuspension check: {user_response.status_code}", 2)).start()
            continue
            
        user_email = user_response.json()['attributes']['email']
        
        # Get product info based on server specs
        product = convert_to_product(server)
        credits_needed = product['price_per_hour'] * 24  # Require 24 hours worth of credits
        
        # Check if user has enough credits
        user_credits = get_credits(user_email)
        
        if user_credits >= credits_needed:
            # User has enough credits, unsuspend server
            threading.Thread(target=webhook_log, args=(f"Unsuspending server {server_id} for user {user_email} (has {user_credits} credits)", 1)).start()
            threading.Thread(target=unsuspend_server, args=(server_id,)).start()
