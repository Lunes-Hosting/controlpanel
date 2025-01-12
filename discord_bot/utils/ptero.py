import requests
import config
from config import PTERODACTYL_ADMIN_KEY, PTERODACTYL_URL
from utils.logger import logger
from managers.database_manager import DatabaseManager
headers = {
    "Authorization": f"Bearer {PTERODACTYL_ADMIN_KEY}",
    'Accept': 'application/json',
    'Content-Type': 'application/json'
}

class PteroAPI():

 def get_all_servers():
    try:
        response = requests.get(f"{PTERODACTYL_URL}api/application/servers?per_page=100000", headers=headers)
        response.raise_for_status()
        data = response.json()
        
        total_servers = data['meta']['pagination']['total']
        total_servers_suspended = sum(1 for server in data['data'] if server['attributes'].get('suspended', False))
        return {"servers": total_servers, "servers_suspended": total_servers_suspended}
    except Exception as e:
        logger.error(f"Error fetching total servers: {str(e)}")
        return f"Error fetching total servers"


