"""
Core functionality module for the Pterodactyl Control Panel.
This module serves as the central interface between the web application and both the Pterodactyl API 
and the local database. It handles all core operations including user management, server provisioning,
credit system, and automated maintenance tasks.

Architecture Overview:
    - Web Layer: Flask routes interface with this module for all core operations
    - Database Layer: MySQL database stores user data, credits, and application state
    - API Layer: Communicates with Pterodactyl API for server management
    - Cache Layer: Uses PteroCache for optimizing API calls
    - Task Layer: Runs scheduled tasks for credit processing and server maintenance

Key Components:
    1. User Management:
        - Registration/Authentication
        - Credit system
        - Role management (admin/client)
        - Email verification
        - Password reset
    
    2. Server Management:
        - Server provisioning
        - Suspension/Unsuspension
        - Resource allocation
        - Node management
        - Server transfers
    
    3. Credit System:
        - Credit tracking
        - Automated usage calculation
        - Payment processing integration
    
    4. Security:
        - Password hashing (bcrypt)
        - Session management
        - IP tracking
        - Admin access control

Database Schema:
    users table:
        - id: int (primary key)
        - name: str
        - email: str (unique)
        - password: str (bcrypt hashed)
        - pterodactyl_id: int
        - credits: int
        - role: str
        - ip: str
        - email_verified_at: datetime
        - last_seen: datetime

Dependencies:
    - flask: Web framework
    - mysql-connector: Database interface
    - bcrypt: Password hashing
    - requests: HTTP client for API calls
    - flask_mail: Email functionality
    - pterocache: Custom caching layer

Environment Variables Required:
    - PTERODACTYL_URL: Base URL for Pterodactyl panel
    - PTERODACTYL_ADMIN_KEY: Admin API key
    - PTERODACTYL_ADMIN_USER_KEY: Client API key
    - DATABASE: MySQL database name
    - MAIL_* settings for email configuration

Constants:
    HEADERS: API headers for admin access
    CLIENT_HEADERS: API headers for client access
"""

import datetime
import time
import string
import threading
import sys
from pterocache import PteroCache
import bcrypt
import mysql.connector
# Establish a connection to the database
import mysql.connector
import requests
from flask import url_for, redirect, current_app, Flask, request, session, render_template
from functools import wraps

import logging
from werkzeug.datastructures.headers import EnvironHeaders
from managers.database_manager import DatabaseManager
from threadedreturn import ThreadWithReturnValue
from config import *
from products import products
import secrets
import random
import json
from flask_mail import Mail, Message

cache = PteroCache()

# API authentication headers
HEADERS = {
    "Authorization": f"Bearer {PTERODACTYL_ADMIN_KEY}",
    'Accept': 'application/json',
    'Content-Type': 'application/json'
}

CLIENT_HEADERS = {
    "Authorization": f"Bearer {PTERODACTYL_ADMIN_USER_KEY}",
    'Accept': 'application/json',
    'Content-Type': 'application/json'
}


