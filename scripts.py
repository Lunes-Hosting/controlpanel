import mysql.connector
import bcrypt
import datetime
# Establish a connection to the database
import mysql.connector
from config import *
import requests
import typing as t
from functools import wraps
from products import products
from retrying import retry
import requests
from flask import request, session, url_for, redirect, abort
from werkzeug.datastructures.headers import EnvironHeaders
import threading
import random, string

# Define the retry decorator
def retry_on_gateway_error():
    return retry(stop_max_attempt_number=10, wait_fixed=2000, retry_on_exception=is_gateway_error)

# Define a function to check if the exception is a 502 Gateway error
def is_gateway_error(exception):
    if isinstance(exception, requests.exceptions.HTTPError):
        return exception.response.status_code == 502
    return False

HEADERS = {"Authorization": f"Bearer {PTERODACTYL_ADMIN_KEY}",
        'Accept': 'application/json',
        'Content-Type': 'application/json'}
CLIENT_HEADERS =  {"Authorization": f"Bearer {PTERODACTYL_ADMIN_USER_KEY}",
        'Accept': 'application/json',
        'Content-Type': 'application/json'}



def sync_users_script():

    data = requests.get(f"{PTERODACTYL_URL}api/application/users?per_page=100000", headers=HEADERS).json()
    for user in data['data']:

        query = f"SELECT * FROM users WHERE email = %s"
        user_controlpanel = use_database(query, (user['attributes']['email'],))


        if user_controlpanel is None:
            password = use_database(f"select password from users where email = %s", (user['attributes']['email'],))
            query = "INSERT INTO users (name, email, password, id, pterodactyl_id, credits) VALUES (%s, %s, %s, %s, %s, %s)"

            values = (user['attributes']['username'], user['attributes']['email'], password, user['attributes']['id'], user['attributes']['id'], 25)
            use_database(query, values)


def get_nodes():
    available_nodes = []
    nodes = requests.get(f"{PTERODACTYL_URL}api/application/nodes", headers=HEADERS).json()
    for node in nodes['data']:
        if "full" not in node['attributes']['name'].lower():
            available_nodes.append({"node_id": node['attributes']['id'], "name": node['attributes']['name']})
    return available_nodes

def get_eggs():
    available_eggs = []
    nests = requests.get(f"{PTERODACTYL_URL}api/application/nests", headers=HEADERS)

    nests_data = nests.json()
    for nest in nests_data['data']:
        resp = requests.get(f"{PTERODACTYL_URL}api/application/nests/{nest['attributes']['id']}/eggs", headers=HEADERS)
        data = resp.json()
        for egg in data['data']:
            attributes = egg['attributes']
            available_eggs.append(
                {"egg_id": attributes['id'], "name": attributes['name'], "docker_image": attributes['docker_image'], "startup": attributes['startup']}
            )
    return available_eggs

@retry_on_gateway_error()
def list_servers(pterodactyl_id:int):
    response = requests.get(f"{PTERODACTYL_URL}api/application/servers?per_page=1000", headers=HEADERS)
    users_server = []
    data = response.json()
    for server in data['data']:
        if server['attributes']['user'] ==pterodactyl_id:
            users_server.append(server)
    return users_server
@retry_on_gateway_error()
def get_server_information(server_id:int):
    response = requests.get(f"{PTERODACTYL_URL}api/application/servers/{server_id}", headers=HEADERS)
    return response.json()

def get_ptero_id(email:str):
    query = f"SELECT pterodactyl_id FROM users WHERE email = %s"
    res = use_database(query, (email,))
    if res is None:
        print(email, "does not have ptero id")
    return res

def login(email: str, password: str):

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

    
@retry_on_gateway_error()          
def register(email: str, password: str, name: str, ip: str):
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
        
        query = "INSERT INTO users (name, email, password, id, pterodactyl_id, ip, credits) VALUES (%s, %s, %s, %s, %s, %s, %s)"

        values = (name, email, password_hash, data['attributes']['id'] + 500, data['attributes']['id'], ip, 25)
        use_database(query, values)

        return response.json()

