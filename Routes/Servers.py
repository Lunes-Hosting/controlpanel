"""
Server Management Module
=====================

This module handles all server-related operations in the Pterodactyl Control Panel,
including server creation, modification, deletion, and transfer functionality.

Templates Used:
-------------
- servers/index.html: Server list overview
- servers/server.html: Individual server details
- servers/create_server.html: Server creation form
- servers/transfer_server.html: Server transfer interface

Database Tables Used:
------------------
- users: User verification status
- servers: Server configurations
- nodes: Available server locations
- credits: User credit tracking

External Services:
---------------
- Pterodactyl Panel API:
  - Server management
  - Resource allocation
  - Node management
- ReCAPTCHA: Form protection

Session Requirements:
------------------
- email: User's email address
- pterodactyl_id: User's panel ID
- verified: Email verification status

Cache Keys:
---------
- server_[id]: Server configuration cache
- node_[id]: Node information cache

Resource Management:
-----------------
Handles allocation of:
- Memory (RAM)
- Disk Space
- CPU Cores
- Bandwidth
"""

import asyncio
import datetime
from flask import Blueprint, request, render_template, session, flash, redirect, url_for, jsonify
import sys
import requests
from threadedreturn import ThreadWithReturnValue
from security import safe_requests
import secrets

sys.path.append("..")
from managers.authentication import login_required, admin_required
from managers.user_manager import get_ptero_id, get_id, get_name, check_if_user_suspended, get_user_verification_status_and_suspension_status
from managers.server_manager import get_nodes, get_eggs, get_server_information, improve_list_servers, get_node_allocation, transfer_server
from managers.credit_manager import get_credits, convert_to_product, use_credits, remove_credits, add_credits
from managers.logging import webhook_log
from managers.utils import HEADERS
from products import products
from managers.database_manager import DatabaseManager
from config import PTERODACTYL_URL, RECAPTCHA_SECRET_KEY, RECAPTCHA_SITE_KEY

servers = Blueprint('servers', __name__)


def get_user_verification_and_ptero_id(email):
        
    result = DatabaseManager.execute_query(
        "SELECT email_verified_at, pterodactyl_id FROM users WHERE email = %s",
        (email,)
    )

    if result:
        verified = False
        if result[0] is not None:
            verified = True

        return (verified, result[1])
    
    return (None, None)

def get_user_verification_ptero_id_and_credits(email):
    #credits
    result = DatabaseManager.execute_query(
        "SELECT email_verified_at, pterodactyl_id, credits FROM users WHERE email = %s",
        (email,)
    )

    if result:
        verified = False
        if result[0] is not None:
            verified = True

        return (verified, result[1], result[2])
    
    return (None, None, None)

def get_user_verification_status(email):
    """
    Check if user's email is verified.
    
    Args:
        email: User's email address
        
    Database Queries:
        - Check email_verified_at in users table
        
    Returns:
        bool: True if verified, False otherwise
        
    Related Tables:
        - users: Stores verification status
    """
    result = DatabaseManager.execute_query(
        "SELECT email_verified_at FROM users WHERE email = %s",
        (email,)
    )
    return result[0] is not None if result else False

def get_user_ptero_id(session):
    """
    Get or set pterodactyl_id in session.
    
    Args:
        session: Flask session object
        
    Process:
        1. Check session for existing ID
        2. Query database if not found
        3. Cache ID in session
        
    Returns:
        tuple: (user_id, panel_id)
        
    Related Functions:
        - get_ptero_id(): Database query helper
    """
    if 'pterodactyl_id' not in session:
        ptero_id = get_ptero_id(session['email'])
        session['pterodactyl_id'] = ptero_id
    return session['pterodactyl_id']

