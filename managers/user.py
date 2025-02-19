from threadedreturn import ThreadWithReturnValue
import datetime
import bcrypt
from flask import current_app
import requests
from config import PTERODACTYL_URL
from managers.database_manager import DatabaseManager
from werkzeug.datastructures.headers import EnvironHeaders

from managers.logger import WebhookLogger
from scripts import send_email

class User(DatabaseManager):

    def __init__(self):
        self.log = WebhookLogger()

    def get_ptero_id(self, email: str) -> tuple[int] | None:
        """
        Gets Pterodactyl ID for a user by their email.
        
        Args:
            email: User's email address
        
        Returns:
            tuple[int]: Tuple containing Pterodactyl ID at index 0
            None: If user not found
        """
        res = self.execute_query("SELECT pterodactyl_id FROM users WHERE email = %s", (email,))
        if res is None:
            return None
        return res
    
    def get_id(self, email: str) -> tuple[int] | None:
        """
        Gets user ID for a user by their email.
        
        Args:
            email: User's email address
        
        Returns:
            tuple[int]: Tuple containing user ID at index 0
            None: If user not found
        """
        res = self.execute_query("SELECT id FROM users WHERE email = %s", (email,))
        if res is None:
            return None
        return res
    
    def get_name(self, user_id: int) -> tuple[str] | None:
        """
        Gets username for a user by their ID.
        
        Args:
            user_id: User's ID
        
        Returns:
            tuple[str]: Tuple containing username at index 0
            None: If user not found
        """
        res = self.execute_query("SELECT name FROM users WHERE id = %s", (user_id,))
        if res is None:
            return None
        return res
    
    def login(self, email: str, password: str):
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
        self.log.webhook_log(f"Login attempt with email {email}")
        hashed_password = self.execute_query("SELECT password FROM users WHERE LOWER(email) = LOWER(%s)", (email,))

        if hashed_password is not None:
            # Verify the password
            is_matched = bcrypt.checkpw(password.encode('utf-8'), hashed_password[0].encode('utf-8'))

            if is_matched:
                # Retrieve all information of the user
                info = self.execute_query("SELECT * FROM users WHERE LOWER(email) = LOWER(%s)", (email,))
                is_pending_deletion = self.execute_query("SELECT * FROM pending_deletions WHERE email = %s", (email,))

                if is_pending_deletion is not None:
                    send_email(email, "Account Reactivated", "Your account has been reactivated!", current_app._get_current_object())
                    self.execute_query("DELETE FROM pending_deletions WHERE email = %s", (email,))
                return info

        return None
    

    def register(self, email: str, password: str, name: str, ip: str) -> str | dict:
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
        name = name.strip()
        salt = bcrypt.gensalt(rounds=14)
        passthread = ThreadWithReturnValue(target=bcrypt.hashpw, args=(password.encode('utf-8'), salt))
        passthread.start()
        if "+" in email:
            self.log.webhook_log(f"Failed to register email {email} do to email blacklist <@491266830674034699>")
            return "Failed to register due to blacklist! contact panel@lunes.host if this is a mistake"
        banned_emails = ["@nowni.com", "@qq.com", "eu.org", "seav.tk", "cock.li", "@vbbb.us.kg", "@mailbuzz.buzz",
                        "gongjua.com", "maillazy.com", "rykone.com", "vayonix", "shopepr.com", "eluxeer.com",
                        "bmixr.com", "numerobo.com", "dotzi.net", "mixzu.net", "prorsd.com", "drmail.in", "sectorid.com",
                        "deliveryotter.com", "naver.com", "shouxs.com", "minduls.com", "hi2.in", "intady.com","echo.tax",
                        "wrenden.com", "etik.com", "varieza.com", "flyzy.net", "mimimail.me", "yuvora.com", "owlny.com",
                        "varieza.com", "rennieexpress.delivery", "dotvu.net", "qejjyl.com", "ronete.com", "duck.com", "dnmx.su",
                        "zapany.com", "vvatxiy.com", "tohru.org"]
        for text in banned_emails:
            if text in email:
                self.log.webhook_log(f"Failed to register email {email} with ip {ip} do to email blacklist <@491266830674034699>")
                return "Failed to register due to blacklist! contact panel@lunes.host if this is a mistake"
        self.log.webhook_log(f"User with email: {email}, name: {name} ip: {ip} registered")
        

        results = self.execute_query("SELECT * FROM users WHERE ip = %s", (ip,))

        if results is not None:
            return "Ip is already registered"

        body = {
            "email": email,
            "username": name,
            "first_name": name,
            "last_name": name,
            "password": password
        }

        response = requests.post(f"{PTERODACTYL_URL}api/application/users", headers=self.HEADERS, json=body, timeout=60)
        data = response.json()

        try:
            error = data['errors'][0]['detail']
            return error
        except KeyError:
            user_id = self.execute_query("SELECT * FROM users ORDER BY id DESC LIMIT 0, 1")[0] + 1
            query = ("INSERT INTO users (name, email, password, id, pterodactyl_id, ip, credits) VALUES (%s, %s, %s, %s, %s, %s, %s)")
            password_hash = passthread.join()
            values = (name, email, password_hash, user_id, data['attributes']['id'], ip, 25)
            self.execute_query(query, values)
            return response.json()
        

    def instantly_delete_user(self, email: str) -> int:
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

        ptero_id = self.get_ptero_id(email)[0]
        user_id = self.get_id(email)[0]
        servers = self.list_servers(ptero_id)
        for server in servers:
            server_id = server['attributes']['id']
            self.delete_server(server_id)
        # Delete the user from the database
        self.execute_query(
            "DELETE FROM ticket_comments WHERE user_id = %s", 
            (user_id,)
        )
        self.execute_query(
            "DELETE FROM tickets WHERE user_id = %s", 
            (user_id,)
        )
            
    
        query = "DELETE FROM users WHERE id = %s"
        values = (user_id,)

    
        self.execute_query(query, values)
        send_email(email, "Account Deleted", "Your account has been deleted!", current_app._get_current_object())
        response = requests.delete(f"{PTERODACTYL_URL}api/application/users/{ptero_id}", headers=HEADERS, timeout=60)
        response.raise_for_status()

        return response.status_code
    

    def add_credits(self, email: str, amount: int, set_client: bool = True):
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
        current_credits = self.execute_query("SELECT credits FROM users WHERE email = %s", (email,))
        query = f"UPDATE users SET credits = {int(current_credits[0]) + amount} WHERE email = %s"
        self.execute_query(query, (email,))
        
        if set_client:
            query = f"UPDATE users SET role = 'client' WHERE email = %s"
            self.execute_query(query, (email,))


    def remove_credits(self, email: str, amount: float) -> str | None:
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
        current_credits = self.execute_query("SELECT credits FROM users WHERE email = %s", (email,))
        new_credits = float(current_credits[0]) - amount
        if new_credits <= 0:
            return "SUSPEND"
        query = f"UPDATE users SET credits = {new_credits} WHERE email = %s"
        self.execute_query(query, (email,))
        return None
    
    def account_get_information(self, email: str):
        query = f"SELECT credits, pterodactyl_id, name, email_verified_at, suspended FROM users WHERE email = %s"
        
        information = self.execute_query(query, (email,))

        verified = False
        if information[3] is not None:
            verified = True
        return information[0], information[1], information[2], verified, information[4] == 1

    def get_credits(self,email: str) -> int:
        """
        Returns int of amount of credits in database.
        
        Args:
            email: User's email
        
        Returns:
            int: Credits amount
        """
        query = f"SELECT credits FROM users WHERE email = %s"
        current_credits = self.execute_query(query, (email,))

        return current_credits[0]


    def check_if_user_suspended(self, pterodactyl_id: str) -> bool | None:
        """
        Returns the bool value of if a user is suspended, if user is not found with the pterodactyl id it returns None
        
        Args:
            pterodactyl_id: Pterodactyl user ID
        
        Returns:
            bool: Whether user is suspended
            None: If user not found
        """
        suspended = self.execute_query(f"SELECT suspended FROM users WHERE pterodactyl_id = %s", (pterodactyl_id,))
        to_bool = {0: False, 1: True}
        if suspended is None:
            return True

        return to_bool[suspended[0]]
    
    def get_user_verification_status_and_suspension_status(self, email):
        result = self.execute_query(
            "SELECT email_verified_at, suspended FROM users WHERE email = %s",
            (email,)
        )

        if result:
            verified = False
            if result[0] is not None:
                verified = True

            return (verified, result[1] == 1)
        
        return (None, None)
    
    def update_ip(self, email: str, ip: EnvironHeaders):
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

            self.execute_query(query, (email,))


    def update_last_seen(self, email: str, everyone: bool = False):
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
            self.execute_query(query)
        else:
            query = f"UPDATE users SET last_seen = '{datetime.datetime.now()}' WHERE email = %s"
            self.execute_query(query, (email,))

    def get_last_seen(self, email: str) -> datetime.datetime:
        """
        Returns datetime object of when user with that email was last seen.
        
        Args:
            email: User's email
        
        Returns:
            datetime.datetime: Last seen time
        """
        query = f"SELECT last_seen FROM users WHERE email = %s"
        last_seen = self.execute_query(query, (email,))
        return last_seen[0]
    
    def is_admin(self, email: str) -> bool:
        """
        Checks if user is an admin.
        
        Args:
            email: User's email
        
        Returns:
            bool: Whether user is an admin
        """
        query = "SELECT role FROM users WHERE email = %s"
        role = self.execute_query(query, (email,))
        return role[0] == "admin"
