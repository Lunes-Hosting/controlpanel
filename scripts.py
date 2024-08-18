import datetime
import string
import threading

import bcrypt
import mysql.connector
# Establish a connection to the database
import mysql.connector
import requests
from flask import url_for, redirect
from werkzeug.datastructures.headers import EnvironHeaders

from config import *
from products import products
import secrets

HEADERS = {"Authorization": f"Bearer {PTERODACTYL_ADMIN_KEY}",
           'Accept': 'application/json',
           'Content-Type': 'application/json'}
CLIENT_HEADERS = {"Authorization": f"Bearer {PTERODACTYL_ADMIN_USER_KEY}",
                  'Accept': 'application/json',
                  'Content-Type': 'application/json'}


def sync_users_script():
    """Adds any users to panel that was added using pterodactyl"""
    data = requests.get(f"{PTERODACTYL_URL}api/application/users?per_page=100000", headers=HEADERS).json()
    for user in data['data']:

        query = f"SELECT * FROM users WHERE email = %s"
        user_controlpanel = use_database(query, (user['attributes']['email'],))

        if user_controlpanel is None:
            user_id = use_database("SELECT * FROM users ORDER BY id DESC LIMIT 0, 1")[0] + 1
            password = use_database(f"select password from users where email = %s", (user['attributes']['email'],),
                                    "panel")
            query = ("INSERT INTO users (name, email, password, id, pterodactyl_id, credits) VALUES (%s, %s, %s, %s, "
                     "%s, %s)")

            values = (
                user['attributes']['username'], user['attributes']['email'], password[0], user_id,
                user['attributes']['id'],
                25)
            use_database(query, values)


def get_nodes() -> list[dict]:
    """Returns list of dictionaries with node information in format:
    {"node_id": node['attributes']['id'], "name": node['attributes']['name']}"""
    available_nodes = []
    nodes = requests.get(f"{PTERODACTYL_URL}api/application/nodes", headers=HEADERS).json()
    for node in nodes['data']:
        if "full" not in node['attributes']['name'].lower():
            available_nodes.append({"node_id": node['attributes']['id'], "name": node['attributes']['name']})
    return available_nodes


def get_eggs() -> list[dict]:
    """Returns list of dictionaries with egg iformation in format:
    {"egg_id": attributes['id'], "name": attributes['name'], "docker_image": attributes['docker_image'],
     "startup": attributes['startup']}
    """
    try:
        available_eggs = []
        nests = requests.get(f"{PTERODACTYL_URL}api/application/nests", headers=HEADERS)

        nests_data = nests.json()
        for nest in nests_data['data']:
            resp = requests.get(f"{PTERODACTYL_URL}api/application/nests/{nest['attributes']['id']}/eggs", headers=HEADERS)
            data = resp.json()
            for egg in data['data']:
                attributes = egg['attributes']
                available_eggs.append(
                    {"egg_id": attributes['id'], "name": attributes['name'], "docker_image": attributes['docker_image'],
                    "startup": attributes['startup']}
                )
        return available_eggs
    except KeyError as e:
        print(e, data, resp)
        return None

def list_servers(pterodactyl_id: int) -> list[dict]:
    """Returns list of dictionaries of servers with owner of that pterodactyl id"""
    try:
        response = requests.get(f"{PTERODACTYL_URL}api/application/servers?per_page=1000", headers=HEADERS)
        users_server = []
        data = response.json()
        for server in data['data']:
            if server['attributes']['user'] == pterodactyl_id:
                users_server.append(server)
        return users_server
    except KeyError as e:
        print(e, pterodactyl_id, data)
        return None

def get_server_information(server_id: int) -> dict:
    """Returns dictionary of server information from pterodactyl api"""
    response = requests.get(f"{PTERODACTYL_URL}api/application/servers/{server_id}", headers=HEADERS)
    return response.json()


def get_ptero_id(email: str) -> tuple[int] | None:
    """Returns tuple with id in index 0, if no user is found returns None"""
    query = f"SELECT pterodactyl_id FROM users WHERE email = %s"
    res = use_database(query, (email,))
    if res is None:
        return None
    return res

def get_id(email: str) -> tuple[int] | None:
    """Returns tuple with id in index 0, if no user is found returns None"""
    query = f"SELECT id FROM users WHERE email = %s"
    res = use_database(query, (email,))
    if res is None:
        return None
    return res

def get_name(user_id: int) -> tuple[str] | None:
    """Returns tuple with id in index 0, if no user is found returns None"""
    query = f"SELECT name FROM users WHERE id = %s"
    res = use_database(query, (user_id,))
    if res is None:
        return None
    return res

