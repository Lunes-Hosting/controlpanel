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
import string
import threading
import sys
from pterocache import *
import bcrypt
import mysql.connector
# Establish a connection to the database
import mysql.connector
import requests
from flask import url_for, redirect
from werkzeug.datastructures.headers import EnvironHeaders
from managers.database_manager import DatabaseManager
from threadedreturn import ThreadWithReturnValue
from config import *
from products import products
import secrets
import random
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
    Synchronizes users between Pterodactyl panel and local database.
    
    Process:
    1. Fetches all users from Pterodactyl API
    2. Gets all existing users from local DB in one query
    3. For each Pterodactyl user not in local DB:
        - Gets their password from panel
        - Creates new user ID
        - Inserts into local DB with default 25 credits
    """
    db = DatabaseManager()
    try:
        # Get all Pterodactyl users
        data = requests.get(f"{PTERODACTYL_URL}api/application/users?per_page=100000", headers=HEADERS).json()
        
        # Get all existing users from panel DB to prevent duplicates
        existing_users = db.execute_query("SELECT email FROM users", database="panel", fetch_all=True)
        existing_emails = {user[0].lower() for user in existing_users} if existing_users else set()
        
        for user in data['data']:
            user_email = user['attributes']['email'].lower()  # normalize email to lowercase
            if user_email not in existing_emails:
                print(f"Adding new user: {user_email}")
                try:
                    user_id = db.execute_query("SELECT * FROM users ORDER BY id DESC LIMIT 0, 1")[0] + 1
                    password = db.execute_query("select password from users where email = %s", (user_email,), database="panel")
                    if password:
                        query = ("INSERT INTO users (name, email, password, id, pterodactyl_id, credits) VALUES (%s, %s, %s, %s, %s, %s)")
                        values = (user['attributes']['username'], user_email, password[0], user_id, user['attributes']['id'], 25)
                        db.execute_query(query, values)
                except Exception as e:
                    print(f"Error adding user {user_email}: {str(e)}")
    except KeyError:
        print(data, "ptero user data")
        
        
    # reset old users passwords
    query = f"SELECT last_seen, email FROM users"
    result = db.execute_query(query, fetch_all=True)
    for last_seen, email in result:
        if last_seen is not None:
            if datetime.datetime.now() - last_seen > datetime.timedelta(days=180):
                webhook_log(f"Resetting password for {email}")
                new_password = secrets.token_hex(32)
                hashed_password = bcrypt.hashpw(new_password.encode('utf-8'), bcrypt.gensalt(rounds=14))
                db.execute_query("UPDATE users SET password = %s WHERE email = %s", (hashed_password, email))
                update_last_seen(email)
            


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

def list_servers(pterodactyl_id: int=None) -> list[dict]:
    """
    Returns list of dictionaries of servers with owner of that pterodactyl id.
    
    Makes a GET request to /api/application/servers to fetch all servers.
    
    Example Response:
    {
        "data": [
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
                    "egg": 1,
                    "container": {
                        "startup_command": "java -Xms128M -Xmx{{SERVER_MEMORY}}M -jar {{SERVER_JARFILE}}",
                        "image": "quay.io/pterodactyl/core:java",
                        "installed": true,
                        "environment": {
                            "SERVER_JARFILE": "server.jar",
                            "VANILLA_VERSION": "latest"
                        }
                    }
                }
            }
        ]
    }
    """
    try:
        response = requests.get(f"{PTERODACTYL_URL}api/application/servers?per_page=10000", headers=HEADERS)
        users_server = []
        data = response.json()
        if pterodactyl_id is not None:
            for server in data['data']:
                if server['attributes']['user'] == pterodactyl_id:
                    users_server.append(server)
            return users_server
        else:
            return data
    except KeyError as e:
        print(e, pterodactyl_id, data)
        return None

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
    response = requests.get(f"{PTERODACTYL_URL}api/application/servers/{server_id}", headers=HEADERS)
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

def login(email: str, password: str):
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
    webhook_log(f"Login attempt with email {email}")
    db = DatabaseManager()
    hashed_password = db.execute_query("SELECT password FROM users WHERE email = %s", (email,))

    if hashed_password is not None:
        # Verify the password
        is_matched = bcrypt.checkpw(password.encode('utf-8'), hashed_password[0].encode('utf-8'))

        if is_matched:
            # Retrieve all information of the user
            info = db.execute_query("SELECT * FROM users WHERE email = %s", (email,))
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
    salt = bcrypt.gensalt(rounds=14)
    passthread = ThreadWithReturnValue(target=bcrypt.hashpw, args=(password.encode('utf-8'), salt))
    passthread.start()

    banned_emails = ["@nowni.com", "@qq.com", "eu.org", "seav.tk", "cock.li", "@vbbb.us.kg", "@mailbuzz.buzz"]
    for text in banned_emails:
        if text in email:
            webhook_log(f"Failed to register email {email} do to email blacklist <@491266830674034699>")
            return "Failed to register! contact panel@lunes.host if this is a mistake"
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

    response = requests.post(f"{PTERODACTYL_URL}api/application/users", headers=HEADERS, json=body)
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


def delete_user(user_id: int) -> int:
    """
    Deletes a user from both the panel database and Pterodactyl.
    
    Makes a DELETE request to /api/application/users/{user_id} to remove the user.
    
    Returns the HTTP status code (204 on success).
    
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
    # Delete the user from the database
    query = "DELETE FROM users WHERE id = %s"
    values = (user_id,)

    db = DatabaseManager()
    db.execute_query(query, values)

    response = requests.delete(f"{PTERODACTYL_URL}api/application/users/{user_id}", headers=HEADERS)
    response.raise_for_status()

    return response.status_code


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
    requests.post(f"{PTERODACTYL_URL}api/application/servers/{server_id}/suspend", headers=HEADERS)


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
    response = requests.get(f"{PTERODACTYL_URL}api/application/servers?per_page=10000", headers=HEADERS).json()

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
    response = requests.delete(f"{PTERODACTYL_URL}api/application/servers/{server_id}", headers=HEADERS)
    if response.status_code == 204:
        webhook_log(f"Server {server_id} deleted successfully.")
    else:
        webhook_log(f"Failed to delete server {server_id}. Status code: {response.status_code}")
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
    requests.post(f"{PTERODACTYL_URL}api/application/servers/{server_id}/unsuspend", headers=HEADERS)


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
    response = requests.get(f"{PTERODACTYL_URL}api/application/servers?per_page=10000", headers=HEADERS).json()
    
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
            resp = requests.get(f"{PTERODACTYL_URL}api/application/servers/{int(server['attributes']['id'])}", headers=HEADERS).json()
            main_product = products[1]
            body = main_product['limits']
            body["feature_limits"] = main_product['product_limits']
            body['allocation'] = resp['attributes']['allocation']
            print(body)
            resp2 = requests.patch(f"{PTERODACTYL_URL}api/application/servers/{int(server['attributes']['id'])}/build", headers=HEADERS,
                                    json=body)
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


