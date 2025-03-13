"""
Server Management Module
=================

This module handles all server-related operations including:
- Server information retrieval
- Server status management (suspension/unsuspension)
- Node and allocation management
- Server transfers
- Egg information

Functions in this module interact primarily with the Pterodactyl API
to manage game servers across the system.
"""

import requests
import json
import threading
import random
from config import PTERODACTYL_URL, PTERODACTYL_ADMIN_KEY
from pterocache import PteroCache
from .logging import webhook_log

# Initialize cache
cache = PteroCache()

# API authentication headers
HEADERS = {
    "Authorization": f"Bearer {PTERODACTYL_ADMIN_KEY}",
    'Accept': 'application/json',
    'Content-Type': 'application/json'
}

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
    nodes = cache.get_nodes()
    if not all:
        nodes = [node for node in nodes if node.get("public", True)]
    return nodes

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
    return cache.get_eggs()

def get_autodeploy_info(project_id: int):
    response = requests.get(f"{PTERODACTYL_URL}api/application/autodeploy/{project_id}", headers=HEADERS, timeout=60)
    if response.status_code == 200:
        return response.json()
    return None

def improve_list_servers(pterodactyl_id: int = None):
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
    if pterodactyl_id is None:
        response = requests.get(f"{PTERODACTYL_URL}api/application/servers", headers=HEADERS, timeout=60)
        if response.status_code == 200:
            return response.json()
        return None
    else:
        response = requests.get(f"{PTERODACTYL_URL}api/application/users/{pterodactyl_id}?include=servers", headers=HEADERS, timeout=60)
        if response.status_code == 200:
            return response.json()
        return None

def get_server_information(server_id: int):
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
    if response.status_code == 200:
        return response.json()
    return None

def suspend_server(server_id: int):
    """
    Suspends a server through Pterodactyl API.
    Typically called when user runs out of credits.
    
    Args:
        server_id: Pterodactyl server ID
    
    Returns:
        None
    """
    response = requests.post(f"{PTERODACTYL_URL}api/application/servers/{server_id}/suspend", headers=HEADERS, timeout=60)
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
    response = requests.post(f"{PTERODACTYL_URL}api/application/servers/{server_id}/unsuspend", headers=HEADERS, timeout=60)
    return response.status_code

def get_node_allocation(node_id: int):
    """
    Gets random allocation for a specific node.
    
    Args:
        node_id: ID of the node
    
    Returns:
        int: Random available allocation ID
        None: If no free allocation found
    """
    response = requests.get(f"{PTERODACTYL_URL}api/application/nodes/{node_id}/allocations", headers=HEADERS, timeout=60)
    if response.status_code == 200:
        allocations = response.json()['data']
        free_allocations = [allocation['attributes']['id'] for allocation in allocations if not allocation['attributes']['assigned']]
        if free_allocations:
            return random.choice(free_allocations)
    return None

def transfer_server(server_id: int, target_node_id: int):
    """
    Transfer a server to a new node using Pterodactyl API.
    
    Args:
        server_id (int): ID of the server to transfer
        target_node_id (int): ID of the target node
    
    Returns:
        int: HTTP status code from the transfer request
    """
    # Get a free allocation on the target node
    allocation_id = get_node_allocation(target_node_id)
    if not allocation_id:
        threading.Thread(target=webhook_log, args=(f"No free allocations found on node {target_node_id} for server transfer", 2)).start()
        return 400
    
    # Prepare transfer request body
    transfer_data = {
        "node": target_node_id,
        "allocation": allocation_id
    }
    
    # Send transfer request
    response = requests.post(
        f"{PTERODACTYL_URL}api/application/servers/{server_id}/transfer",
        headers=HEADERS,
        json=transfer_data,
        timeout=60
    )
    
    # Log the result
    if response.status_code == 202:
        threading.Thread(target=webhook_log, args=(f"Successfully initiated transfer of server {server_id} to node {target_node_id}", 1)).start()
    else:
        try:
            error_message = response.json().get('errors', [{}])[0].get('detail', 'Unknown error')
            threading.Thread(target=webhook_log, args=(f"Failed to transfer server {server_id}: {error_message}", 2)).start()
        except:
            threading.Thread(target=webhook_log, args=(f"Failed to transfer server {server_id}: Status code {response.status_code}", 2)).start()
    
    return response.status_code
