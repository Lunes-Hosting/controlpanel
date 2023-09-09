from flask import Blueprint, request, render_template, redirect, url_for, session, flash
import sys
sys.path.append("..") 
from scripts import *
from products import products
import threading
servers = Blueprint('servers', __name__)

@servers.route('/')
def servers_index():
    if 'email' not in session:
        return redirect(url_for("user.login_user"))
    after_request(session=session, request=request.environ, require_login=True)
    if 'pterodactyl_id' in session:
        id = session['pterodactyl_id']
    else:
        id = get_ptero_id(session['email'])
        session['pterodactyl_id'] = id

    cnx = mysql.connector.connect(
        host=HOST,
        user=USER,
        password=PASSWORD,
        database=DATABASE
        )
    cursor = cnx.cursor(buffered=True)
    
    query = f"Select email_verified_at FROM users where email = %s"
    cursor.execute(query, (session['email'],))
    results = cursor.fetchone()
    servers = []
    cnx.commit()
    if results[0] == None:
        verified=False
    else:
        verified=True
        servers = list_servers(id[0])
    
    return render_template('servers.html', servers=servers, verified=verified)

@servers.route('/<server_id>')
def server(server_id):
    if 'email' not in session:
        return redirect(url_for("user.login_user"))
    after_request(session=session, request=request.environ, require_login=True)
    resp = requests.get(f"{PTERODACTYL_URL}api/application/servers/{int(server_id)}", headers=HEADERS).json()
    cnx = mysql.connector.connect(
    host=HOST,
    user=USER,
    password=PASSWORD,
    database=DATABASE
    )

    cursor = cnx.cursor()
    query = f"SELECT pterodactyl_id FROM users where email = %s"
    print(session)
    cursor.execute(query, (session['email'],))
    rows = cursor.fetchone()
    cursor.close()
    cnx.close()
    

    if resp['attributes']['user'] == rows[0]:

        if 'pterodactyl_id' in session:
            id = session['pterodactyl_id']
        else:
            id = get_ptero_id(session['email'])
            session['pterodactyl_id'] = id

        
        servers = list_servers(id[0])

        products_local = list(products)
        for server in servers:
            if server['attributes']['user'] == id[0]:

                if server['attributes']['limits']['memory'] == 128:
                    print("yes")
                    
                    products_local.remove(products[0])
                    break
        

        info = get_server_information(server_id)
        return render_template('server.html', info=info, products=products_local)
    else:
        return "You cant view this server you dont own it!"

    
@servers.route("/create")
def create_server():
    if 'email' not in session:
        return redirect(url_for("user.login_user"))
    after_request(session=session, request=request.environ, require_login=True)
    

    if 'pterodactyl_id' in session:
        id = session['pterodactyl_id']
    else:
        id = get_ptero_id(session['email'])
        session['pterodactyl_id'] = id
    

    query = f"Select email_verified_at FROM users where email = %s"
    results = use_database(query, (session['email'],))
    if results[0] == None:
        return redirect(url_for('servers.servers_index'))
    
    servers = list_servers(id[0])
    nodes = get_nodes()
    eggs = get_eggs()
    products_local = list(products)
    for server in servers:
        if server['attributes']['user'] == id[0]:

            if server['attributes']['limits']['memory'] == 128:
                print("yes")
                
                products_local.remove(products[0])
                break
    return render_template('create_server.html', eggs=eggs, nodes=nodes, products=products_local)

@servers.route("/delete/<server_id>")
def delete_server(server_id):
    if 'email' not in session:
        return redirect(url_for("user.login_user"))
    after_request(session=session, request=request.environ, require_login=True)
    
    resp = requests.get(f"{PTERODACTYL_URL}api/application/servers/{int(server_id)}", headers=HEADERS).json()
    cnx = mysql.connector.connect(
    host=HOST,
    user=USER,
    password=PASSWORD,
    database=DATABASE
    )

    cursor = cnx.cursor()
    query = f"SELECT pterodactyl_id FROM users where email = %s"
    cursor.execute(query, (session['email'],))
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
    if 'email' not in session:
        return redirect(url_for("user.login_user"))
    after_request(session=session, request=request.environ, require_login=True)
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
        products_local = list(products)
        
        servers = list_servers(id[0])
        products_local = list(products)
        for server in servers:
            if server['attributes']['user'] == id[0]:

                if server['attributes']['limits']['memory'] == 128:
                    print("yes")
                    
                    products_local.remove(products[0])
                    break
    found_product = False
    for product in products_local:
        if product['id'] == int(request.form.get('plan')):
            found_product = True
            main_product = product
            credits_used = main_product['price'] / 30 / 24
            res = remove_credits(session['email'], credits_used)
            if res == "SUSPEND":
                flash("You are out of credits")
                return redirect(url_for('servers.servers_index'))
    
    if found_product == False:
        return "You already have free server"
    body = {
    "name": request.form['name'],
    "user": session['pterodactyl_id'][0],
    "egg": egg_id,
    "docker_image": docker_image,
    "startup": startup,

    "limits": main_product['limits'],
    
    "feature_limits": main_product['product_limits'],
    "allocation": {
    "default": alloac_id
    },
    "environment": {
        "BUILD_NUMBER": "latest",
        "MINECRAFT_VERSION": "latest",
        "MEMORY_OVERHEAD": "1500",
        "SERVER JAR FILE": "server.jar",
    }
    }
    
    res = requests.post(f"{PTERODACTYL_URL}api/application/servers", headers=HEADERS, json=body)
    print(res.json())
    return redirect(url_for('servers.servers_index'))

@servers.route('/update/<server_id>', methods=['POST'])
def update_server_submit(server_id):
    if 'email' not in session:
        return redirect(url_for("user.login_user"))
    after_request(session=session, request=request.environ, require_login=True)
    
    resp = requests.get(f"{PTERODACTYL_URL}api/application/servers/{int(server_id)}", headers=HEADERS).json()
    cnx = mysql.connector.connect(
    host=HOST,
    user=USER,
    password=PASSWORD,
    database=DATABASE
    )

    cursor = cnx.cursor()
    query = f"SELECT pterodactyl_id FROM users where email = %s"
    cursor.execute(query, (session['email'],))
    rows = cursor.fetchone()
    cursor.close()
    cnx.close()
    print(rows[0])
    if resp['attributes']['user'] == rows[0]:
        for product in products:
            if product['id'] == int(request.form.get('plan')):
                main_product = dict(product)
                credits_used = main_product['price'] / 30 / 24
                res = remove_credits(session['email'], credits_used)
                if res == "SUSPEND":
                    flash("You are out of credits")
                    return redirect(url_for('servers.servers_index'))
        body=main_product['limits']
        body["feature_limits"] = main_product['product_limits']
        body['allocation'] = resp['attributes']['allocation']
        print(body)
        resp2 = requests.patch(f"{PTERODACTYL_URL}api/application/servers/{int(server_id)}/build", headers=HEADERS, json=body)
        print(resp2.text)
        return redirect(url_for('servers.servers_index'))
    else:
        return "You can't update this server you dont own it!"