def update_ip(email: str, ip: EnvironHeaders):
    """
    Updates the ip by getting the header with key "CF-Connecting-IP" default is "localhost".
    
    Args:
        email: User's email
        ip: IP address
    
    Returns:
        None
    """
    real_ip = ip.get('CF-Connecting-IP', "localhost")
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
    if everyone is True:
        query = f"UPDATE users SET last_seen = '{datetime.datetime.now()}'"
        db = DatabaseManager()
        db.execute_query(query)
    else:
        query = f"UPDATE users SET last_seen = '{datetime.datetime.now()}' WHERE email = %s"
    db = DatabaseManager()
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


def after_request(session, request: EnvironHeaders, require_login: bool = False):
    """
    This function is called after every request
    
    Args:
        session: Session object
        request: Request object
        require_login: Whether to require login
    
    Returns:
        None
    """
    if require_login is True:
        email = session.get("email")
        if email is None:
            return redirect(url_for("user.login_user"))
        else:
            print(email)
            t1 = threading.Thread(target=update_last_seen, args=(email,), daemon=True)
            t2 = threading.Thread(target=update_ip, args=(email, request), daemon=True)
            ptero_id = get_ptero_id(session['email'])
            session['pterodactyl_id'] = ptero_id
            t1.start()
            t2.start()

    random_id = session.get("random_id")
    if random_id is None:
        characters = string.ascii_letters + string.digits  # You can add more characters if needed

        random_string = ''.join(secrets.SystemRandom().choice(characters) for _ in range(50))

        session['random_id'] = random_string


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