def sync_users_script():
    """
    Handles periodic maintenance tasks:
    1. Process pending deletions after 30 days
    2. Reset passwords for inactive users (180+ days)
    """
    db = DatabaseManager()
    
    # Process pending deletions after 30 days
    results = db.execute_query("SELECT * FROM pending_deletions", fetch_all=True)
    if results:
        for user in results:
            email = user[1]
            request_time = user[2]
            
            if datetime.datetime.now() - request_time > datetime.timedelta(days=30):
                webhook_log(f"Processing pending deletion for {email} after 30 days")
                
                # First verify the user still exists
                user_exists = db.execute_query("SELECT * FROM users WHERE email = %s", (email,))
                if not user_exists:
                    webhook_log(f"User {email} already deleted, cleaning up pending deletion entry")
                    db.execute_query("DELETE FROM pending_deletions WHERE email = %s", (email,))
                    continue
                
                try:
                    # Get Pterodactyl ID before deletion
                    ptero_id = get_ptero_id(email)
                    if not ptero_id:
                        webhook_log(f"User {email} not found in Pterodactyl, cleaning up pending deletion entry")
                        db.execute_query("DELETE FROM pending_deletions WHERE email = %s", (email,))
                        continue
                        
                    # Try to delete from Pterodactyl first
                    response = requests.delete(f"{PTERODACTYL_URL}api/application/users/{ptero_id[0]}", headers=HEADERS, timeout=60)
                    if response.status_code != 204:
                        webhook_log(f"Failed to delete {email} from Pterodactyl - Status: {response.status_code}", 2)
                        continue
                        
                    # If Pterodactyl deletion succeeded, delete locally
                    user_id = db.execute_query("SELECT id FROM users WHERE email = %s", (email,))[0]
                    
                    # Delete user's tickets and comments
                    db.execute_query("DELETE FROM ticket_comments WHERE user_id = %s", (user_id,))
                    db.execute_query("DELETE FROM tickets WHERE user_id = %s", (user_id,))
                    
                    # Finally delete the user
                    db.execute_query("DELETE FROM users WHERE id = %s", (user_id,))
                    
                    # Clean up pending deletion entry
                    db.execute_query("DELETE FROM pending_deletions WHERE email = %s", (email,))
                    
                    webhook_log(f"Successfully processed pending deletion for {email}")
                    
                except Exception as e:
                    webhook_log(f"Error processing pending deletion for {email}: {str(e)}", 2)
    
    # Reset passwords for long-inactive users
    query = f"SELECT last_seen, email FROM users"
    result = db.execute_query(query, fetch_all=True)
    if result:
        for last_seen, email in result:
            if last_seen is not None:
                if datetime.datetime.now() - last_seen > datetime.timedelta(days=180):
                    try:
                        webhook_log(f"Resetting password for inactive user {email}")
                        new_password = secrets.token_hex(32)
                        hashed_password = bcrypt.hashpw(new_password.encode('utf-8'), bcrypt.gensalt(rounds=14))
                        db.execute_query("UPDATE users SET password = %s WHERE email = %s", (hashed_password, email))
                        
                        # Update Pterodactyl password if possible
                        ptero_id = get_ptero_id(email)
                        if ptero_id:
                            info = requests.get(f"{PTERODACTYL_URL}api/application/users/{ptero_id[0]}", headers=HEADERS, timeout=60).json()['attributes']
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
                        webhook_log(f"Error resetting password for {email}: {str(e)}", 2)


def get_nodes(all: bool = False) -> list[dict]:
    """
    Returns cached list of available nodes from Pterodactyl.
    
    Returns:
        list[dict]: List of node information with format:
            {
                "node_id": int,
                "name": str
            }
    """
    if all:
        return cache.all_nodes
    else:
        return cache.available_nodes


def get_eggs() -> list[dict]:
    """
    Returns cached list of server eggs from Pterodactyl.
    
    Returns:
        list[dict]: List of egg information with format:
            {
                "egg_id": int,
                "name": str,
                "docker_image": str,
                "startup": str
            }
    """
    return cache.egg_cache

def get_autodeploy_info(project_id: int) -> list[dict]:
    res = DatabaseManager().execute_query("SELECT * FROM projects WHERE id = %s", (project_id,))
    if res is not None:
        egg_id = res[8]
        egg_info = requests.get(f"{PTERODACTYL_URL}/api/application/nests/{AUTODEPLOY_NEST_ID}/eggs/{egg_id}?include=variables", 
            headers={"Authorization": f"Bearer {PTERODACTYL_ADMIN_KEY}"}).json()
        attributes = egg_info['attributes']
        
        # Convert res[7] to a dictionary
        try:
            environment = json.loads(res[7]) if res[7] else {}  # Ensure it's a dict
        except json.JSONDecodeError:
            environment = {}  # Fallback to empty dict if parsing fails
        
        return [{"egg_id": attributes['id'], "name": attributes['name'], "docker_image": attributes['docker_image'],
                "startup": attributes['startup']}], environment
    