def login(email: str, password: str):
    """Checks if login info is correct if it isn't correct returns None if it is returns unmodified database
    information"""
    webhook_log(f"Login attempt with email {email}")
    query = f"SELECT password FROM users WHERE email = %s"
    hashed_password = use_database(query, (email,))

    if hashed_password is not None:
        # Verify the password
        is_matched = bcrypt.checkpw(password.encode('utf-8'), hashed_password[0].encode('utf-8'))

        if is_matched:
            # Retrieve all information of the user
            all_info = f"SELECT * FROM users WHERE email = %s"
            info = use_database(all_info, (email,))

            return info

    return None


def register(email: str, password: str, name: str, ip: str) -> str | dict:
    """Attempts to register user if it fails it returns error in string otherwise returns user object json"""
    webhook_log(f"User with email: {email}, name: {name} ip: {ip} registered")
    salt = bcrypt.gensalt(rounds=10)
    password_hash = bcrypt.hashpw(password.encode('utf-8'), salt)

    query = f"SELECT * FROM users WHERE ip = %s"
    results = use_database(query, (ip,))

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
        user_id = use_database("SELECT * FROM users ORDER BY id DESC LIMIT 0, 1")[0] + 1
        query = ("INSERT INTO users (name, email, password, id, pterodactyl_id, ip, credits) VALUES (%s, %s, %s, %s, "
                 "%s, %s, %s)")

        values = (name, email, password_hash, user_id, data['attributes']['id'], ip, 25)
        use_database(query, values)

        return response.json()


def delete_user(user_id: int) -> int:
    """Returns request status code"""
    # Delete the user from the database
    query = "DELETE FROM users WHERE id = %s"
    values = (user_id,)

    use_database(query, values)

    response = requests.delete(f"{PTERODACTYL_URL}api/application/users/{user_id}", headers=HEADERS)
    response.raise_for_status()

    return response.status_code


def add_credits(email: str, amount: int, set_client: bool = True):
    # Delete the user from the database
    query = f"SELECT credits FROM users WHERE email = %s"

    current_credits = use_database(query, (email,))
    query = f"UPDATE users SET credits = {int(current_credits[0]) + amount} WHERE email = %s"

    use_database(query, (email,))
    if set_client:
        query = f"UPDATE users SET role = 'client' WHERE email = %s"
        use_database(query, (email,))


def remove_credits(email: str, amount: float) -> str | None:
    """Attempts to remove credits from user returns "SUSPEND" if the amount of credits to subtract is more than amount
     user has otherwise returns None"""
    query = f"SELECT credits FROM users WHERE email = %s"

    current_credits = use_database(query, (email,))
    new_credits = float(current_credits[0]) - amount
    if new_credits <= 0:
        return "SUSPEND"
    query = f"UPDATE users SET credits = {new_credits} WHERE email = %s"

    use_database(query, (email,))
    return None


def convert_to_product(data) -> dict:
    """Returns Product with matched MEMORY count all other fields ignored"""
    returned = None
    for product in products:
        if int(product['limits']['memory']) == int(data['attributes']['limits']['memory']):
            returned = product
            break

    if returned is None:
        print(data['attributes']['limits']['memory'], products)
    return returned


def suspend_server(server_id: int):
    requests.post(f"{PTERODACTYL_URL}api/application/servers/{server_id}/suspend", headers=HEADERS)


def use_credits():
    """Checks all servers products and uses credits of owners"""
    response = requests.get(f"{PTERODACTYL_URL}api/application/servers?per_page=10000", headers=HEADERS).json()

    for server in response['data']:

        product = convert_to_product(server)
        if product is not None:

            query = f"SELECT email FROM users WHERE pterodactyl_id='{int(server['attributes']['user'])}'"

            email = use_database(query)

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
    """Tries to delete server returns status code"""
    response = requests.delete(f"{PTERODACTYL_URL}api/application/servers/{server_id}", headers=HEADERS)
    if response.status_code == 204:
        webhook_log(f"Server {server_id} deleted successfully.")
    else:
        webhook_log(f"Failed to delete server {server_id}. Status code: {response.status_code}")
    return response.status_code


def unsuspend_server(server_id: int):
    """Un-suspends specific server id"""
    requests.post(f"{PTERODACTYL_URL}api/application/servers/{server_id}/unsuspend", headers=HEADERS)