def verify_server_ownership(server_id, user_email):
    """
    Verify server ownership.
    
    Args:
        server_id: Pterodactyl server ID
        user_email: User's email address
        
    API Calls:
        - Pterodactyl: Get server details
        
    Process:
        1. Fetch server details
        2. Get user's panel ID
        3. Compare owner IDs
        
    Returns:
        bool: True if user owns server
        
    Related Functions:
        - get_ptero_id(): Gets user's panel ID
    """
    resp = safe_requests.get(f"{PTERODACTYL_URL}api/application/servers/{int(server_id)}", headers=HEADERS, timeout=60).json()
    ptero_id = get_ptero_id(user_email)
    return resp['attributes']['user'] == ptero_id[0] if ptero_id else False

def verify_server_ownership_by_ptero_id(server_id, ptero_id):
    """
    Verify server ownership.
    
    Args:
        server_id: Pterodactyl server ID
        user_email: User's email address
        
    API Calls:
        - Pterodactyl: Get server details
        
    Process:
        1. Fetch server details
        2. Get user's panel ID
        3. Compare owner IDs
        
    Returns:
        bool: True if user owns server
        
    Related Functions:
        - get_ptero_id(): Gets user's panel ID
    """
    resp = safe_requests.get(f"{PTERODACTYL_URL}api/application/servers/{int(server_id)}", headers=HEADERS, timeout=60).json()
    try:
        return resp['attributes']['user'] == ptero_id if ptero_id else False
    except:
        return False


@servers.route('/<server_id>')
@login_required
def server(server_id):
    """
    Display detailed server information.
    
    Args:
        server_id: Pterodactyl server ID
        
    Templates:
        - servers/server.html: Server details
        
    API Calls:
        - Pterodactyl: Get server details
        - Pterodactyl: Get resource usage
        
    Process:
        1. Verify authentication
        2. Check server ownership
        3. Fetch server details
        4. Get resource utilization
        
    Returns:
        template: servers/server.html with:
            - info: Server configuration
            - usage: Resource utilization
            - limits: Resource allocations
            - products: Upgrade options
            
    Related Functions:
        - get_server_info(): Gets details
        - get_resource_usage(): Gets utilization
    """

    
    verified, ptero_id, credits = get_user_verification_ptero_id_and_credits(session["email"])
    if not verify_server_ownership_by_ptero_id(server_id, ptero_id):
        return "You can't view this server - you don't own it!"
        
    
    response = improve_list_servers(ptero_id) #improved
    
    # Extract servers from the response
    servers_list = []
    if response and 'attributes' in response and 'relationships' in response['attributes']:
        if 'servers' in response['attributes']['relationships']:
            servers_list = response['attributes']['relationships']['servers']['data']
    
    # Filter available products
    products_local = [p for p in products if p['enabled']]
    for server in servers_list:
        if (server['attributes']['user'] == ptero_id and 
            server['attributes']['limits']['memory'] == 128):
            products_local.remove(products[0])
            break
            
    info = get_server_information(server_id)
    nodes = get_nodes()
    
    # Get current product for the server
    current_product = convert_to_product(info)
    
    return render_template('server.html', info=info, products=products_local, nodes=tuple(nodes), verified=verified, credits=int(credits), current_product=current_product)