def improve_list_servers(pterodactyl_id: int = None) -> tuple[dict]:
    """
    
    Example Response:
    {
        "object": "user",
        "attributes": {
            "id": 2,
            "external_id": null,
            "uuid": "4cbba6d2-923b-4630-b7ac-8b9dcf204bb0",
            "username": "gustavo_fring",
            "email": "frostyornot@gmail.com",
            "first_name": "gustavo_fring",
            "last_name": "gustavo_fring",
            "language": "en",
            "root_admin": true,
            "2fa": false,
            "created_at": "2022-09-28T20:52:34+00:00",
            "updated_at": "2024-07-10T03:29:42+00:00",
            "relationships": {
            "servers": {
                "object": "list",
                "data": [
                {
                    "object": "server",
                    "attributes": {
                    "id": 2765,
                    "external_id": null,
                    "uuid": "01ed4077-d73e-4988-8e62-370a77df3fe6",
                    "identifier": "01ed4077",
                    "name": "</h1>",
                    "description": "",
                    "status": null,
                    "suspended": false,
                    "limits": {
                        "memory": 512,
                        "swap": 0,
                        "disk": 0,
                        "io": 500,
                        "cpu": 0,
                        "threads": null,
                        "oom_disabled": true
                    },
                    "feature_limits": {
                        "databases": 0,
                        "allocations": 0,
                        "backups": 0
                    },
                    "user": 2,
                    "node": 11,
                    "allocation": 8105,
                    "nest": 6,
                    "egg": 16,
                    "container": {
                        "startup_command": "{{STARTUP_COMMAND}}",
                        "image": "ghcr.io/parkervcp/yolks:python_3.11",
                        "installed": 1,
                        "environment": {
                        "STARTUP_COMMAND": "eval git reset --hard HEAD && git checkout main && git pull && pip install -r requirements.txt -U && python3 main.py",
                        "STARTUP": "{{STARTUP_COMMAND}}",
                        "P_SERVER_LOCATION": "US",
                        "P_SERVER_UUID": "01ed4077-d73e-4988-8e62-370a77df3fe6",
                        "P_SERVER_ALLOCATION_LIMIT": 0
                        }
                    },
                    "updated_at": "2024-08-31T02:42:51+00:00",
                    "created_at": "2023-10-30T01:48:27+00:00"
                    }
                }
                ]
            }
            }
        }
    }
    """
    resp = requests.get(
        f"{PTERODACTYL_URL}api/application/users/{int(pterodactyl_id)}?include=servers", 
        headers=HEADERS, 
    timeout=60).json()

    relationship = resp["attributes"]["relationships"]["servers"]["data"]

    # Using list comprehension for faster filtering
    server_list = [server for server in relationship if int(server["attributes"]["user"]) == pterodactyl_id]

    return (server_list)




def get_server_information(server_id: int) -> dict:
    """
    Returns dictionary of server information from pterodactyl api.
    
    Makes a GET request to /api/application/servers/{server_id} to fetch specific server details.
    
    Example Response:
    {
        "attributes": {
            "id": 1,
            "external_id": null,
            "uuid": "uuid-string",
            "identifier": "identifier-string",
            "name": "Server Name",
            "description": "Server Description",
            "status": "installing",
            "suspended": false,
            "limits": {
                "memory": 1024,
                "swap": 0,
                "disk": 10240,
                "io": 500,
                "cpu": 100
            },
            "feature_limits": {
                "databases": 5,
                "allocations": 5,
                "backups": 2
            },
            "user": 1,
            "node": 1,
            "allocation": 1,
            "nest": 1,
            "egg": 1
        }
    }
    """
    response = requests.get(f"{PTERODACTYL_URL}api/application/servers/{server_id}", headers=HEADERS, timeout=60)
    return response.json()


def get_ptero_id(email: str) -> tuple[int] | None:
    """
    Gets Pterodactyl ID for a user by their email.
    
    Args:
        email: User's email address
    
    Returns:
        tuple[int]: Tuple containing Pterodactyl ID at index 0
        None: If user not found
    """
    db = DatabaseManager()
    res = db.execute_query("SELECT pterodactyl_id FROM users WHERE email = %s", (email,))
    if res is None:
        return None
    return res

def get_id(email: str) -> tuple[int] | None:
    """
    Gets user ID for a user by their email.
    
    Args:
        email: User's email address
    
    Returns:
        tuple[int]: Tuple containing user ID at index 0
        None: If user not found
    """
    db = DatabaseManager()
    res = db.execute_query("SELECT id FROM users WHERE email = %s", (email,))
    if res is None:
        return None
    return res

def get_name(user_id: int) -> tuple[str] | None:
    """
    Gets username for a user by their ID.
    
    Args:
        user_id: User's ID
    
    Returns:
        tuple[str]: Tuple containing username at index 0
        None: If user not found
    """
    db = DatabaseManager()
    res = db.execute_query("SELECT name FROM users WHERE id = %s", (user_id,))
    if res is None:
        return None
    return res

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
    webhook_log(f"Login attempt with email {email} ip: {ip}")
    db = DatabaseManager()
    hashed_password = db.execute_query("SELECT password FROM users WHERE LOWER(email) = LOWER(%s)", (email,))

    if hashed_password is not None:
        # Verify the password
        is_matched = bcrypt.checkpw(password.encode('utf-8'), hashed_password[0].encode('utf-8'))

        if is_matched:
            # Retrieve all information of the user
            info = db.execute_query("SELECT * FROM users WHERE LOWER(email) = LOWER(%s)", (email,))
            is_pending_deletion = db.execute_query("SELECT * FROM pending_deletions WHERE email = %s", (email,))

            if is_pending_deletion is not None:
                send_email(email, "Account Reactivated", "Your account has been reactivated!", current_app._get_current_object())
                db.execute_query("DELETE FROM pending_deletions WHERE email = %s", (email,))
            return info

    return None