@retry_on_gateway_error()
def delete_user(user_id: int):
    # Delete the user from the database
    query = "DELETE FROM users WHERE id = %s"
    values = (user_id,)

    use_database(query, values)

    response = requests.delete(f"{PTERODACTYL_URL}api/application/users/{user_id}", headers=HEADERS)
    response.raise_for_status()

    return response.status_code()

def add_credits(email: str, amount: int, set_client:bool=True):
    # Delete the user from the database
    query = f"SELECT credits FROM users WHERE email = %s"
    
    credits = use_database(query, (email,))
    query = f"UPDATE users SET credits = {int(credits[0]) + amount} WHERE email = %s"
    
    use_database(query, (email,))
    if set_client:
        query = f"UPDATE users SET role = 'client' WHERE email = %s"
        use_database(query, (email,))



def remove_credits(email: str, amount: float):
    query = f"SELECT credits FROM users WHERE email = %s"
    

    credits = use_database(query, (email,))
    new_credits = float(credits[0]) - amount
    if new_credits <=0:
        return "SUSPEND"
    query = f"UPDATE users SET credits = {new_credits} WHERE email = %s"
    

    use_database(query, (email,))
    return None
    
def convert_to_product(data):
    returned = None
    for product in products:
        if int(product['limits']['memory']) == int(data['attributes']['limits']['memory']):
            returned = product
            break

        
    if returned == None:
        print(data['attributes']['limits']['memory'], products)
    return returned
    
def suspend_server(id:int):
    requests.post(f"{PTERODACTYL_URL}api/application/servers/{id}/suspend", headers=HEADERS)
    
def use_credits():
    response = requests.get(f"{PTERODACTYL_URL}api/application/servers?per_page=10000", headers=HEADERS).json()


    for server in response['data']:

        product = convert_to_product(server)
        if product is not None:

            query = f"SELECT email FROM users WHERE pterodactyl_id='{int(server['attributes']['user'])}'"
            

            email = use_database(query)
            
            if email is not None:
                if server['attributes']['suspended'] == False:
                    result = remove_credits(email[0], product['price'] / 30 /24)
                    if result == "SUSPEND":
                        suspend_server(server['attributes']['id'])
                
            else:
                print(email, product['price'])
        else:
            print(server['attributes']['name'])

def delete_server(server_id):
    response = requests.delete(f"{PTERODACTYL_URL}api/application/servers/{server_id}", headers=HEADERS)
    if response.status_code == 204:
        print(f"Server {server_id} deleted successfully.")
    else:
        print(f"Failed to delete server {server_id}. Status code: {response.status_code}")

def unsuspend_server(id:int):
    requests.post(f"{PTERODACTYL_URL}api/application/servers/{id}/unsuspend", headers=HEADERS)
    