def check_to_unsuspend():
    """Gets all servers loops through and checks if user has moore credits than required or was last seen for free
    tier to un-suspend it, ignores suspended users"""
    response = requests.get(f"{PTERODACTYL_URL}api/application/servers?per_page=10000", headers=HEADERS).json()
    
    for server in response['data']:
        user_suspended = check_if_user_suspended(server['attributes']['user'])
        if user_suspended:
            if not server['attributes']['suspended']:
                suspend_server(server['attributes']['id'])
            else:
                suspended_at = server['attributes']['updated_at']
                suspension_duration = datetime.datetime.now() - datetime.datetime.strptime(suspended_at,
                                                                                           "%Y-%m-%dT%H:%M:%S+00:00")

                if suspension_duration.days > 5:
                    webhook_log(f"Deleting server {server['attributes']['name']} due to suspension for more than 5 days.")

                    delete_server(server['attributes']['id'])
        product = convert_to_product(server)
        if product is None:
            webhook_log(f"```{server}``` no product")
        # print(server['attributes']['name'], product)
        if product is not None and product['name'] != "Free Tier":

            query = f"SELECT email FROM users WHERE pterodactyl_id='{int(server['attributes']['user'])}'"
            email = use_database(query)
            
            query = f"SELECT credits FROM users WHERE pterodactyl_id='{int(server['attributes']['user'])}'"
            current_credits = use_database(query)

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

                                delete_server(server['attributes']['id'])

            else:
                print(email, product['price'])
        elif product is not None:
            if product['name'] == "Free Tier":
                query = f"SELECT last_seen, email FROM users WHERE pterodactyl_id='{int(server['attributes']['user'])}'"
                last_seen, email = use_database(query)
                
                if last_seen is not None:
                    if datetime.datetime.now() - last_seen > datetime.timedelta(days=30):
                        print(
                            f"Deleting server {server['attributes']['name']} due to inactivity for more than 30 days.")
                        delete_server(server['attributes']['id'])
                    else:
                        if not check_if_user_suspended(server['attributes']['user']):
                            unsuspend_server(server['attributes']['id'])
                else:
                    update_last_seen(email)




def get_credits(email: str) -> int:
    """Returns int of amount of credits in database."""
    query = f"SELECT credits FROM users WHERE email = %s"
    current_credits = use_database(query, (email,))

    return current_credits[0]


def check_if_user_suspended(pterodactyl_id: str) -> bool | None:
    """Returns the bool value of if a user is suspended, if user is not found with the pterodactyl id it returns None"""
    suspended = use_database(f"SELECT suspended FROM users WHERE pterodactyl_id = %s", (pterodactyl_id,))
    to_bool = {0: False, 1: True}
    if suspended is None:
        return True

    return to_bool[suspended[0]]


def update_ip(email: str, ip: EnvironHeaders):
    """Updates the ip by getting the header with key "CF-Connecting-IP" default is "localhost"."""
    real_ip = ip.get('CF-Connecting-IP', "localhost")
    if real_ip != "localhost":
        query = f"UPDATE users SET ip = '{real_ip}' where email = %s"

        use_database(query, (email,))


def update_last_seen(email: str, everyone: bool = False):
    """Sets a users last seen to current time in database, if "everyone" is True it updates everyone in database to
    current time."""
    if everyone is True:
        query = f"UPDATE users SET last_seen = '{datetime.datetime.now()}'"
        use_database(query)
    else:
        query = f"UPDATE users SET last_seen = '{datetime.datetime.now()}' WHERE email = %s"
    use_database(query, (email,))


def get_last_seen(email: str) -> datetime.datetime:
    """Returns datetime object of when user with that email was last seen."""
    query = f"SELECT last_seen FROM users WHERE email = %s"
    last_seen = use_database(query, (email,))
    return last_seen[0]


def after_request(session, request: EnvironHeaders, require_login: bool = False):
    """
    This function is called after every request
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
    query = "SELECT role FROM users WHERE email = %s"
    role = use_database(query, (email,))
    return role[0] == "admin"


def use_database(query: str, values: tuple = None, database=DATABASE, all: bool = False) -> tuple | None | list:
    """Runs database query, if "SELECT" is in the query it returns unmodified result otherwise returns None"""
    result = None
    cnx = mysql.connector.connect(
        host=HOST,
        user=USER,
        password=PASSWORD,
        database=database,
        charset='utf8mb4',
        collation='utf8mb4_unicode_ci'
    )

    cursor = cnx.cursor(buffered=True)
    cursor.execute(query, values)
    if "select" in query.lower():
        if all is False:
            result = cursor.fetchone()
        else:
            result = cursor.fetchall()
    cnx.commit()
    cursor.close()
    cnx.close()
    return result


def webhook_log(message: str):
    resp = requests.post(WEBHOOK_URL,
                         json={"username": "Web Logs", "content": message})
    print(resp.text)