def register(email: str, password: str, name: str, ip: str) -> str | dict:
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
    email = email.strip().lower()
    name = name.strip().lower()
    salt = bcrypt.gensalt(rounds=14)
    passthread = ThreadWithReturnValue(target=bcrypt.hashpw, args=(password.encode('utf-8'), salt))
    passthread.start()

    resp_emails = requests.get("https://lunes.host/blockedemails.txt")
    blocked_emails = [line.strip() for line in resp_emails.text.splitlines() if line.strip()]
    banned_emails = set(blocked_emails)
        
    if "+" in email or any(banned in email for banned in banned_emails):
        webhook_log(f"Failed to register email {email} with IP {ip} due to email blacklist", non_embed_message="<@491266830674034699>")
        session['suspended'] = True
        return "Failed to register due to blacklist! Contact panel@lunes.host if this is a mistake"
    webhook_log(f"User with email: {email}, name: {name} ip: {ip} registered")
    

    db = DatabaseManager()
    results = db.execute_query("SELECT * FROM users WHERE ip = %s", (ip,))

    if results is not None:
        return "Ip is already registered"

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
        user_id = db.execute_query("SELECT * FROM users ORDER BY id DESC LIMIT 0, 1")[0] + 1
        query = ("INSERT INTO users (name, email, password, id, pterodactyl_id, ip, credits) VALUES (%s, %s, %s, %s, %s, %s, %s)")
        password_hash = passthread.join()
        values = (name, email, password_hash, user_id, data['attributes']['id'], ip, 25)
        db.execute_query(query, values)
        return response.json()


def instantly_delete_user(email: str, skip_email: bool = False) -> int:
    """
    Deletes a user from both the panel database and Pterodactyl.
    
    Args:
        email: User's email address
        skip_email: If True, won't send deletion email (used during sync)
    
    Returns:
        int: HTTP status code from deletion request
    """
    db = DatabaseManager()
    ptero_id = get_ptero_id(email)
    if not ptero_id:
        return 404
        
    user_id = get_id(email)
    if not user_id:
        return 404
        
    ptero_id = ptero_id[0]
    user_id = user_id[0]
    
    # Delete all user's servers first
    servers = improve_list_servers(ptero_id)
    if servers:
        for server in servers:
            server_id = server['attributes']['id']
            delete_server(server_id)
    
    # Delete user's tickets and comments
    db.execute_query("DELETE FROM ticket_comments WHERE user_id = %s", (user_id,))
    db.execute_query("DELETE FROM tickets WHERE user_id = %s", (user_id,))
    
    # Delete the user
    db.execute_query("DELETE FROM users WHERE id = %s", (user_id,))
    
    # Only send email if not skipped
    if not skip_email:
        try:
            send_email(email, "Account Deleted", "Your account has been deleted!", current_app._get_current_object())
        except RuntimeError:
            # Skip email if no application context
            pass
    
    # Delete from Pterodactyl last
    try:
        response = requests.delete(f"{PTERODACTYL_URL}api/application/users/{ptero_id}", headers=HEADERS, timeout=60)
        return response.status_code
    except requests.exceptions.RequestException:
        return 500


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
    db = DatabaseManager()
    current_credits = db.execute_query("SELECT credits FROM users WHERE email = %s", (email,))
    query = f"UPDATE users SET credits = {int(current_credits[0]) + amount} WHERE email = %s"
    db.execute_query(query, (email,))
    
    if set_client:
        query = f"UPDATE users SET role = 'client' WHERE email = %s"
        db.execute_query(query, (email,))


def remove_credits(email: str, amount: float) -> str | None:
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
    db = DatabaseManager()
    current_credits = db.execute_query("SELECT credits FROM users WHERE email = %s", (email,))
    new_credits = float(current_credits[0]) - amount
    if new_credits <= 0:
        return "SUSPEND"
    query = f"UPDATE users SET credits = {new_credits} WHERE email = %s"
    db.execute_query(query, (email,))
    return None


def convert_to_product(data) -> dict:
    """
    Returns Product with matched MEMORY count all other fields ignored.
    
    Args:
        data: Server data
    
    Returns:
        dict: Product information
    """
    returned = None
    for product in products:
        if int(product['limits']['memory']) == int(data['attributes']['limits']['memory']):
            returned = product
            break

    if returned is None:
        # print(data['attributes']['limits']['memory'], products)
        pass
    return returned


