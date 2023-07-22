from flask import Blueprint, request, render_template, redirect, url_for, session
import sys
sys.path.append("..") 
from scripts import *
from products import products
import json
servers = Blueprint('servers', __name__)

@servers.route('/')
def servers_index():
    if not 'email' in session:
        return redirect(url_for('user.login_user'))
        
    if 'pterodactyl_id' in session:
        id = session['pterodactyl_id']
    else:
        id = get_ptero_id(session['email'])
        session['pterodactyl_id'] = id
        
    servers = list_servers(id[0][0])
    return render_template('servers.html', servers=servers)

@servers.route('/<server_id>')
def server(server_id):
    print(server_id)
    info = get_server_information(server_id)
    print(info)
    return render_template('server.html', info=info)

@servers.route("/create")
def create_server():
    if not 'email' in session:
        return redirect(url_for('user.login_user'))
    

    if 'pterodactyl_id' in session:
        id = session['pterodactyl_id']
    else:
        id = get_ptero_id(session['email'])
        session['pterodactyl_id'] = id
        
    servers = list_servers(id[0][0])
    nodes = get_nodes()
    eggs = get_eggs()
    products_local = products
    for server in servers:

        if server['attributes']['user'] == id:
            print("mhm", 1, server['attributes']['name'], 2, server['attributes']['limits'],333333)
            if server['attributes']['limits']['memory'] == 128:
                print("yes")
                products_local.remove(products_local[0])
                break
    return render_template('create_server.html', eggs=eggs, nodes=nodes, products=products_local)

@servers.route("/delete/<server_id>")
def delete_server(server_id):
    if not 'email' in session:
        return redirect(url_for('user.login_user'))
    resp = requests.get(f"{PTERODACTYL_URL}api/application/servers/{int(server_id)}", headers=HEADERS).json()
    cnx = mysql.connector.connect(
    host=HOST,
    user=USER,
    password=PASSWORD,
    database=DATABASE
    )

    cursor = cnx.cursor()
    query = f"SELECT pterodactyl_id FROM users where email = '{session['email']}'"
    cursor.execute(query)
    rows = cursor.fetchone()
    cursor.close()
    cnx.close()
    print(rows[0])
    if resp['attributes']['user'] == rows[0]:
        resp = requests.delete(f"{PTERODACTYL_URL}api/application/servers/{int(server_id)}", headers=HEADERS)
        return redirect(url_for('servers.servers_index'))
    else:
        return "You can't delete this server you dont own it!"

@servers.route('/create/submit', methods=['POST'])
def create_server_submit():
    if not 'email' in session:
        return redirect(url_for('user.login_user'))
    node_id = request.form['node_id']
    egg_id = request.form['egg_id']
    eggs = get_eggs()
    for egg in eggs:
        # print(egg_id, egg['egg_id'])
        if int(egg['egg_id']) == int(egg_id):
            docker_image = egg['docker_image']
            startup = egg['startup']
            break

    resp = requests.get(f"{PTERODACTYL_URL}api/application/nodes/{node_id}/allocations", headers=HEADERS).json()
    for allocation in resp['data']:
        if allocation['attributes']['assigned'] == False:
            alloac_id = allocation['attributes']['id']
    for product in products:
        if product['id'] == int(request.form.get('plan')):
            main_product = product
    body = {
    "name": request.form['name'],
    "user": session['pterodactyl_id'][0][0],
    "egg": egg_id,
    "docker_image": docker_image,
    "startup": startup,

    "limits": main_product['limits'],
    
    "feature_limits": main_product['product_limits'],
    "allocation": {
    "default": alloac_id
    },
    "environment": {
        
    }
    }
    
    res = requests.post(f"{PTERODACTYL_URL}api/application/servers", headers=HEADERS, json=body)
    print(res.json())
    return redirect(url_for('servers.servers_index'))