import requests
import random
from scripts import *
import time
from security import safe_requests

DISCONTINUED_NODE = 8

# Define variables for the server and other parameters
base_url = 'https://ctrl.lunes.host/admin/servers/view/{server_id}/manage/transfer'

response = safe_requests.get(f"{PTERODACTYL_URL}api/application/servers?per_page=10000", headers=HEADERS).json()
    
for server in response['data']:
    
    product = convert_to_product(server)
    if product is None:
        server_id = server['attributes']['id']
        print(server['attributes']['name'], None)
        resp = safe_requests.get(f"{PTERODACTYL_URL}api/application/servers/{int(server_id)}", headers=HEADERS).json()
        main_product = products[1]
        body = main_product['limits']
        body["feature_limits"] = main_product['product_limits']
        body['allocation'] = resp['attributes']['allocation']
        print(body)
        resp2 = requests.patch(f"{PTERODACTYL_URL}api/application/servers/{int(server_id)}/build", headers=HEADERS,
                                json=body)