def suspend_server(server_id: int):
    """
    Suspends a server through Pterodactyl API.
    Typically called when user runs out of credits.
    
    Args:
        server_id: Pterodactyl server ID
    
    Returns:
        None
    """
    requests.post(f"{PTERODACTYL_URL}api/application/servers/{server_id}/suspend", headers=HEADERS, timeout=60)


def use_credits():
    """
    Scheduled task that processes credit usage for all servers.
    
    Process:
    1. Gets all servers
    2. For each server:
        - Gets server details
        - Calculates credit cost based on resources
        - Attempts to remove credits from owner
        - Suspends server if not enough credits
    
    Returns:
        None
    """
    response = requests.get(f"{PTERODACTYL_URL}api/application/servers?per_page=10000", headers=HEADERS, timeout=60).json()

    for server in response['data']:

        product = convert_to_product(server)
        if product is not None:

            query = f"SELECT email FROM users WHERE pterodactyl_id='{int(server['attributes']['user'])}'"

            db = DatabaseManager()
            email = db.execute_query(query)

            if email is not None:
                if not server['attributes']['suspended']:
                    result = remove_credits(email[0], product['price'] / 30 / 24)
                    if result == "SUSPEND":
                        send_email(email[0], "Server Suspended", "Your server has been suspended due to lack of credits!", current_app._get_current_object())
                        suspend_server(server['attributes']['id'])

            else:
                print(email, product['price'])
        else:
            print(server['attributes']['name'])


def delete_server(server_id) -> int:
    """
    Tries to delete server returns status code.
    
    Args:
        server_id: Pterodactyl server ID
    
    Returns:
        int: HTTP status code
    """
    response = requests.delete(f"{PTERODACTYL_URL}api/application/servers/{server_id}", headers=HEADERS, timeout=60)
    if response.status_code == 204:
        webhook_log(f"Server {server_id} deleted successfully via Script delete_server function.", 0)
    else:
        webhook_log(f"Failed to delete server {server_id}. Status code: {response.status_code}", 1)
    return response.status_code


def unsuspend_server(server_id: int):
    """
    Un-suspends specific server id.
    
    Makes a POST request to /api/application/servers/{server_id}/unsuspend to unsuspend the server.
    
    Example Success: Returns 204 (No Content)
    Example Error Response:
    {
        "errors": [
            {
                "code": "NotFound",
                "status": "404",
                "detail": "The requested resource was not found on this server."
            }
        ]
    }
    """
    requests.post(f"{PTERODACTYL_URL}api/application/servers/{server_id}/unsuspend", headers=HEADERS, timeout=60)


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
    response = requests.get(f"{PTERODACTYL_URL}api/application/servers?per_page=10000", headers=HEADERS, timeout=60).json()
    
    for server in response['data']:
        user_suspended = check_if_user_suspended(server['attributes']['user'])
        if user_suspended:
            
            delete_server(server['attributes']['id'])
            webhook_log("Server deleted due to user suspension")
        
        product = convert_to_product(server)
        if product is None:
            webhook_log(f"```{server}``` no product")
        #           server_id = server['attributes']['id']
            print(server['attributes']['name'], None)
            resp = requests.get(f"{PTERODACTYL_URL}api/application/servers/{int(server['attributes']['id'])}", headers=HEADERS, timeout=60).json()
            main_product = products[1]
            body = main_product['limits']
            body["feature_limits"] = main_product['product_limits']
            body['allocation'] = resp['attributes']['allocation']
            print(body)
            resp2 = requests.patch(f"{PTERODACTYL_URL}api/application/servers/{int(server['attributes']['id'])}/build", headers=HEADERS,
                                    json=body, timeout=60)
        if product is not None and product['name'] != "Free Tier":

            query = f"SELECT email FROM users WHERE pterodactyl_id='{int(server['attributes']['user'])}'"

            db = DatabaseManager()
            email = db.execute_query(query)
            
            query = f"SELECT credits FROM users WHERE pterodactyl_id='{int(server['attributes']['user'])}'"
            current_credits = db.execute_query(query)

            if email is None or current_credits is None:
                pass
            if email is not None:
                if server['attributes']['suspended']:
                    # print(server['attributes']['user'], "is suspeded", credits[0], product['price'] / 30/ 24)
                    if current_credits[0] >= product['price'] / 30 / 24:
                        if not check_if_user_suspended(server['attributes']['user']):
                            unsuspend_server(server['attributes']['id'])
                    else:
                        if server['attributes']['suspended']:

                            suspended_at = server['attributes']['updated_at']
                            suspension_duration = datetime.datetime.now() - datetime.datetime.strptime(suspended_at,
                                                                                                       "%Y-%m-%dT%H"
                                                                                                       ":%M:%S+00:00")

                            if suspension_duration.days > 3:
                                print(
                                    f"Deleting server {server['attributes']['name']} due to suspension for more than "
                                    f"3 days.")
                                webhook_log(f"Deleting server {server['attributes']['name']} with id: {server['attributes']['id']} due to suspension for more than 3 days.")

                                delete_server(server['attributes']['id'])

            else:
                print(email, product['price'])
        elif product is not None:
            if product['name'] == "Free Tier":
                query = f"SELECT last_seen, email FROM users WHERE pterodactyl_id='{int(server['attributes']['user'])}'"
                db = DatabaseManager()
                last_seen, email = db.execute_query(query)
                
                if last_seen is not None:
                    if datetime.datetime.now() - last_seen > datetime.timedelta(days=30):
                        webhook_log(
                            f"Deleting server {server['attributes']['name']} due to inactivity for more than 30 days.")
                        delete_server(server['attributes']['id'])
                    else:
                        if not check_if_user_suspended(server['attributes']['user']):
                            unsuspend_server(server['attributes']['id'])
                else:
                    update_last_seen(email)


