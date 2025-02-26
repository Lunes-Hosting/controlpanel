

from config import *
import requests
class PteroCache():
    
    def __init__(self) -> None:
        self.HEADERS = {"Authorization": f"Bearer {PTERODACTYL_ADMIN_KEY}",
           'Accept': 'application/json',
           'Content-Type': 'application/json'}
        self.CLIENT_HEADERS = {"Authorization": f"Bearer {PTERODACTYL_ADMIN_USER_KEY}",
                  'Accept': 'application/json',
                  'Content-Type': 'application/json'}
        self.egg_cache = None
        self.available_nodes = None
        self.all_nodes = None
        self.update_all()
        

    def update_all(self):
        self.update_egg_cache()
        self.update_node_cache()
    
    def update_egg_cache(self):
        disabled_nests = [15, 23]
        disabled_eggs = [55, 3]
        try:
            available_eggs = []
            nests = requests.get(f"{PTERODACTYL_URL}api/application/nests", headers=self.HEADERS, timeout=60)

            nests_data = nests.json()
            for nest in nests_data['data']:
                if nest['attributes']['id'] not in disabled_nests:
                    resp = requests.get(f"{PTERODACTYL_URL}api/application/nests/{nest['attributes']['id']}/eggs", headers=self.HEADERS, timeout=60)
                    data = resp.json()
                    for egg in data['data']:
                        attributes = egg['attributes']
                        if attributes['id'] not in disabled_eggs:
                            available_eggs.append(
                                {"egg_id": attributes['id'], "name": attributes['name'], "docker_image": attributes['docker_image'],
                                "startup": attributes['startup']}
                            )
            self.egg_cache = available_eggs
            return
        except KeyError as e:
            print(e, data, resp)
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
