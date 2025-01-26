from config import *
import requests
import time
from threading import Lock

class PteroCache:
    def __init__(self) -> None:
        self.HEADERS = {
            "Authorization": f"Bearer {PTERODACTYL_ADMIN_KEY}",
            'Accept': 'application/json',
            'Content-Type': 'application/json'
        }
        self.CLIENT_HEADERS = {
            "Authorization": f"Bearer {PTERODACTYL_ADMIN_USER_KEY}",
            'Accept': 'application/json',
            'Content-Type': 'application/json'
        }
        self.egg_cache = None
        self.available_nodes = None
        self.all_nodes = None
        self._last_egg_update = 0
        self._last_node_update = 0
        self._cache_lock = Lock()
        self._egg_cache_ttl = 300  # 5 minutes
        self._node_cache_ttl = 300  # 5 minutes
        self.update_all()
    
    def _needs_update(self, last_update: float, ttl: int) -> bool:
        return time.time() - last_update > ttl
    
    def update_all(self):
        with self._cache_lock:
            self.update_egg_cache()
            self.update_node_cache()
    
    def update_egg_cache(self):
        if not self._needs_update(self._last_egg_update, self._egg_cache_ttl):
            return

        disabled_nests = [15]
        disabled_eggs = [55, 3]
        try:
            available_eggs = []
            nests = requests.get(
                f"{PTERODACTYL_URL}api/application/nests", 
                headers=self.HEADERS,
                timeout=10
            )
            nests.raise_for_status()
            nests_data = nests.json()
            
            for nest in nests_data['data']:
                if nest['attributes']['id'] not in disabled_nests:
                    resp = requests.get(
                        f"{PTERODACTYL_URL}api/application/nests/{nest['attributes']['id']}/eggs",
                        headers=self.HEADERS,
                        timeout=10
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    
                    for egg in data['data']:
                        attributes = egg['attributes']
                        if attributes['id'] not in disabled_eggs:
                            available_eggs.append({
                                "egg_id": attributes['id'],
                                "name": attributes['name'],
                                "docker_image": attributes['docker_image'],
                                "startup": attributes['startup']
                            })
            
            with self._cache_lock:
                self.egg_cache = available_eggs
                self._last_egg_update = time.time()
                
        except (requests.RequestException, KeyError) as e:
            print(f"Error updating egg cache: {e}")
            # Keep old cache on error
            if self.egg_cache is None:
                self.egg_cache = []
    
    def update_node_cache(self):
        if not self._needs_update(self._last_node_update, self._node_cache_ttl):
            return

        try:
            available_nodes = []
            all_nodes = []
            
            resp = requests.get(
                f"{PTERODACTYL_URL}api/application/nodes", 
                headers=self.HEADERS,
                timeout=10
            )
            resp.raise_for_status()
            data = resp.json()
            
            for node in data['data']:
                node_data = {
                    "node_id": node['attributes']['id'],
                    "name": node['attributes']['name']
                }
                all_nodes.append(node_data)
                
                # Only add nodes that aren't full
                alloc_resp = requests.get(
                    f"{PTERODACTYL_URL}api/application/nodes/{node['attributes']['id']}/allocations",
                    headers=self.HEADERS,
                    timeout=10
                )
                alloc_resp.raise_for_status()
                alloc_data = alloc_resp.json()
                
                if any(not alloc['attributes']['assigned'] for alloc in alloc_data['data']):
                    available_nodes.append(node_data)
            
            with self._cache_lock:
                self.available_nodes = available_nodes
                self.all_nodes = all_nodes
                self._last_node_update = time.time()
                
        except (requests.RequestException, KeyError) as e:
            print(f"Error updating node cache: {e}")
            # Keep old cache on error
            if self.available_nodes is None:
                self.available_nodes = []
            if self.all_nodes is None:
                self.all_nodes = []