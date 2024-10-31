from flask import Blueprint, request, render_template, session, flash
import sys
from threadedreturn import ThreadWithReturnValue
sys.path.append("..")
from scripts import *
from products import products

servers = Blueprint('servers', __name__)


@servers.route('/')
def servers_index():
    if 'email' not in session:
        return redirect(url_for("user.login_user"))
    after_request(session, request.environ, True)
    if 'pterodactyl_id' in session:
        ptero_id = session['pterodactyl_id']
    else:
        ptero_id = get_ptero_id(session['email'])
        session['pterodactyl_id'] = ptero_id

    cnx = mysql.connector.connect(
        host=HOST,
        user=USER,
        password=PASSWORD,
        database=DATABASE,
        charset='utf8mb4',
        collation='utf8mb4_unicode_ci'
    )
    cursor = cnx.cursor(buffered=True)

    query = f"Select email_verified_at FROM users where email = %s"
    cursor.execute(query, (session['email'],))
    results = cursor.fetchone()
    servers_list = []
    cnx.commit()
    if results[0] is None:
        verified = False
    else:
        verified = True
        servers_list = list_servers(ptero_id[0])

    return render_template('servers.html', servers=servers_list, verified=verified)


@servers.route('/<server_id>')
def server(server_id):
    if 'email' not in session:
        return redirect(url_for("user.login_user"))
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

    if resp['attributes']['user'] == rows[0]:

        if 'pterodactyl_id' in session:
            ptero_id = session['pterodactyl_id']
        else:
            ptero_id = get_ptero_id(session['email'])
            session['pterodactyl_id'] = ptero_id

        servers_list = list_servers(ptero_id[0])

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

        info = get_server_information(server_id)
        return render_template('server.html', info=info, products=products_local)
    else:
        return "You cant view this server you dont own it!"


@servers.route("/create")
def create_server():
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
    random.shuffle(resp['date'])
    for allocation in resp['data']:
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
    if 'email' not in session:
        return redirect(url_for("user.login_user"))
    if not is_admin(session['email']):
        return "YOUR NOT ADMIN BRO"
    res = update_server_submit(server_id, True)
    print(res)
    return redirect(url_for('admin.admin_server', server_id=server_id))


@servers.route('/update/<server_id>', methods=['POST'])
def update_server_submit(server_id, bypass_owner_only: bool = False):
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