def webhook_log(message: str):
    """
    Sends a message to the webhook.
    
    Args:
        message: Message to send
    
    Returns:
        None
    """
    resp = requests.post(WEBHOOK_URL,
                         json={"username": "Web Logs", "content": message})
    print(resp.text)

def send_email(email: str, title:str, message: str, inner_app):
    """
    Sends an email to the user.
    
    Args:
        email: User's email
        title: Email title
        message: Email message
        inner_app: Flask app
    
    Returns:
        None
    """
    with inner_app.app_context():
        mail = Mail(inner_app)
        print("started emails")
        msg = Message(title, recipients=[email])
        msg.body = message
        mail.send(msg)
        print(f"sent email to {email}")

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
    with inner_app.app_context():
        mail = Mail(inner_app)
        print("started emails")
        msg = Message('Email Verification', recipients=[email])
        msg.body = f'Please click the link below to verify your email:\n\n {HOSTED_URL}verify_email/{verification_token}'
        mail.send(msg)
        print(f"sent email to {email}")
        
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
    with inner_app.app_context():
        mail = Mail(inner_app)
        msg = Message('Password Reset Request', recipients=[email])
        msg.body = f'Please click the link below to reset your password:\n\n {HOSTED_URL}reset_password/{reset_token}'
        mail.send(msg)


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
    response = requests.get(f"{PTERODACTYL_URL}api/application/nodes/{node_id}/allocations", headers=HEADERS)
    data = response.json()
    try:
        allocations = []
        for allocation in data['data']:
            if allocation['attributes']['assigned'] is False:
                allocations.append(allocation['attributes']['id'])
        if len(allocations) == 0:
            return None
        return random.choice(allocations)
    except KeyError:
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
        return 404

    # Get allocation on target node
    allocation_id = get_node_allocation(target_node_id)
    if not allocation_id:
        return 400  # No free allocation

    # Build transfer request
    transfer_data = {
        "allocation_id": allocation_id,
        "node_id": target_node_id
    }
    print(transfer_data, 2)
    
    # Perform server transfer
    transfer_url = f"{PTERODACTYL_URL}api/application/servers/{server_id}/transfer"
    
    try:
        response = requests.post(
            transfer_url, 
            headers=HEADERS, 
            json=transfer_data
        )
        
        # Log the response for debugging
        print(f"Server Transfer Response: {response.status_code}")
        print(f"Response Text: {response.text}")
        
        # Simple logging if transfer is successful
        if response.status_code == 202:
            # Get the user who owns the server (assuming we can retrieve this from server_info)
            user_id = server_info['attributes']['user']
            
            # Log the transfer
            webhook_log(f"User {user_id} transferred server {server_id} to node {target_node_id}")
        
        return response.status_code
    
    except Exception as e:
        print(f"Server transfer error: {e}")
        return 500

def get_all_servers() -> list[dict]:
    """
    Returns list of all servers from Pterodactyl API with ?per_page=10000 parameter.
    
    Returns:
        list[dict]: List of server information
    """
    response = requests.get(f"{PTERODACTYL_URL}api/application/servers?per_page=10000", headers=HEADERS)
    data = response.json()
    return data['data']
