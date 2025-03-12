

import threading
from config import *
import requests
class PteroCache():
    
    def __init__(self) -> None:
        self.HEADERS = {"Authorization": f"Bearer {PTERODACTYL_ADMIN_KEY}",
           'Accept': 'application/json',
           'Content-Type': 'application/json'}
        self.egg_cache = None
        self.available_nodes = None
        self.all_nodes = None
        self.update_all()
        

    def update_all(self):
        self.update_egg_cache()
        self.update_node_cache()
    

    def fetch_eggs(self, nest_id, results):
        """ Fetch eggs from a given nest and store the response """
        try:
            response = requests.get(
                f"{PTERODACTYL_URL}api/application/nests/{nest_id}/eggs",
                headers=self.HEADERS,
                timeout=60,
            )
            response.raise_for_status()
            results.append(response.json())  # Append the response data to the shared list
        except requests.RequestException as e:
            print(f"Error fetching eggs for nest {nest_id}: {e}")

    def update_egg_cache(self):
        disabled_nests = {15, 23}
        disabled_eggs = {55, 3}
        
        try:
            available_eggs = []
            nests = requests.get(f"{PTERODACTYL_URL}api/application/nests", headers=self.HEADERS, timeout=60)
            nests.raise_for_status()
            nests_data = nests.json()

            threads = []
            results = []  # Shared list for storing API responses
            
            for nest in nests_data.get('data', []):
                nest_id = nest['attributes']['id']
                if nest_id not in disabled_nests:
                    thread = threading.Thread(target=self.fetch_eggs, args=(nest_id, results))
                    threads.append(thread)
                    thread.start()

            for thread in threads:
                thread.join()

            # Process results after all threads complete
            for data in results:
                for egg in data.get('data', []):
                    attributes = egg['attributes']
                    if attributes['id'] not in disabled_eggs:
                        available_eggs.append({
                            "egg_id": attributes['id'],
                            "name": attributes['name'],
                            "docker_image": attributes['docker_image'],
                            "startup": attributes['startup']
                        })

            self.egg_cache = available_eggs

        except (requests.RequestException, KeyError) as e:
            print(f"Error updating egg cache: {e}")
            return None
            
    def update_node_cache(self):
        available_nodes = []
        all_nodes = []
        nodes = requests.get(f"{PTERODACTYL_URL}api/application/nodes", headers=self.HEADERS, timeout=60).json()
        for node in nodes['data']:
            all_nodes.append({"node_id": node['attributes']['id'], "name": node['attributes']['name']})
            if "full" not in node['attributes']['name'].lower():
                available_nodes.append({"node_id": node['attributes']['id'], "name": node['attributes']['name']})
        self.available_nodes = available_nodes
        self.all_nodes = all_nodes
