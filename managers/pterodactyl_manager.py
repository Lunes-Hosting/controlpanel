import datetime
import random
from flask import current_app
import requests
from config import PTERODACTYL_ADMIN_KEY, PTERODACTYL_ADMIN_USER_KEY, PTERODACTYL_URL
from managers.database_manager import DatabaseManager
from managers.logger import WebhookLogger
from managers.user import User
import products
from legacy_scripts import convert_to_product, send_email

class Pterodactyl(DatabaseManager, User):
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
    
    PTERODACTYL_URL=PTERODACTYL_URL

    def __init__(self):
        logger = WebhookLogger()


    def improve_list_servers(self, pterodactyl_id: int = None) -> tuple[dict]:
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
            f"{self.PTERODACTYL_URL}api/application/users/{int(pterodactyl_id)}?include=servers", 
            headers=self.HEADERS, 
        timeout=60).json()

        relationship = resp["attributes"]["relationships"]["servers"]["data"]

        # Using list comprehension for faster filtering
        server_list = [server for server in relationship if int(server["attributes"]["user"]) == pterodactyl_id]

        return (server_list)
    
    def get_server_information(self, server_id: int) -> dict:
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
        response = requests.get(f"{self.PTERODACTYL_URL}api/application/servers/{server_id}", headers=self.HEADERS, timeout=60)
        return response.json()
    
    def suspend_server(self, server_id: int):
        """
        Suspends a server through Pterodactyl API.
        Typically called when user runs out of credits.
        
        Args:
            server_id: Pterodactyl server ID
        
        Returns:
            None
        """
        requests.post(f"{PTERODACTYL_URL}api/application/servers/{server_id}/suspend", headers=self.HEADERS, timeout=60)


    def use_credits(self):
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
        response = requests.get(f"{PTERODACTYL_URL}api/application/servers?per_page=10000", headers=self.HEADERS, timeout=60).json()

        for server in response['data']:

            product = convert_to_product(server)
            if product is not None:

                query = f"SELECT email FROM users WHERE pterodactyl_id='{int(server['attributes']['user'])}'"

                db = DatabaseManager()
                email = db.execute_query(query)

                if email is not None:
                    if not server['attributes']['suspended']:
                        result = self.remove_credits(email[0], product['price'] / 30 / 24)
                        if result == "SUSPEND":
                            send_email(email[0], "Server Suspended", "Your server has been suspended due to lack of credits!", current_app._get_current_object())
                            self.suspend_server(server['attributes']['id'])

                else:
                    print(email, product['price'])
            else:
                print(server['attributes']['name'])


    def delete_server(self, server_id) -> int:
        """
        Tries to delete server returns status code.
        
        Args:
            server_id: Pterodactyl server ID
        
        Returns:
            int: HTTP status code
        """
        response = requests.delete(f"{PTERODACTYL_URL}api/application/servers/{server_id}", headers=self.HEADERS, timeout=60)
        if response.status_code == 204:
            self.log.webhook_log(f"Server {server_id} deleted successfully via Script delete_server function.", 0)
        else:
            self.log.webhook_log(f"Failed to delete server {server_id}. Status code: {response.status_code}", 1)
        return response.status_code


    def unsuspend_server(self, server_id: int):
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
        requests.post(f"{PTERODACTYL_URL}api/application/servers/{server_id}/unsuspend", headers=self.HEADERS, timeout=60)


    def check_to_unsuspend(self):
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
        response = requests.get(f"{PTERODACTYL_URL}api/application/servers?per_page=10000", headers=self.HEADERS, timeout=60).json()
        
        for server in response['data']:
            user_suspended = self.check_if_user_suspended(server['attributes']['user'])
            if user_suspended:
                
                self.delete_server(server['attributes']['id'])
                self.log.webhook_log("Server deleted due to user suspension")
            
            product = convert_to_product(server)
            if product is None:
                self.log.webhook_log(f"```{server}``` no product")
            #           server_id = server['attributes']['id']
                print(server['attributes']['name'], None)
                resp = requests.get(f"{PTERODACTYL_URL}api/application/servers/{int(server['attributes']['id'])}", headers=self.HEADERS, timeout=60).json()
                main_product = products[1]
                body = main_product['limits']
                body["feature_limits"] = main_product['product_limits']
                body['allocation'] = resp['attributes']['allocation']
                print(body)
                resp2 = requests.patch(f"{PTERODACTYL_URL}api/application/servers/{int(server['attributes']['id'])}/build", headers=self.HEADERS,
                                        json=body, timeout=60)
            if product is not None and product['name'] != "Free Tier":

                query = f"SELECT email FROM users WHERE pterodactyl_id='{int(server['attributes']['user'])}'"


                email = self.execute_query(query)
                
                query = f"SELECT credits FROM users WHERE pterodactyl_id='{int(server['attributes']['user'])}'"
                current_credits = self.execute_query(query)

                if email is None or current_credits is None:
                    pass
                if email is not None:
                    if server['attributes']['suspended']:
                        # print(server['attributes']['user'], "is suspeded", credits[0], product['price'] / 30/ 24)
                        if current_credits[0] >= product['price'] / 30 / 24:
                            if not self.check_if_user_suspended(server['attributes']['user']):
                                self.unsuspend_server(server['attributes']['id'])
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
                                    self.log.webhook_log(f"Deleting server {server['attributes']['name']} with id: {server['attributes']['id']} due to suspension for more than 3 days.")

                                    self.delete_server(server['attributes']['id'])

                else:
                    print(email, product['price'])
            elif product is not None:
                if product['name'] == "Free Tier":
                    query = f"SELECT last_seen, email FROM users WHERE pterodactyl_id='{int(server['attributes']['user'])}'"
                    db = DatabaseManager()
                    last_seen, email = db.execute_query(query)
                    
                    if last_seen is not None:
                        if datetime.datetime.now() - last_seen > datetime.timedelta(days=30):
                            self.log.webhook_log(
                                f"Deleting server {server['attributes']['name']} due to inactivity for more than 30 days.")
                            self.delete_server(server['attributes']['id'])
                        else:
                            if not self.check_if_user_suspended(server['attributes']['user']):
                                self.unsuspend_server(server['attributes']['id'])
                    else:
                        self.update_last_seen(email)

    def get_node_allocation(self, node_id: int) -> int | None:
        """
        Gets random allocation for a specific node.
        
        Args:
            node_id: ID of the node
        
        Returns:
            int: Random available allocation ID
            None: If no free allocation found
        """
        response = requests.get(f"{PTERODACTYL_URL}api/application/nodes/{node_id}/allocations", headers=self.HEADERS, timeout=60)
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


    def transfer_server(self, server_id: int, target_node_id: int) -> int:
        """
        Transfer a server to a new node using Pterodactyl API.
        
        Args:
            server_id (int): ID of the server to transfer
            target_node_id (int): ID of the target node
        
        Returns:
            int: HTTP status code from the transfer request
        """
        # Get server details
        server_info = self.get_server_information(server_id)
        if not server_info:
            return 404

        # Get allocation on target node
        allocation_id = self.get_node_allocation(target_node_id)
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
                headers=self.HEADERS, 
                json=transfer_data, 
            timeout=60)
            
            # Log the response for debugging
            print(f"Server Transfer Response: {response.status_code}")
            print(f"Response Text: {response.text}")
            
            # Simple logging if transfer is successful
            if response.status_code == 202:
                # Get the user who owns the server (assuming we can retrieve this from server_info)
                user_id = server_info['attributes']['user']
                
                # Log the transfer
                self.log.webhook_log(f"User {user_id} transferred server {server_id} to node {target_node_id}")
            
            return response.status_code
        
        except Exception as e:
            print(f"Server transfer error: {e}")
            return 500

    def get_all_servers(self) -> list[dict]:
        """
        Returns list of all servers from Pterodactyl API with ?per_page=10000 parameter.
        
        Returns:
            list[dict]: List of server information
        """
        response = requests.get(f"{PTERODACTYL_URL}api/application/servers?per_page=10000", headers=self.HEADERS, timeout=60)
        data = response.json()
        return data['data']

    
