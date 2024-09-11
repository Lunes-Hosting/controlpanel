import requests
from scripts import *
import time
import secrets

DISCONTINUED_NODE = 8

# Define variables for the server and other parameters
base_url = 'https://ctrl.lunes.host/admin/servers/view/{server_id}/manage/transfer'
headers = {
    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5',
    'Accept-Encoding': 'gzip, deflate, br',
    'Referer': 'https://ctrl.lunes.host/admin/servers/view/{server_id}/manage',
    'Content-Type': 'application/x-www-form-urlencoded',
    'Origin': 'https://ctrl.lunes.host',
    'Connection': 'keep-alive',
    'Cookie': '_tt_enable_cookie=1; _ttp=TQ0XrRJqTU0nXvZHOKSi8jKeUX6; cf_clearance=oZcXsbZDkfeKyU61FbU6k8wGOfEYw7nkPfOjElnI6Sg-1723417358-1.0.1.1-1hCFQseUPQwCbjSAtAEeznO6ThE4BOncNcDqC6.p9dIocyxVt7kujS8Ks0tQhcu7.g0s0xnxPPpKytSDQBOEIw; remember_web_59ba36addc2b2f9401580f014c7f58ea4e30989d=eyJpdiI6IkorbTZEdnBWMWNrU3hCSWtBYmpjUUE9PSIsInZhbHVlIjoiQUVzeEJGWUswRHhSUDJHTEhlZ3Y2UmNMNUVkR3hXalNUQmdVakkwbHVXQjVxaEVsZDkwYzMrL1FHbGNFMklxK01kRkNjd0tYcHJXOXl2TFU4Z2RqWVJNT09WTkNFQ25uMWIzck5Fdm5VbDkwTG9RcnFOcDhQOGFNNzhlMWtoLzJKYlBwYUk0dHZpNWRVbTN6STZBdHFCaEw4d2hldjNodlpBWkEyMXBvMDF4U0VoTmViMVRlKzNXSFFqTzR0eDdaWDNjRnh0Y2FsS0hUSCtJUjNGamwxRlRhMVgzd1AzSWRXRk1DQ0NzaEF0ND0iLCJtYWMiOiJlZTQxYmM3NTg2MzM4NDkwYzRkYzU1M2I2NDg5ZTc1ODE0Y2Q5YWRmZTQxOWI1NDRjMzhjNWU3N2Q0NGFiZThjIiwidGFnIjoiIn0%3D; XSRF-TOKEN=eyJpdiI6ImFBQW5rbGJDN21zZ2kvOUZBZi9vOUE9PSIsInZhbHVlIjoiOEFIOHhmWmhTeVhxSVhZNlJ1bG9CbHRCaThvR0FsL0dTOTNFdDZWTHJjTEs2MXJzVUtBa2daUW5kL3pvanoyM0RqSUFEd1dtQ1g0T1pIQ1k1THNDUTczekgzZmorMmR2U3g2cFcvMmdNRERHR1FZbGhoZVhrUTFQN1owU2pJQmwiLCJtYWMiOiIyNDBhM2EzN2ExN2Y4YmRkYTUwN2NlMTBlMmI3ZTY0NzJmYzAzNDNiMzI2NmEwZWYyN2VhYTAxMWQxNzYxZTFhIiwidGFnIjoiIn0%3D; pterodactyl_session=eyJpdiI6Ild3TjhsRityK3FuYUNsZ1IxeU5oZWc9PSIsInZhbHVlIjoidlJYMkJwOFNVbStoSnFmZS9jc1VsQzUyc0xJSjRCR2VIQ0Jua1Nwa0dZNXVOaEJCTjRzRnQ3aWF3YkNVZ3liSnZxQWpBK1B0cWlIUmhFRGJKd0wzT21hUHMwNjJac0F5bEFkb2xtUmtGOEJ5VW9nKzBKRi84Tkk1S1kzdDZNVWciLCJtYWMiOiIzN2EyZWJkOTdjOTY5YjE2MDE5Zjk3MjIzYjY2N2NmNjQzNDc0YTZlNWI1MjkyOGIwNDBlZjZhNGE1ZWUwMDYxIiwidGFnIjoiIn0%3D',
    'Upgrade-Insecure-Requests': '1',
    'Sec-Fetch-Dest': 'document',
    'Sec-Fetch-Mode': 'navigate',
    'Sec-Fetch-Site': 'same-origin',
    'Sec-Fetch-User': '?1',
    'TE': 'trailers'
}

# Define server and transfer details
server_ids = []  # Replace with your server IDs


response_servers = requests.get(f"{PTERODACTYL_URL}api/application/servers?per_page=10000", headers=HEADERS)
data = response_servers.json()
for server in data['data']:
    if server['attributes']['node'] == DISCONTINUED_NODE:
        server_ids.append(server['attributes']['id'])
        
print(server_ids)


nodes = get_nodes()

token = '3XZoznqyIXhXjArKJUIiYTETlGC6EOFZQpzSN3ze'

# Loop through each server and send the transfer request
for server_id in server_ids:
    node_id = secrets.choice(nodes)['node_id']
    url = base_url.format(server_id=server_id)
    headers['Referer'] = headers['Referer'].format(server_id=server_id)
    resp = requests.get(f"{PTERODACTYL_URL}api/application/nodes/{node_id}/allocations?per_page=1000",
                        headers=HEADERS).json()
    
    for allocation in resp['data']:
        if not allocation['attributes']['assigned']:
            alloac_id = allocation['attributes']['id']
    data = {
        'node_id': node_id,
        'allocation_id': alloac_id,
        '_token': token
    }
    
    response = requests.post(url, headers=headers, data=data)
    print(response.text)
    # Check the response
    if response.status_code == 200:
    
        print(f'Successfully transferred server {server_id}')
    else:
        print(f'Failed to transfer server {server_id}: {response.status_code} - {response.text}')
        
    time.sleep(5)
