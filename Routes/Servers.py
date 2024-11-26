"""
Server management routes for the Pterodactyl Control Panel.
Handles all server-related operations including creation, modification, and deletion.

Session Variables Used:
    - email: str - User's email for authentication
    - pterodactyl_id: tuple[int] - User's Pterodactyl panel ID
    - verified: bool - Whether user's email is verified

URL Routes:
    - / : List all servers
    - /<server_id> : View specific server
    - /create : Server creation form
    - /create/submit : Handle server creation
    - /update/<server_id> : Update server configuration
    - /delete/<server_id> : Delete server
"""

from flask import Blueprint, request, render_template, session, flash
import sys
from threadedreturn import ThreadWithReturnValue
sys.path.append("..")
from scripts import *
from products import products

servers = Blueprint('servers', __name__)

def get_user_verification_status(email):
    """Helper function to check if user's email is verified"""
    query = "SELECT email_verified_at FROM users WHERE email = %s"
    result = use_database(query, (email,))
    return result[0] is not None if result else False

def get_user_ptero_id(session):
    """Helper function to get or set pterodactyl_id in session"""
    if 'pterodactyl_id' not in session:
        ptero_id = get_ptero_id(session['email'])
        session['pterodactyl_id'] = ptero_id
    return session['pterodactyl_id']

def verify_server_ownership(server_id, user_email):
    """Helper function to verify server ownership"""
    resp = requests.get(f"{PTERODACTYL_URL}api/application/servers/{int(server_id)}", headers=HEADERS).json()
    ptero_id = get_ptero_id(user_email)
    return resp['attributes']['user'] == ptero_id[0] if ptero_id else False

@servers.route('/')
def servers_index():
    """Lists all servers owned by the authenticated user."""
    if 'email' not in session:
        return redirect(url_for("user.login_user"))
        
    after_request(session, request.environ, True)
    ptero_id = get_user_ptero_id(session)
    verified = get_user_verification_status(session['email'])
    
    servers_list = []
    if verified:
        servers_list = list_servers(ptero_id[0])
        
    return render_template('servers.html', servers=servers_list, verified=verified)

@servers.route('/<server_id>')
def server(server_id):
    """Displays detailed information about a specific server."""
    if 'email' not in session:
        return redirect(url_for("user.login_user"))
        
    after_request(session, request.environ, True)
    
    if not verify_server_ownership(server_id, session['email']):
        return "You can't view this server - you don't own it!"
        
    ptero_id = get_user_ptero_id(session)
    servers_list = list_servers(ptero_id[0])
    
    # Filter available products
    products_local = [p for p in products if p['enabled']]
    for server in servers_list:
        if (server['attributes']['user'] == ptero_id[0] and 
            server['attributes']['limits']['memory'] == 128):
            products_local.remove(products[0])
            break
            
    info = get_server_information(server_id)
    return render_template('server.html', info=info, products=products_local)

@servers.route("/create")
def create_server():
    """
    Displays server creation form.
    
    Session Requirements:
        - email: User must be logged in
    
    Returns:
        template: create_server.html with:
            - eggs: Available server types
            - nodes: Available nodes
            - products: Available upgrade options
    """
    if 'email' not in session:
        return redirect(url_for("user.login_user"))
    after_request(session, request.environ, True)

    if 'pterodactyl_id' in session:
        ptero_id = session['pterodactyl_id']
    else:
        ptero_id = get_ptero_id(session['email'])
        session['pterodactyl_id'] = ptero_id

    query = f"Select email_verified_at FROM users where email = %s"
    results = use_database(query, (session['email'],))
    if results[0] is None:
        return redirect(url_for('servers.servers_index'))


    servers_list = list_servers(ptero_id[0])
    
    nodes = get_nodes()
    eggs = get_eggs()
    products_local = list(products)
    for _product in products_local:
        if _product['enabled'] == False:
            products_local.remove(_product)
    for server_inc in servers_list:
        if server_inc['attributes']['user'] == ptero_id[0]:

            if server_inc['attributes']['limits']['memory'] == 128:
                print("yes")

                products_local.remove(products[0])
                break
    return render_template('create_server.html', eggs=eggs, nodes=nodes, products=products_local,
                           RECAPTCHA_PUBLIC_KEY=RECAPTCHA_SITE_KEY)


@servers.route("/delete/<server_id>")
def delete_server(server_id):
    """
    Deletes a server.
    
    Session Requirements:
        - email: User must be logged in
    
    Args:
        server_id: Pterodactyl server ID
    
    Returns:
        redirect: To servers list on success
        str: Error message if user doesn't own server
    """
    if 'email' not in session:
        return redirect(url_for("user.login_user"))
    webhook_log(f"Server with id: {server_id} was deleted by user")
    after_request(session, request.environ, True)

    resp = requests.get(f"{PTERODACTYL_URL}api/application/servers/{int(server_id)}", headers=HEADERS).json()
    cnx = mysql.connector.connect(
        host=HOST,
        user=USER,
        password=PASSWORD,
        database=DATABASE,
        charset='utf8mb4',
        collation='utf8mb4_unicode_ci'
    )

    cursor = cnx.cursor()
    query = f"SELECT pterodactyl_id FROM users where email = %s"
    cursor.execute(query, (session['email'],))
    rows = cursor.fetchone()
    cursor.close()
    cnx.close()
    print(rows[0])
    if resp['attributes']['user'] == rows[0]:
        requests.delete(f"{PTERODACTYL_URL}api/application/servers/{int(server_id)}", headers=HEADERS)
        return redirect(url_for('servers.servers_index'))
    else:
        return "You can't delete this server you dont own it!"


