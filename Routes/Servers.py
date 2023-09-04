from flask import Blueprint, request, render_template, redirect, url_for, session, flash
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
        
    update_last_seen(session['email'])
    update_ip(session['email'], request.headers.get('Cf-Connecting-Ip', request.remote_addr))
    cnx = mysql.connector.connect(
        host=HOST,
        user=USER,
        password=PASSWORD,
        database=DATABASE
        )
    cursor = cnx.cursor(buffered=True)
        
    query = f"Select email_verified_at FROM users where email='{session['email']}'"
    cursor.execute(query)
    results = cursor.fetchone()
    print(results)
    cnx.commit()
    if results[0] == None:
        verified=False
    else:
        verified=True
    servers = list_servers(id[0][0])
    return render_template('servers.html', servers=servers, verified=verified)

@servers.route('/<server_id>')
def server(server_id):
    if not 'email' in session:
        return redirect(url_for('user.login_user'))
    

    print(server_id)
    info = get_server_information(server_id)
    print(info)
    update_last_seen(session['email'])
    update_ip(session['email'], request.headers.get('Cf-Connecting-Ip', request.remote_addr))
    
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
    
    update_last_seen(session['email'])
    update_ip(session['email'], request.headers.get('Cf-Connecting-Ip', request.remote_addr))
    cnx = mysql.connector.connect(
        host=HOST,
        user=USER,
        password=PASSWORD,
        database=DATABASE
        )
    cursor = cnx.cursor(buffered=True)
        
    query = f"Select email_verified_at FROM users where email='{session['email']}'"
    cursor.execute(query)
    results = cursor.fetchone()
    print(results)
    cnx.commit()
    if results[0] == None:
        return redirect(url_for('servers.servers_index'))
    
    servers = list_servers(id[0][0])
    nodes = get_nodes()
    eggs = get_eggs()
    products_local = list(products)
    for server in servers:
        if server['attributes']['user'] == id[0][0]:

            if server['attributes']['limits']['memory'] == 128:
                print("yes")
                
                products_local.remove(products[0])
                break
    return render_template('create_server.html', eggs=eggs, nodes=nodes, products=products_local)

@servers.route("/delete/<server_id>")
def delete_server(server_id):
    if not 'email' in session:
        return redirect(url_for('user.login_user'))
    update_last_seen(session['email'])
    update_ip(session['email'], request.headers.get('Cf-Connecting-Ip', request.remote_addr))
    
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
    update_last_seen(session['email'])
    update_ip(session['email'], request.headers.get('Cf-Connecting-Ip', request.remote_addr))
    
    node_id = request.form['node_id']
    egg_id = request.form['egg_id']
    eggs = get_eggs()
    for egg in eggs:
        # print(egg_id, egg['egg_id'])
        if int(egg['egg_id']) == int(egg_id):
            docker_image = egg['docker_image']
            startup = egg['startup']
            break

    resp = requests.get(f"{PTERODACTYL_URL}api/application/nodes/{node_id}/allocations?per_page=1000", headers=HEADERS).json()
    for allocation in resp['data']:
        if allocation['attributes']['assigned'] == False:
            alloac_id = allocation['attributes']['id']
    for product in products:
        if product['id'] == int(request.form.get('plan')):
            main_product = product
            credits_used = main_product['price'] / 30 / 24
            res = remove_credits(session['email'], credits_used)
            if res == "SUSPEND":
                flash("You are out of credits")
                return redirect(url_for('servers.servers_index'))
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
    "headersment": {
        "BUILD_NUMBER": "latest",
        "MINECRAFT_VERSION": "latest",
        "MEMORY_OVERHEAD": "1500",
        "SERVER JAR FILE": "server.jar",
    }
    }
    
    res = requests.post(f"{PTERODACTYL_URL}api/application/servers", headers=HEADERS, json=body)
    print(res.json())
    return redirect(url_for('servers.servers_index'))