@servers.route("/create", methods=["GET"])
@login_required
def create_server():
    """
    Display server creation form.
    
    Templates:
        - servers/create_server.html: Creation form
        
    API Calls:
        - Pterodactyl: List available eggs
        - Pterodactyl: List available nodes
        
    Process:
        1. Verify authentication
        2. Check email verification
        3. Load server options
        4. Calculate resource limits
        
    Returns:
        template: create_server.html with:
            - eggs: Available server types
            - nodes: Available locations
            - products: Resource plans
            - limits: User's resource limits
            
    Related Functions:
        - get_eggs(): Lists server types
        - get_nodes(): Lists locations
    """

    if 'pterodactyl_id' in session:
        ptero_id = session['pterodactyl_id']
    else:
        ptero_id = get_ptero_id(session['email']) #uses db
        session['pterodactyl_id'] = ptero_id

    # Check email verification
    verified = get_user_verification_status(session['email']) #uses db
    if not verified:
        return redirect(url_for('user.index'))

    # Enforce 15-minute cooldown from registration
    created_at_row = DatabaseManager.execute_query(
        "SELECT created_at FROM users WHERE email = %s",
        (session['email'],)
    )
    if created_at_row and created_at_row[0] is not None:
        try:
            created_at = created_at_row[0]
            now = datetime.datetime.utcnow()
            # If created_at is timezone-aware, normalize by removing tzinfo for comparison
            if hasattr(created_at, 'tzinfo') and created_at.tzinfo is not None:
                created_at = created_at.replace(tzinfo=None)
            remaining = (created_at + datetime.timedelta(minutes=15)) - now
            if remaining.total_seconds() > 0:
                minutes_left = int(remaining.total_seconds() // 60) + (1 if remaining.total_seconds() % 60 else 0)
                flash(f"You can create servers {minutes_left} minute(s) after registering. Please try again later.")
                return redirect(url_for('user.index'))
        except Exception:
            # If any parsing error occurs, fail open (allow) to avoid blocking legitimate users
            pass

    response = improve_list_servers(ptero_id[0]) #improved
    
    # Extract servers from the response
    servers_list = []
    if response and 'attributes' in response and 'relationships' in response['attributes']:
        if 'servers' in response['attributes']['relationships']:
            servers_list = response['attributes']['relationships']['servers']['data']

    # Enforce max 2 servers for non-client users
    role_row = DatabaseManager.execute_query(
        "SELECT role FROM users WHERE email = %s",
        (session['email'],)
    )
    if role_row and role_row[0] != 'client' and role_row[0] != 'admin' and role_row[0] != 'support':
        user_server_count = len(servers_list)
        if user_server_count >= 2:
            flash("You have reached the maximum of 2 servers for non-client accounts.")
            return redirect(url_for('user.index'))

    nodes = get_nodes()
    project_id = request.args.get('project_id')
    if project_id is None:
        eggs = get_eggs()
        environment = None
        # Use session.pop with a default value to avoid KeyError
        session.pop('project_id', None)
    else:
        eggs, environment = get_autodeploy_info(project_id)
        environment['egg_id'] = eggs[0]['egg_id']
        session['project_id'] = project_id

    products_local = list(products)
    for _product in products_local:
        if _product['enabled'] == False:
            products_local.remove(_product)
    for server_inc in servers_list:
        if server_inc['attributes']['user'] == ptero_id[0]:
            if server_inc['attributes']['limits']['memory'] == 128:
                products_local.remove(products[0])
                break
    return render_template('create_server.html', eggs=eggs, nodes=nodes, products=products_local, environment=environment,
                        RECAPTCHA_PUBLIC_KEY=RECAPTCHA_SITE_KEY)

@servers.route("/delete/<server_id>")
@login_required
def delete_server(server_id):
    """
    Delete a server.
    
    Args:
        server_id: Pterodactyl server ID
        
    API Calls:
        - Pterodactyl: Delete server
        
    Database Queries:
        - Update user resources
        - Log server deletion
        
    Process:
        1. Verify authentication
        2. Check server ownership
        3. Delete from panel
        4. Update resource counts
        5. Log deletion
        
    Returns:
        redirect: To servers list with:
            - success: Deletion status
            - message: Result details
            
    Related Functions:
        - delete_from_panel(): Removes server
        - update_resources(): Updates limits
    """
    resp = safe_requests.get(f"{PTERODACTYL_URL}api/application/servers/{int(server_id)}", headers=HEADERS, timeout=60).json()
    
    # Get user's pterodactyl ID
    ptero_id = DatabaseManager.execute_query(
        "SELECT pterodactyl_id FROM users WHERE email = %s",
        (session['email'],)
    )
    try:
        if resp['attributes']['user'] == ptero_id[0]:
            webhook_log(f"Server with id: {server_id} was deleted by user", 0, database_log=True)
            requests.delete(f"{PTERODACTYL_URL}api/application/servers/{int(server_id)}", headers=HEADERS, timeout=60)
            return redirect(url_for('user.index'))
        else:
            webhook_log(f"Server with id {server_id} attempted deleted from user {session["email"]}", 1, database_log=True)
            return "You can't delete this server you dont own it!"
    except KeyError:
        webhook_log(f"Server with id {server_id} not found", 1)
        print(resp, "server delete not found!!!!!!")
        return "Server not found"    
@servers.route('/create/submit', methods=['POST'])
@login_required
def create_server_submit():
    """
    Handle server creation form submission.
    """
    recaptcha_response = request.form.get('g-recaptcha-response')
    data = {
        'secret': RECAPTCHA_SECRET_KEY,
        'response': recaptcha_response
    }

    response = requests.post('https://challenges.cloudflare.com/turnstile/v0/siteverify', data=data, timeout=60)
    result = response.json()
    if not result['success']:
        flash("Failed captcha please try again")
        return redirect(url_for('servers.create_server'))

    # Enforce max 2 servers for non-client users
    role_row = DatabaseManager.execute_query(
        "SELECT role FROM users WHERE email = %s",
        (session['email'],)
    )
    if role_row and role_row[0] != 'client':
        # Count current servers
        ptero_id_local = get_ptero_id(session['email'])[0]
        response_local = improve_list_servers(ptero_id_local)
        current_servers = []
        if response_local and 'attributes' in response_local and 'relationships' in response_local['attributes']:
            if 'servers' in response_local['attributes']['relationships']:
                current_servers = response_local['attributes']['relationships']['servers']['data']
        if len(current_servers) >= 2:
            flash("You have reached the maximum of 2 servers for non-client accounts.")
            return redirect(url_for('user.index'))

    # Enforce 15-minute cooldown from registration
    created_at_row = DatabaseManager.execute_query(
        "SELECT created_at FROM users WHERE email = %s",
        (session['email'],)
    )
    if created_at_row and created_at_row[0] is not None:
        try:
            created_at = created_at_row[0]
            now = datetime.datetime.utcnow()
            if hasattr(created_at, 'tzinfo') and created_at.tzinfo is not None:
                created_at = created_at.replace(tzinfo=None)
            if (now - created_at) < datetime.timedelta(minutes=15):
                remaining = (created_at + datetime.timedelta(minutes=15)) - now
                minutes_left = int(remaining.total_seconds() // 60) + (1 if remaining.total_seconds() % 60 else 0)
                flash(f"You must wait {minutes_left} more minute(s) after registering before creating a server.")
                return redirect(url_for('user.index'))
        except Exception:
            pass

    # Validate required form fields
    if not request.form.get('name'):
        flash("Server name is required")
        return redirect(url_for('servers.create_server'))
        
    if not request.form.get('node_id'):
        flash("Please select a node")
        return redirect(url_for('servers.create_server'))
        
    if not request.form.get('egg_id'):
        flash("Please select a software")
        return redirect(url_for('servers.create_server'))
        
    # Validate plan selection
    if not request.form.get('plan'):
        flash("Please select a plan")
        return redirect(url_for('servers.create_server'))

    node_id = request.form['node_id']
    egg_id = request.form['egg_id']
    
    # Check if we have a project_id in the session (set during create_server)
    project_id = session.get('project_id')
    
    if project_id:
        # Retrieve environment variables for this project
        egg_info, environment = get_autodeploy_info(project_id)
        docker_image = egg_info[0]['docker_image']
        startup = egg_info[0]['startup']
        # Clear the session variable to avoid keeping it for future requests
        session.pop('project_id', None)
    else:
        eggs = get_eggs()

        for egg in eggs:
            if int(egg['egg_id']) == int(egg_id):
                docker_image = egg['docker_image']
                startup = egg['startup']
                break

        # Use default environment variables
        environment = {
            "BUILD_NUMBER": "latest",
            "MINECRAFT_VERSION": "latest",
            "MEMORY_OVERHEAD": "1500",
            "SERVER_JARFILE": "server.jar",
            "VANILLA_VERSION": "latest"
        }
    

    resp = safe_requests.get(f"{PTERODACTYL_URL}api/application/nodes/{node_id}/allocations?per_page=100000",
                        headers=HEADERS, timeout=60).json()
    
    allocs = resp['data']
    secrets.SystemRandom().shuffle(allocs)
    alloac_id = None
    for allocation in allocs:
        if not allocation['attributes']['assigned']:
            alloac_id = allocation['attributes']['id']
            break
            
    if alloac_id is None:
        flash("Selected node is full. Please choose a different node.")
        return redirect(url_for('servers.create_server'))
            
    ptero_id = get_ptero_id(session['email'])[0]
    response = improve_list_servers(ptero_id) #improved
    
    # Extract servers from the response
    servers_list = []
    if response and 'attributes' in response and 'relationships' in response['attributes']:
        if 'servers' in response['attributes']['relationships']:
            servers_list = response['attributes']['relationships']['servers']['data']
        
    products_local = list(products)
    for server_inc in servers_list:
        if server_inc['attributes']['user'] == ptero_id:
            if server_inc['attributes']['limits']['memory'] == 128:
                products_local.remove(products[0])
                break

    found_product = False
    try:
        plan_id = int(request.form.get('plan'))
        for product in products_local:
            if product['id'] == plan_id:
                found_product = True
                main_product = product
                credits_used = main_product['price'] / 30 / 24
                res = remove_credits(session['email'], credits_used)
                if res == "SUSPEND":
                    flash("You are out of credits")
                    return redirect(url_for('user.index'))
    except (ValueError, TypeError):
        flash("Please select a valid plan")
        return redirect(url_for('servers.create_server'))

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
        "environment": environment  # Use the environment variables we determined earlier
    }

    res: dict = requests.post(f"{PTERODACTYL_URL}api/application/servers", headers=HEADERS, json=body, timeout=60).json()

    error = res.get('errors', None)
    if error is not None:
        flash("Failed to create server try a different node or open a ticket")
        add_credits(session['email'], credits_used, False)
        webhook_log(f"Server was just created: ```{res}```", non_embed_message="<@491266830674034699>", database_log=True)
    webhook_log(f"Server was just created: ```{res}```", database_log=True)
    return redirect(url_for('user.index'))


@servers.route('/adminupdate/<server_id>', methods=['POST'])
@admin_required
def admin_update_server_submit(server_id):
    """
    Update server configuration (admin only).
    
    Args:
        server_id: Pterodactyl server ID
        
    API Calls:
        - Pterodactyl: Update server
        
    Database Queries:
        - Verify admin status
        - Update resource allocation
        
    Process:
        1. Verify admin status
        2. Update configuration
        3. Log changes
        
    Returns:
        redirect: To server page with:
            - success: Update status
            - message: Result details
            
    Related Functions:
        - update_server(): Updates config
        - log_admin_action(): Records change
    """

    res = update_server_submit(server_id, True)
    return redirect(url_for('admin.admin_server', server_id=server_id))


@servers.route('/update/<server_id>', methods=['POST'])
@login_required
def update_server_submit(server_id, bypass_owner_only: bool = False):
    """
    Update server configuration.
    """
    resp_thread = ThreadWithReturnValue(target=requests.get, args=(f"{PTERODACTYL_URL}api/application/servers/{int(server_id)}", ), kwargs={"headers": HEADERS})
    resp_thread.start()
    webhook_log(f"Server update with id: {server_id} was attempted", database_log=True)
    
    if check_if_user_suspended(str(get_ptero_id(session['email'])[0])):
        return ("Your Account has been suspended for breaking our TOS, if you believe this is a mistake you can submit "
                "apeal at panel@lunes.host")

    ptero_id = get_ptero_id(session['email'])[0]
    response = improve_list_servers(ptero_id) #improved
    
    # Extract servers from the response
    servers_list = []
    if response and 'attributes' in response and 'relationships' in response['attributes']:
        if 'servers' in response['attributes']['relationships']:
            servers_list = response['attributes']['relationships']['servers']['data']
        
    products_local = list(products)
    for server_inc in servers_list:
        if server_inc['attributes']['user'] == ptero_id:
            if server_inc['attributes']['limits']['memory'] == 128 and bypass_owner_only is False:
                products_local.remove(products[0])
                break
        elif bypass_owner_only is False:
            return "You can't update this server you dont own it!"

    found_product = False
    selected_plan_id = int(request.form.get('plan'))
    for product in products_local:
        if product['id'] == selected_plan_id:
            found_product = True
            main_product = product
            credits_used = main_product['price'] / 30 / 24
            
            # Check if this is a free plan (memory = 128MB)
            is_free_plan = main_product['limits']['memory'] == 128
            
            # Only check credits if not downgrading to free plan
            if bypass_owner_only is False and not is_free_plan:
                res = remove_credits(session['email'], credits_used)
                if res == "SUSPEND":
                    flash("You are out of credits")
                    return redirect(url_for('index'))

    if not found_product:
        return "You already have free server"

    resp = resp_thread.join().json()
    body = main_product['limits']
    body["feature_limits"] = main_product['product_limits']
    body['allocation'] = resp['attributes']['allocation']
    _resp2 = requests.patch(f"{PTERODACTYL_URL}api/application/servers/{int(server_id)}/build", headers=HEADERS,
                           json=body, timeout=60)
    return redirect(url_for('index'))

@servers.route('/transfer/<server_id>')
@login_required
def transfer_server_route(server_id):
    """
    Display server transfer page.
    
    Args:
        server_id: Pterodactyl server ID
        
    Templates:
        - servers/transfer_server.html: Transfer form
        
    API Calls:
        - Pterodactyl: Get server location
        - Pterodactyl: List available nodes
        
    Process:
        1. Verify ownership
        2. Get current location
        3. List valid targets
        4. Filter by capacity
        
    Returns:
        template: transfer_server.html with:
            - server_id: Server identifier
            - current: Current node
            - nodes: Available targets
            - limits: Transfer restrictions
            
    Related Functions:
        - get_nodes(): Lists locations
        - check_capacity(): Filters nodes
    """
    
    if not verify_server_ownership(server_id, session['email']):
        return "You can't transfer this server - you don't own it!"
    
    # Get server information
    info = get_server_information(server_id)
    current_node_id = info['attributes']['node']
    
    # Get node details to get the node name
    nodes = get_nodes()
    current_node_name = next((node['name'] for node in nodes if node['node_id'] == current_node_id), 'Unknown Node')
    
    # Get all nodes and filter out "full" nodes
    available_nodes = [
        node for node in nodes 
        if "full" not in node['name'].lower() and node['node_id'] != current_node_id
    ]
    
    return render_template('transfer_server.html', 
                           server_id=server_id, 
                           current_node=current_node_name, 
                           current_node_id=current_node_id,
                           nodes=available_nodes)

@servers.route('/transfer/<server_id>/submit', methods=['POST'])
@login_required
def transfer_server_submit(server_id):
    """
    Handle server transfer submission.
    
    Args:
        server_id: Pterodactyl server ID
        
    API Calls:
        - Pterodactyl: Transfer server
        
    Database Queries:
        - Log transfer
        - Update location
        
    Process:
        1. Verify ownership
        2. Validate target node
        3. Initiate transfer
        4. Monitor progress
        5. Update records
        
    Form Data:
        - node_id: Target location
        
    Returns:
        redirect: To server page with:
            - success: Transfer status
            - message: Result details
            
    Related Functions:
        - transfer_server(): Moves server
        - monitor_transfer(): Tracks progress
    """
    
    if not verify_server_ownership(server_id, session['email']):
        return "You can't transfer this server - you don't own it!", 403
    
    # Get target node from JSON data
    data = request.get_json()
    if not data or 'node_id' not in data:
        return "Missing node_id in request", 400
        
    node_id = data['node_id']
    
    # Attempt server transfer
    status_code = transfer_server(int(server_id), int(node_id))
    
    if status_code == 202:
        return jsonify({'message': 'Server transfer initiated successfully.'}), 200
    elif status_code == 400:
        return jsonify({'message': 'Selected node is full. Please choose a different node.'}), 400
    else:
        return jsonify({'message': 'An unexpected error occurred during server transfer.'}), 500