def account_get_information(email: str):
    query = f"SELECT credits, pterodactyl_id, name, email_verified_at, suspended FROM users WHERE email = %s"
    
    db = DatabaseManager()
    information = db.execute_query(query, (email,))

    verified = False
    if information[3] is not None:
        verified = True
    return information[0], information[1], information[2], verified, information[4] == 1

def get_credits(email: str) -> int:
    """
    Returns int of amount of credits in database.
    
    Args:
        email: User's email
    
    Returns:
        int: Credits amount
    """
    query = f"SELECT credits FROM users WHERE email = %s"
    db = DatabaseManager()
    current_credits = db.execute_query(query, (email,))

    return current_credits[0]


def check_if_user_suspended(pterodactyl_id: str) -> bool | None:
    """
    Returns the bool value of if a user is suspended, if user is not found with the pterodactyl id it returns None
    
    Args:
        pterodactyl_id: Pterodactyl user ID
    
    Returns:
        bool: Whether user is suspended
        None: If user not found
    """
    db = DatabaseManager()
    suspended = db.execute_query(f"SELECT suspended FROM users WHERE pterodactyl_id = %s", (pterodactyl_id,))
    to_bool = {0: False, 1: True}
    if suspended is None:
        return True

    return to_bool[suspended[0]]


def get_user_verification_status_and_suspension_status(email):
    result = DatabaseManager.execute_query(
        "SELECT email_verified_at, suspended FROM users WHERE email = %s",
        (email,)
    )

    if result:
        verified = False
        if result[0] is not None:
            verified = True

        return (verified, result[1] == 1)
    
    return (None, None)


#print(get_user_verification_status_and_suspension_status("frostyornot@gmail.com"))
#print(check_if_user_suspended(2))

def update_ip(email: str, real_ip: str):
    """
    Updates the ip by getting the header with key "CF-Connecting-IP" default is "localhost".
    
    Args:
        email: User's email
        real_ip: The real IP address of the user.
    
    Returns:
        None
    """
    if real_ip != "localhost":
        query = f"UPDATE users SET ip = '{real_ip}' where email = %s"

        db = DatabaseManager()
        db.execute_query(query, (email,))



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
    db = DatabaseManager()
    if everyone is True:
        query = f"UPDATE users SET last_seen = '{datetime.datetime.now()}'"
        db.execute_query(query)
    else:
        query = f"UPDATE users SET last_seen = '{datetime.datetime.now()}' WHERE email = %s"
        db.execute_query(query, (email,))


def get_last_seen(email: str) -> datetime.datetime:
    """
    Returns datetime object of when user with that email was last seen.
    
    Args:
        email: User's email
    
    Returns:
        datetime.datetime: Last seen time
    """
    query = f"SELECT last_seen FROM users WHERE email = %s"
    db = DatabaseManager()
    last_seen = db.execute_query(query, (email,))
    return last_seen[0]


def is_admin(email: str) -> bool:
    """
    Checks if user is an admin.
    
    Args:
        email: User's email
    
    Returns:
        bool: Whether user is an admin
    """
    query = "SELECT role FROM users WHERE email = %s"
    db = DatabaseManager()
    role = db.execute_query(query, (email,))
    return role[0] == "admin"


