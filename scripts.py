import mysql.connector
import bcrypt
import datetime
# Establish a connection to the database
import mysql.connector
from config import *
import requests
from products import products
from retrying import retry
import requests

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
    cnxpanel = mysql.connector.connect(
            host=HOST,
            user=USER,
            password=PASSWORD,
            database=DATABASE
        )
    cnx = mysql.connector.connect(
            host=HOST,
            user=USER,
            password=PASSWORD,
            database="panel"
        )

    cursor = cnx.cursor()
    cursorpanel = cnxpanel.cursor()
    data = requests.get(f"{PTERODACTYL_URL}api/application/users", headers=HEADERS).json()
    for user in data['data']:

        query = f"SELECT * FROM users WHERE email = '{user['attributes']['email']}'"
        cursor.execute(query)
        user = cursor.fetchone()

        if user is None:
            cursorpanel.execute(f"select password from users where email='{user['attributes']['email']}'")
            password = cursorpanel.fetchone()
            query = "INSERT INTO users (name, email, password, id, pterodactyl_id) VALUES (%s, %s, %s, %s, %s)"

            values = (user['attributes']['username'], user['attributes']['email'], password, user['attributes']['username'], user['attributes']['id'] + 500, user['attributes']['id'])
            cursor.execute(query, values)
            cnx.commit()
        
            
            
    cursor.close()
    cnx.close()
    cursorpanel.close()
    cnx.close()


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
def describe_users():
    cnx = mysql.connector.connect(
    host=HOST,
    user=USER,
    password=PASSWORD,
    database=DATABASE
    )

    cursor = cnx.cursor()
    query = "DESCRIBE users"
    cursor.execute(query)
    rows = cursor.fetchall()
    cursor.close()
    cnx.close()
    return rows
    

def get_all_users() ->list:
    cnx = mysql.connector.connect(
    host=HOST,
    user=USER,
    password=PASSWORD,
    database=DATABASE
    )

    cursor = cnx.cursor()
    query = "SELECT * FROM users WHERE email = 'dwattynip123@gmail.com'"
    cursor.execute(query)
    res = cursor.fetchall()
    cursor.close()
    cnx.close()
    return res

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
    
    cnx = mysql.connector.connect(
    host=HOST,
    user=USER,
    password=PASSWORD,
    database=DATABASE
    )

    cursor = cnx.cursor()
    query = f"SELECT pterodactyl_id FROM users WHERE email = '{email}'"
    cursor.execute(query)
    res = cursor.fetchall()
    cursor.close()
    cnx.close()
    return res

def login(email: str, password: str):
    try:
        # Establish a connection to the MySQL server
        cnx = mysql.connector.connect(
            host=HOST,
            user=USER,
            password=PASSWORD,
            database=DATABASE
        )

        # Create a cursor object to execute SQL queries
        cursor = cnx.cursor(buffered=True)

        # Retrieve the hashed password from the database
        query = f"SELECT password FROM users WHERE email = '{email}'"
        cursor.execute(query)
        hashed_password = cursor.fetchone()

        if hashed_password is not None:
            # Verify the password
            is_matched = bcrypt.checkpw(password.encode('utf-8'), hashed_password[0].encode('utf-8'))

            if is_matched:
                # Retrieve all information of the user
                all_info = f"SELECT * FROM users WHERE email = '{email}'"
                cursor.execute(all_info)
                info = cursor.fetchone()
                return info

        return None

    except mysql.connector.Error as err:
        print(f"Error: {err}")
        return None

    finally:
        # Close the cursor and the connection
        if cursor:
            cursor.close()
        if cnx:
            cnx.close()
@retry_on_gateway_error()          
def register(email: str, password: str, name: str, ip: str):
    salt = bcrypt.gensalt(rounds=10)
    cnx = mysql.connector.connect(
        host=HOST,
        user=USER,
        password=PASSWORD,
        database=DATABASE
    )
    password_hash = bcrypt.hashpw(password.encode('utf-8'), salt)
    cursor = cnx.cursor(buffered=True)
        
    query = f"SELECT * FROM users WHERE ip='{ip}'"
    cursor.execute(query)
    results = cursor.fetchone()
    cnx.commit()
    # if results is not None:
    #     return "Ip is already registered"


    
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
        cursor = cnx.cursor()
        
        query = "INSERT INTO users (name, email, password, id, pterodactyl_id, ip) VALUES (%s, %s, %s, %s, %s, %s)"

        values = (name, email, password_hash, data['attributes']['id'] + 500, data['attributes']['id'], ip)
        cursor.execute(query, values)
        cnx.commit()
        cursor.close()
        cnx.close()

        return response.json()

