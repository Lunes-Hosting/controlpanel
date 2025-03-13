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
from config import PTERODACTYL_URL, PTERODACTYL_ADMIN_KEY, AUTODEPLOY_NEST_ID, PTERODACTYL_CLIENT_KEY
from pterocache import PteroCache
from managers.database_manager import DatabaseManager
from .logging import webhook_log
import time
from security import safe_requests
import secrets

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
    """
    Gets autodeploy information for a project.
    
    Args:
        project_id: ID of the project
        
    Returns:
        tuple: (list of egg information, environment variables)
    """
    res = DatabaseManager.execute_query("SELECT * FROM projects WHERE id = %s", (project_id,))
    if res is not None:
        egg_id = res[8]
        egg_info = safe_requests.get(f"{PTERODACTYL_URL}api/application/nests/{AUTODEPLOY_NEST_ID}/eggs/{egg_id}?include=variables", 
            headers=HEADERS, timeout=60).json()
        attributes = egg_info['attributes']
        
        # Convert res[7] to a dictionary
        try:
            environment = json.loads(res[7]) if res[7] else {}  # Ensure it's a dict
        except json.JSONDecodeError:
            environment = {}  # Fallback to empty dict if parsing fails
        
        return [{"egg_id": attributes['id'], "name": attributes['name'], "docker_image": attributes['docker_image'],
                "startup": attributes['startup']}], environment
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
        response = safe_requests.get(f"{PTERODACTYL_URL}api/application/servers?per_page=100000", headers=HEADERS, timeout=60)
        if response.status_code == 200:
            return response.json()
        return None
    else:
        response = safe_requests.get(f"{PTERODACTYL_URL}api/application/users/{pterodactyl_id}?include=servers", headers=HEADERS, timeout=60)
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
    response = safe_requests.get(f"{PTERODACTYL_URL}api/application/servers/{server_id}", headers=HEADERS, timeout=60)
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
    response = safe_requests.get(f"{PTERODACTYL_URL}api/application/nodes/{node_id}/allocations?per_page=100000", headers=HEADERS, timeout=60)
    if response.status_code == 200:
        allocations = response.json()['data']
        free_allocations = [allocation['attributes']['id'] for allocation in allocations if not allocation['attributes']['assigned']]
        if free_allocations:
            return secrets.choice(free_allocations)
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
        threading.Thread(target=webhook_log, args=(f"Failed to get server info for server {server_id}", 2)).start()
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
        
        # If we get a connection error (504), try to forcefully stop the server and retry
        if response.status_code == 504:
            print(f"Connection error when transferring server {server_id}. Attempting to forcefully stop the server.", 1)
            
            # Get server identifier
            server_identifier = server_info['attributes']['identifier']
            
            # Create client API headers with client key
            client_headers = {
                "Authorization": f"Bearer {PTERODACTYL_CLIENT_KEY}",
                "Accept": "application/json",
                "Content-Type": "application/json"
            }
            
            # Send kill command to the server using identifier and client API
            kill_url = f"{PTERODACTYL_URL}api/client/servers/{server_identifier}/power"
            kill_data = {"signal": "kill"}
            
            try:
                kill_response = requests.post(kill_url, headers=client_headers, json=kill_data, timeout=30)
                print(f"Force stop command sent to server {server_id} ({server_identifier}). Status: {kill_response.status_code}", 1)
                
                # Wait a moment for the server to fully stop
                time.sleep(5)
                
                # Try the transfer again
                print(f"Retrying transfer for server {server_id}", 1)
                response = requests.post(
                    transfer_url, 
                    headers=HEADERS, 
                    json=transfer_data, 
                    timeout=60
                )
            except Exception as e:
                print(f"Error stopping server {server_id}: {str(e)}", 2)
        
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

def get_all_servers():
    """
    Returns a list of all servers from the Pterodactyl API.
    
    Makes a GET request to /api/application/servers to fetch all servers.
    
    Returns:
        list: List of server objects
        None: If the request fails
    """
    response = safe_requests.get(f"{PTERODACTYL_URL}api/application/servers?per_page=100000", headers=HEADERS, timeout=60)
    if response.status_code == 200:
        return response.json()['data']
    return None


def get_server(server_id: int):
    """
    Get detailed information about a specific server.
    
    This is an alias for get_server_information to maintain compatibility
    with existing code during the restructuring process.
    
    Args:
        server_id: ID of the server
        
    Returns:
        dict: Server information
        None: If the server is not found
    """
    return get_server_information(server_id)

def delete_server(server_id: int):
    """
    Deletes a server through Pterodactyl API.
    
    Args:
        server_id: ID of the server to delete
        
    Returns:
        bool: True if successful, False otherwise
    """
    response = requests.delete(f"{PTERODACTYL_URL}api/application/servers/{server_id}", headers=HEADERS, timeout=60)
    if response.status_code == 204:
        return True
    return False