def get_db_connection(database=DATABASE):
    """
    Creates and returns a database connection with standard configuration.
    
    Args:
        database: Database name to connect to
        
    Returns:
        tuple: (connection, cursor)
    """
    # This function is deprecated, use DatabaseManager instead
    raise DeprecationWarning("This function is deprecated. Use DatabaseManager instead.")



STATUS_MAP = {
    -1: {"color": 0x95A5A6, "title": "No Code"},   # Gray
    0: {"color": 0x3498DB, "title": "Info"},      # Blue
    1: {"color": 0xF1C40F, "title": "Warning"}, # Yellow
    2: {"color": 0xE74C3C, "title": "Error"},    # Red
}

logger = logging.getLogger(__name__)

def webhook_log(embed_message: str, status: int = -1, non_embed_message: str = None):
    """
    Sends a log message to a Discord webhook with formatting.

    Args:
        message: Message to send.
        status: Status of Message (-1: Debug, 0: Info, 1: Warning, 2: Error).

    Returns:
        None
    """

    status_info = STATUS_MAP.get(status, STATUS_MAP[-1])
    # Log locally
    
    logger.info(embed_message)

    # Create Discord embed
    embed = {
        "title": f"**{status_info['title']} Log**",
        "description": embed_message,
        "color": status_info["color"],
        "footer": {"text": f"Logged at {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}"}
    }

    payload = {
        "content" : non_embed_message,
        "username": "Webhook Logger",
        "embeds": [embed]
    }

    # Send to Discord webhook
    try:
        resp = requests.post(WEBHOOK_URL, json=payload)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error(f"Failed to send webhook log: {e}")


def send_email(email: str, title:str, message: str, inner_app):
    """
    Sends an email to the user asynchronously using APScheduler.
    
    Args:
        email: User's email
        title: Email title
        message: Email message
        inner_app: Flask app
    
    Returns:
        None
    """
    def _send_email_task():
        with inner_app.app_context():
            mail = Mail(inner_app)
            print("started emails")
            msg = Message(title, recipients=[email])
            msg.body = message
            mail.send(msg)
            print(f"sent email to {email}")
    
    # Add task to scheduler
    scheduler = inner_app.apscheduler
    scheduler.add_job(
        func=_send_email_task,
        trigger='date',
        id=f'send_email_{email}_{int(time.time())}',
        run_date=datetime.datetime.now()
    )

# Function to generate a verification token
def generate_verification_token():
    """
    Generates a verification token.
    
    Returns:
        str: Verification token
    """
    return ''.join(secrets.SystemRandom().choices(string.ascii_letters + string.digits, k=20))


# Function to send a verification email
def send_verification_email(email: str, verification_token: str, inner_app):
    """
    Sends a verification email to the user.
    
    Args:
        email: User's email
        verification_token: Verification token
        inner_app: Flask app
    
    Returns:
        None
    """
    send_email(email, "Email Verification", f'Please click the link below to verify your email\
        :\n\n {HOSTED_URL}verify_email/{verification_token}', inner_app)

def send_reset_email(email: str, reset_token: str, inner_app):
    """
    Sends a password reset email to the user.
    
    Args:
        email: User's email
        reset_token: Reset token
        inner_app: Flask app
    
    Returns:
        None
    """
    send_email(email, "Password Reset Request", f'Please click the link below to reset your password\
        :\n\n {HOSTED_URL}reset_password/{reset_token}', inner_app)


# Function to generate a random reset token
def generate_reset_token():
    """
    Generates a reset token.
    
    Returns:
        str: Reset token
    """
    return ''.join(secrets.SystemRandom().choices(string.ascii_letters + string.digits, k=20))


def get_node_allocation(node_id: int) -> int | None:
    """
    Gets random allocation for a specific node.
    
    Args:
        node_id: ID of the node
    
    Returns:
        int: Random available allocation ID
        None: If no free allocation found
    """
    response = requests.get(f"{PTERODACTYL_URL}api/application/nodes/{node_id}/allocations?per_page=10000", headers=HEADERS, timeout=60)
    if response.status_code != 200:
        webhook_log(f"Failed to get allocations for node {node_id} - Status: {response.status_code}", 2)
        return None
        
    data = response.json()
    try:
        allocations = []
        for allocation in data['data']:
            if allocation['attributes']['assigned'] is False:
                allocations.append(allocation['attributes']['id'])
        if len(allocations) == 0:
            webhook_log(f"No free allocations found on node {node_id}", 2)
            return None
        return random.choice(allocations)
    except (KeyError, ValueError) as e:
        webhook_log(f"Error parsing allocations for node {node_id}: {str(e)}", 2)
        return None