def check_to_unsuspend():
    response = requests.get(f"{PTERODACTYL_URL}api/application/servers?per_page=10000", headers=HEADERS).json()
    cnx = mysql.connector.connect(
            host=HOST,
            user=USER,
            password=PASSWORD,
            database=DATABASE
            )

    cursor = cnx.cursor()
    for server in response['data']:
        user_suspended =  check_if_user_suspended(int(server['attributes']['user']))
        if user_suspended == True:
            if server['attributes']['suspended'] == False:
                suspend_server(server['attributes']['id'])
            else:
                suspended_at = server['attributes']['updated_at']
                suspension_duration = datetime.datetime.now() - datetime.datetime.strptime(suspended_at, "%Y-%m-%dT%H:%M:%S+00:00")

                if suspension_duration.days > 3:
                    
                    print(f"Deleting server {server['attributes']['name']} due to suspension for more than 3 days.")
                    
                    delete_server(server['attributes']['id'])
        product = convert_to_product(server)
        if product is None:
            print(server, "no product")
        # print(server['attributes']['name'], product)
        if product is not None and product['name'] != "Free Tier":

            query = f"SELECT email FROM users WHERE pterodactyl_id='{int(server['attributes']['user'])}'"
            cursor.execute(query)
            email = cursor.fetchone()
            cnx.commit()
            
            query = f"SELECT credits FROM users WHERE pterodactyl_id='{int(server['attributes']['user'])}'"
            cursor.execute(query)
            credits = cursor.fetchone()
            
            cnx.commit()
            if email is None or credits is None:
                pass
            if email is not None:
                if server['attributes']['suspended'] == True:
                    # print(server['attributes']['user'], "is suspeded", credits[0], product['price'] / 30/ 24)
                    if credits[0] >= product['price'] / 30 /24:
                        if check_if_user_suspended(server['attributes']['user']) == False:
                            unsuspend_server(server['attributes']['id'])
                    else:
                        if server['attributes']['suspended']:
                        
                            suspended_at = server['attributes']['updated_at']
                            suspension_duration = datetime.datetime.now() - datetime.datetime.strptime(suspended_at, "%Y-%m-%dT%H:%M:%S+00:00")
                            
                            if suspension_duration.days > 3:
                                
                                print(f"Deleting server {server['attributes']['name']} due to suspension for more than 3 days.")
                                
                                delete_server(server['attributes']['id'])

            else:
                print(email, product['price'])
        elif product is not None:
            if product['name'] == "Free Tier":
                query = f"SELECT last_seen FROM users WHERE pterodactyl_id='{int(server['attributes']['user'])}'"
                cursor.execute(query)
                last_seen = cursor.fetchone()
                if last_seen is not None:
                    if datetime.datetime.now() - last_seen[0] > datetime.timedelta(days=30):
                        print(f"Deleting server {server['attributes']['name']} due to inactivity for more than 30 days.")
                        delete_server(server['attributes']['id'])
                    else:
                        if check_if_user_suspended(server['attributes']['user']) == False:
                            unsuspend_server(server['attributes']['id'])
                cnx.commit()
                
    cursor.close()
    cnx.close()
    
def get_credits(email:str):
    query = f"SELECT credits FROM users WHERE email = %s"
    credits = use_database(query, (email,))

    return credits[0]

def check_if_user_suspended(pterodactyl_id: str):
    suspended = use_database(f"SELECT suspended FROM users WHERE pterodactyl_id = %s", (pterodactyl_id,))
    to_bool = {0: False, 1: True}
    if suspended is None:
        return True
    
    return to_bool[suspended[0]]
    
def update_ip(email:str, ip:EnvironHeaders):
    real_ip=ip.get('CF-Connecting-IP', "localhost")
    query = f"UPDATE users SET ip = '{real_ip}' where email = %s"
    
    use_database(query, (email,))
    
def update_last_seen(email:str, everyone:bool=False):
    if everyone is True:
        query = f"UPDATE users SET last_seen = '{datetime.datetime.now()}'"
        use_database(query)
    else:
        query = f"UPDATE users SET last_seen = '{datetime.datetime.now()}' WHERE email = %s"
    use_database(query, (email,))


def get_last_seen(email:str):
    query = f"SELECT last_seen FROM users WHERE email = %s"
    last_seen = use_database(query, (email,))
    return last_seen[0]

def after_request(session, request: EnvironHeaders, require_login:bool=False):
    """
    This function is called after every request
    """
    if require_login is True:
        email = session.get("email")
        if email is None:
            return redirect(url_for("user.login_user"))
        else:
            print(email)
            t1 =threading.Thread(target=update_last_seen, args=(email,), daemon=True)
            t2 = threading.Thread(target=update_ip, args=(email, request), daemon=True)
            id = get_ptero_id(session['email'])
            session['pterodactyl_id'] = id
            t1.start()
            t2.start()
    
    random_id = session.get("random_id")
    print(random_id)
    if random_id is None:
        characters = string.ascii_letters + string.digits  # You can add more characters if needed

        random_string = ''.join(random.choice(characters) for _ in range(50))

        session['random_id'] = random_string

def use_database(query:str, values:tuple=None):
    result = None
    cnx = mysql.connector.connect(
            host=HOST,
            user=USER,
            password=PASSWORD,
            database=DATABASE
            )

    cursor = cnx.cursor(buffered=True)
    cursor.execute(query, values)
    if "select" in query.lower():
        result = cursor.fetchone()
    cnx.commit()
    cursor.close()
    cnx.close()
    return result
