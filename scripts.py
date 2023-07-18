import mysql.connector
import bcrypt
# Establish a connection to the database
import mysql.connector
from config import *
import requests
HEADERS = {"Authorization": f"Bearer {PTERODACTYL_ADMIN_KEY}",
        'Accept': 'application/json',
        'Content-Type': 'application/json'}

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
        cursor = cnx.cursor()

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
            
def register(email: str, password: str, name: str):
    salt = bcrypt.gensalt(rounds=10)
    password_hash = bcrypt.hashpw(password.encode('utf-8'), salt)

    cnx = mysql.connector.connect(
        host=HOST,
        user=USER,
        password=PASSWORD,
        database=DATABASE
    )
    
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
        
        query = "INSERT INTO users (name, email, password, id, pterodactyl_id) VALUES (%s, %s, %s, %s, %s)"
        print(response.text)
        values = (name, email, password_hash, data['attributes']['id'] + 500, data['attributes']['id'])
        cursor.execute(query, values)
        cnx.commit()
        cursor.close()
        cnx.close()

        return response.json()


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