def transfer_server(server_id: int, target_node_id: int) -> int:
    """
    Transfer a server to a new node using Pterodactyl API.
    
    Args:
        server_id (int): ID of the server to transfer
        target_node_id (int): ID of the target node
    
    Returns:
        int: HTTP status code from the transfer request
    """
    # Get server details
    server_info = get_server_information(server_id)
    if not server_info:
        webhook_log(f"Failed to get server info for server {server_id}", 2)
        return 404

    # Get allocation on target node
    allocation_id = get_node_allocation(target_node_id)
    if not allocation_id:
        webhook_log(f"Failed to get allocation for node {target_node_id} when transferring server {server_id}", 2)
        return 400  # No free allocation

    # Build transfer request
    transfer_data = {
        "allocation_id": allocation_id,
        "node_id": target_node_id
    }
    
    # Perform server transfer
    transfer_url = f"{PTERODACTYL_URL}api/application/servers/{server_id}/transfer"
    
    try:
        response = requests.post(
            transfer_url, 
            headers=HEADERS, 
            json=transfer_data, 
            timeout=60
        )
        
        # Log the response for debugging
        if response.status_code not in [202, 204]:
            print(f"Server transfer failed - Status: {response.status_code}, Response: {response.text}", 2)
        else:
            # Get the user who owns the server
            user_id = server_info['attributes']['user']
            print(f"User {user_id} transferred server {server_id} to node {target_node_id}")
        
        return response.status_code
    
    except Exception as e:
        print(f"Server transfer error for server {server_id}: {str(e)}", 2)
        return 500

def get_all_servers() -> list[dict]:
    """
    Returns list of all servers from Pterodactyl API with ?per_page=10000 parameter.
    
    Returns:
        list[dict]: List of server information
    """
    response = requests.get(f"{PTERODACTYL_URL}api/application/servers?per_page=10000", headers=HEADERS, timeout=60)
    data = response.json()
    return data['data']



def get_all_servers() -> list[dict]:
    """
    Returns list of all servers from Pterodactyl API with ?per_page=10000 parameter.
    
    Returns:
        list[dict]: List of server information
    """
    response = requests.get(f"{PTERODACTYL_URL}api/application/servers?per_page=10000", headers=HEADERS, timeout=60)
    data = response.json()
    return data['data']


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("email"):  # If user is not logged in
            session["next"] = request.url  # Store the attempted URL
            return redirect(url_for("user.login_user"))  # Redirect to login page
        email = session['email']
        t1 = threading.Thread(target=update_last_seen, args=(email,), daemon=True)

        # Extract the real IP from request context before starting the thread
        real_ip = request.headers.get('CF-Connecting-IP', 'localhost')
        t2 = threading.Thread(target=update_ip, args=(email, real_ip), daemon=True)

        ptero_id = get_ptero_id(session['email'])
        session['pterodactyl_id'] = ptero_id

        t1.start()
        t2.start()

        # Handle random_id generation if not set
        random_id = session.get("random_id")
        if random_id is None:
            characters = string.ascii_letters + string.digits
            random_string = ''.join(secrets.SystemRandom().choice(characters) for _ in range(50))
            session['random_id'] = random_string

        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("email"):  # Check if user is logged in
            session["next"] = request.url  # Store the attempted URL
            return redirect(url_for("user.login_user"))  # Redirect to login page

        if not is_admin(session["email"]):  # Check if user is an admin
            return render_template("admin/forbidden.html"), 403  # Return 403 Forbidden status

        email = session['email']
        t1 = threading.Thread(target=update_last_seen, args=(email,), daemon=True)

        # Extract the real IP from request context before starting the thread
        real_ip = request.headers.get('CF-Connecting-IP', 'localhost')
        t2 = threading.Thread(target=update_ip, args=(email, real_ip), daemon=True)

        ptero_id = get_ptero_id(session['email'])
        session['pterodactyl_id'] = ptero_id

        t1.start()
        t2.start()

        # Handle random_id generation if not set
        random_id = session.get("random_id")
        if random_id is None:
            characters = string.ascii_letters + string.digits
            random_string = ''.join(secrets.SystemRandom().choice(characters) for _ in range(50))
            session['random_id'] = random_string

        return f(*args, **kwargs)
    return decorated_function
