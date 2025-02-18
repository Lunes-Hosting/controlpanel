from config import PTERODACTYL_ADMIN_KEY, PTERODACTYL_ADMIN_USER_KEY, PTERODACTYL_URL
from managers.database_manager import DatabaseManager
from security import safe_requests

class Pterodactyl(DatabaseManager):
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
        resp = safe_requests.get(
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
        response = safe_requests.get(f"{self.PTERODACTYL_URL}api/application/servers/{server_id}", headers=self.HEADERS, timeout=60)
        return response.json()