@retry_on_gateway_error()
def delete_user(user_id: int):
    cnx = mysql.connector.connect(
        host=HOST,
        user=USER,
        password=PASSWORD,
        database=DATABASE
    )

    cursor = cnx.cursor()
    
    # Delete the user from the database
    query = "DELETE FROM users WHERE id = %s"
    values = (user_id,)

    cursor.execute(query, values)
    cnx.commit()


    response = requests.delete(f"{PTERODACTYL_URL}api/application/users/{user_id}", headers=HEADERS)
    response.raise_for_status()

    cursor.close()
    cnx.close()

    return response.status_code()

def add_credits(email: str, amount: int):
    cnx = mysql.connector.connect(
    host=HOST,
    user=USER,
    password=PASSWORD,
    database=DATABASE
    )

    cursor = cnx.cursor()
    
    # Delete the user from the database
    query = f"SELECT credits FROM users WHERE email='{email}'"
    

    cursor.execute(query)
    credits = cursor.fetchone()
    print(credits, email)
    query = f"UPDATE users SET credits = {int(credits[0]) + amount} WHERE email='{email}'"
    

    cursor.execute(query)
    cnx.commit()
    cursor.close()
    cnx.close()


def remove_credits(email: str, amount: float):
    cnx = mysql.connector.connect(
    host=HOST,
    user=USER,
    password=PASSWORD,
    database=DATABASE
    )

    cursor = cnx.cursor()
    print(email)
    # Delete the user from the database
    query = f"SELECT credits FROM users WHERE email='{email}'"
    

    cursor.execute(query)
    credits = cursor.fetchone()
    print(credits, email)
    new_credits = float(credits[0]) - amount
    if new_credits <=0:
        return "SUSPEND"
    query = f"UPDATE users SET credits = {new_credits} WHERE email='{email}'"
    

    cursor.execute(query)
    cnx.commit()
    cursor.close()
    cnx.close()
    return None
    
def convert_to_product(data):
    returned = None
    for product in products:
        if product['limits']['memory'] == data['attributes']['limits']['memory']:
            returned = product
            break
        
    return returned
    
def suspend_server(id:int):
    requests.post(f"{PTERODACTYL_URL}api/application/servers/{id}/suspend", headers=HEADERS)
    
def use_credits():
    response = requests.get(f"{PTERODACTYL_URL}api/application/servers?per_page=1000", headers=HEADERS).json()
    cnx = mysql.connector.connect(
            host=HOST,
            user=USER,
            password=PASSWORD,
            database=DATABASE
            )

    cursor = cnx.cursor()
    for server in response['data']:

        product = convert_to_product(server)
        if product is not None:

            query = f"SELECT email FROM users WHERE pterodactyl_id='{int(server['attributes']['user'])}'"
            

            cursor.execute(query)
            email = cursor.fetchone()
            cnx.commit()
            if email is not None:
                if server['attributes']['suspended'] == False:
                    result = remove_credits(email[0], product['price'] / 30 /24)
                    if result == "SUSPEND":
                        suspend_server(server['attributes']['id'])
                
            else:
                print(email, product['price'])
        else:
            print(server['attributes']['name'])
    cursor.close()
    cnx.close()

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

        product = convert_to_product(server)
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
                        unsuspend_server(server['attributes']['id'])
                    else:
                        if server['attributes']['suspended']:
                        
                            suspended_at = server['attributes']['updated_at']
                            suspension_duration = datetime.datetime.now() - datetime.datetime.strptime(suspended_at, "%Y-%m-%dT%H:%M:%S+00:00")
                            if "Test" in server['attributes']['name']:
                                print(server['attributes']['name'], suspension_duration.days)
                            
                            if suspension_duration.days > 8:
                                
                                print(f"Deleting server {server['attributes']['name']} due to suspension for more than 7 days.")
                                
                                delete_server(server['attributes']['id'])

            else:
                print(email, product['price'])
        else:
            pass
    cursor.close()
    cnx.close()
    
def get_credits(email:str):
    cnx = mysql.connector.connect(
            host=HOST,
            user=USER,
            password=PASSWORD,
            database=DATABASE
            )

    cursor = cnx.cursor()
    query = f"SELECT credits FROM users WHERE email='{email}'"
    cursor.execute(query)
    credits = cursor.fetchone()
    cnx.commit()
    cursor.close()
    cnx.close()
    return credits[0]