@servers.route('/create/submit', methods=['POST'])
def create_server_submit():
    """
    Handles server creation form submission.
    
    Session Requirements:
        - email: User must be logged in
        - verified: Email must be verified
    
    Form Data:
        - name: Server name
        - egg_id: Server type/egg
        - location: Node location
        - memory: RAM allocation
        - disk: Disk space allocation
        - cpu: CPU allocation
        - g-recaptcha-response: ReCAPTCHA token
    
    Returns:
        redirect: To servers list on success
        template: Error page on failure
    """
    if 'email' not in session:
        return redirect(url_for("user.login_user"))
    recaptcha_response = request.form.get('g-recaptcha-response')
    data = {
        'secret': RECAPTCHA_SECRET_KEY,
        'response': recaptcha_response
    }

    response = requests.post('https://www.google.com/recaptcha/api/siteverify', data=data)
    result = response.json()
    if not result['success']:
        flash("Failed captcha please try again")
        return redirect(url_for("servers.create_server"))
    after_request(session, request.environ, True)
    node_id = request.form['node_id']
    egg_id = request.form['egg_id']
    eggs = get_eggs()
    for egg in eggs:
        # print(egg_id, egg['egg_id'])
        if int(egg['egg_id']) == int(egg_id):
            docker_image = egg['docker_image']
            startup = egg['startup']
            break

    resp = requests.get(f"{PTERODACTYL_URL}api/application/nodes/{node_id}/allocations?per_page=10000",
                        headers=HEADERS).json()
    
    allocs = resp['data']
    random.shuffle(allocs)
    for allocation in allocs:
        if not allocation['attributes']['assigned']:
            alloac_id = allocation['attributes']['id']
    ptero_id = get_ptero_id(session['email'])[0]
    servers_list = list_servers(ptero_id)
    products_local = list(products)
    for server_inc in servers_list:
        if server_inc['attributes']['user'] == ptero_id:

            if server_inc['attributes']['limits']['memory'] == 128:
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

    if not found_product:
        return "You already have free server"
    if check_if_user_suspended(str(get_ptero_id(session['email'])[0])):
        return ("Your Account has been suspended for breaking our TOS, if you believe this is a mistake you can submit "
                "apeal at panel@lunes.host")
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

    webhook_log(f"Server was just created: ```{res.json()}```")
    return redirect(url_for('servers.servers_index'))


@servers.route('/adminupdate/<server_id>', methods=['POST'])
def admin_update_server_submit(server_id):
    """
    Updates server configuration (admin only).
    
    Session Requirements:
        - email: User must be logged in
        - admin: User must be an admin
    
    Args:
        server_id: Pterodactyl server ID
    
    Returns:
        redirect: To server page on success
    """
    if 'email' not in session:
        return redirect(url_for("user.login_user"))
    if not is_admin(session['email']):
        return "YOUR NOT ADMIN BRO"
    res = update_server_submit(server_id, True)
    print(res)
    return redirect(url_for('admin.admin_server', server_id=server_id))


@servers.route('/update/<server_id>', methods=['POST'])
def update_server_submit(server_id, bypass_owner_only: bool = False):
    """
    Updates server configuration.
    
    Session Requirements:
        - email: User must be logged in
        - verified: Email must be verified
    
    Args:
        server_id: Pterodactyl server ID
        bypass_owner_only: Allow non-owners to modify (admin only)
    
    Form Data:
        - memory: New RAM allocation
        - disk: New disk space allocation
        - cpu: New CPU allocation
    
    Returns:
        redirect: To server page on success
        str: Error message on failure
    """
    if 'email' not in session:
        return redirect(url_for("user.login_user"))
    after_request(session, request.environ, True)
    webhook_log(f"Server update with id: {server_id} was attempted")
    resp = requests.get(f"{PTERODACTYL_URL}api/application/servers/{int(server_id)}", headers=HEADERS).json()
    if check_if_user_suspended(str(get_ptero_id(session['email'])[0])):
        return ("Your Account has been suspended for breaking our TOS, if you believe this is a mistake you can submit "
                "apeal at panel@lunes.host")

    ptero_id = get_ptero_id(session['email'])[0]
    servers_list = list_servers(ptero_id)
    products_local = list(products)
    for server_inc in servers_list:
        if server_inc['attributes']['user'] == ptero_id:

            if server_inc['attributes']['limits']['memory'] == 128 and bypass_owner_only is False:
                products_local.remove(products[0])
                break
        elif bypass_owner_only is False:
            return "You can't update this server you dont own it!"

    found_product = False
    for product in products_local:
        print(product['id'], request.form.get('plan'))
        if product['id'] == int(request.form.get('plan')):
            found_product = True
            main_product = product
            credits_used = main_product['price'] / 30 / 24
            if bypass_owner_only is False:
                res = remove_credits(session['email'], credits_used)
                if res == "SUSPEND":
                    flash("You are out of credits")
                    return redirect(url_for('servers.servers_index'))

    if not found_product:
        return "You already have free server"

    body = main_product['limits']
    body["feature_limits"] = main_product['product_limits']
    body['allocation'] = resp['attributes']['allocation']
    print(body)
    resp2 = requests.patch(f"{PTERODACTYL_URL}api/application/servers/{int(server_id)}/build", headers=HEADERS,
                           json=body)
    print(resp2.text)
    return redirect(url_for('servers.servers_index'))
