"""
Admin Routes Module
=================

This module handles all administrative routes and functionality for the control panel.
It provides interfaces for user management, server administration, and support ticket handling.

Templates Used:
-------------
- admin/admin.html: Admin dashboard homepage
- admin/users.html: User management interface
- admin/servers.html: Server overview list
- admin/server.html: Individual server management
- admin/tickets.html: Support ticket management
- admin/user_servers.html: User's server list
- admin/manage_server.html: Detailed server management
- admin/nodes.html: Node management interface

Database Tables Used:
------------------
- users: User account management
- servers: Server configurations
- tickets: Support ticket tracking
- ticket_comments: Support communication
- credits: User credit tracking

External Services:
---------------
- Pterodactyl Panel API: Server management and user control
- Webhook Service: Action logging

Session Requirements:
------------------
All routes require:
- email: User's email address
- Admin status verification

Access Control:
-------------
All routes are protected by is_admin() verification
"""

from flask import Blueprint, render_template, request, session, redirect, url_for, flash
import scripts
from scripts import after_request
import time
import threading
import sys
import json

sys.path.append("..")

# Create a blueprint for the user routes
admin = Blueprint('admin', __name__)


@admin.route("/")
def admin_index():
    """
    Display admin dashboard homepage.
    
    Session Requirements:
        - email: User must be logged in
        
    Access Control:
        - User must be admin
        
    Templates:
        - admin/admin.html: Main admin dashboard
        
    Database Queries:
        - Verify admin status
        
    Returns:
        template: admin/admin.html
        str: Error message if not admin
        
    Related Functions:
        - is_admin(): Verifies admin privileges
    """
    if 'email' not in session:
        return redirect(url_for("user.login_user"))
    if not scripts.is_admin(session['email']):
        return "YOUR NOT ADMIN BRO"
    return render_template("admin/admin.html")


@admin.route("/users")
def users():
    """
    Display paginated list of users with efficient search.
    
    Query Parameters:
        - page: Current page number (default 1)
        - search: Optional search term
        
    Templates:
        - admin/users.html: User management interface
        
    Database Queries:
        - Get paginated user list
        - Count total users
        - Get user credits
        - Get server counts
        
    Process:
        1. Verify admin status
        2. Parse pagination parameters
        3. Execute search if term provided
        4. Fetch user data with credits
        5. Calculate server allocations
        
    Returns:
        template: admin/users.html with:
            - users: Paginated user list
            - total_users: Total user count
            - current_page: Active page number
            - search_term: Current search filter
            
    Related Functions:
        - get_user_credits(): Fetches credit balances
        - count_user_servers(): Gets server counts
    """
    if 'email' not in session:
        return redirect(url_for("user.login_user"))
    if not scripts.is_admin(session['email']):
        return "YOUR NOT ADMIN BRO"
    
    # Get query parameters
    page = int(request.args.get('page', 1))
    search_term = request.args.get('search', '').strip()
    per_page = 100
    
    # Prepare base queries
    base_query = "SELECT name, credits, role, email, suspended, id, pterodactyl_id FROM users"
    count_query = "SELECT COUNT(*) FROM users"
    
    # Prepare search and pagination parameters
    search_params = []
    
    # Apply search if term exists
    if search_term:
        base_query += " WHERE (name LIKE ? OR email LIKE ? OR CAST(id AS CHAR) LIKE ?)"
        count_query += " WHERE (name LIKE ? OR email LIKE ? OR CAST(id AS CHAR) LIKE ?)"
        search_params = [f'%{search_term}%', f'%{search_term}%', f'%{search_term}%']
    
    # Add pagination
    base_query += " LIMIT ? OFFSET ?"
    
    # Prepare final parameters
    if search_term:
        # For count query, use search params
        count_params = search_params
        # For main query, add pagination params
        query_params = search_params + [per_page, (page - 1) * per_page]
    else:
        # No search, just use pagination params
        count_params = []
        query_params = [per_page, (page - 1) * per_page]
    
    # Debug print
    print(f"Count Query: {count_query}")
    print(f"Count Params: {count_params}")
    print(f"Base Query: {base_query}")
    print(f"Query Params: {query_params}")
    
    # Execute count query
    total_users_result = scripts.use_database(count_query, count_params)
    total_users = total_users_result[0] if total_users_result else 0
    
    # Execute users query
    users_from_db = scripts.use_database(base_query, query_params, all=True)
    
    # Debug print
    print(f"Total Users: {total_users}")
    print(f"Users from DB: {users_from_db}")
    
    # Process users
    full_users = []
    if users_from_db:
        for user in users_from_db:
            full_users.append({
                "name": user[0], 
                "credits": int(user[1]), 
                "role": user[2], 
                "email": user[3], 
                "suspended": user[4],
                "id": user[5], 
                "panel_id": user[6]
            })
    
    # Calculate total pages
    total_pages = max(1, (total_users + per_page - 1) // per_page)
    
    return render_template(
        "admin/users.html", 
        users=full_users, 
        total_pages=total_pages, 
        current_page=page, 
        total_users=total_users,
        search_term=search_term
    )


@admin.route("/servers")
def admin_servers():
    """
    Display list of all servers in the panel.
    
    Templates:
        - admin/servers.html: Server overview list
        
    API Calls:
        - Pterodactyl: List all servers
        
    Database Queries:
        - Get server owners
        - Get server resources
        
    Process:
        1. Verify admin status
        2. Fetch all servers from panel
        3. Match servers with owners
        4. Calculate resource usage
        
    Returns:
        template: admin/servers.html with:
            - servers: List of all servers
            - resources: Server resource usage
            - owners: Server ownership mapping
            
    Related Functions:
        - get_server_list(): Fetches panel servers
        - get_server_owner(): Maps server to user
    """
    if 'email' not in session:
        return redirect(url_for("user.login_user"))
    if not scripts.is_admin(session['email']):
        return "YOUR NOT ADMIN BRO"
    resp = requests.get(f"{PTERODACTYL_URL}api/application/servers?per_page=10000", headers=HEADERS).json()
    return render_template("admin/servers.html", servers=resp['data'])


@admin.route('/user/<user_id>')
def admin_user(user_id):
    """
    Get detailed information about a specific user.
    
    Args:
        user_id: Pterodactyl user ID
        
    Templates:
        - admin/user.html: User detail view
        
    API Calls:
        - Pterodactyl: Get user details
        
    Database Queries:
        - Get user credits
        - Get user servers
        - Get support tickets
        
    Process:
        1. Verify admin status
        2. Fetch user from panel
        3. Get local user data
        4. Combine information
        
    Returns:
        json: User information including:
            - panel_info: Pterodactyl data
            - credits: Credit balance
            - servers: Server count
            - tickets: Support history
            
    Related Functions:
        - get_user_info(): Gets panel user data
        - get_user_resources(): Gets usage stats
    """
    if 'email' not in session:
        return redirect(url_for("user.login_user"))
    if not scripts.is_admin(session['email']):
        return "YOUR NOT ADMIN BRO"
    resp = requests.get(f"{PTERODACTYL_URL}api/application/users/{user_id}", headers=HEADERS).json()
    return resp


@admin.route('/server/<server_id>')
def admin_server(server_id):
    """
    Display detailed server information and management options.
    
    Args:
        server_id: Pterodactyl server ID
        
    Templates:
        - admin/server.html: Server management interface
        
    API Calls:
        - Pterodactyl: Get server details
        - Pterodactyl: Get resource usage
        
    Database Queries:
        - Get server configuration
        - Get owner information
        - Get available plans
        
    Process:
        1. Verify admin status
        2. Fetch server details
        3. Get resource utilization
        4. Load available plans
        5. Format display data
        
    Returns:
        template: admin/server.html with:
            - info: Server configuration
            - usage: Resource utilization
            - products: Available plans
            - owner: User information
            
    Related Functions:
        - get_server_info(): Gets server details
        - get_available_plans(): Lists upgrade options
    """
    if 'email' not in session:
        return redirect(url_for("user.login_user"))
    if not scripts.is_admin(session['email']):
        return "YOUR NOT ADMIN BRO"
    after_request(session=session, request=request.environ, require_login=True)

    if 'pterodactyl_id' in session:
        ptero_id = session['pterodactyl_id']
    else:
        ptero_id = scripts.get_ptero_id(session['email'])
        session['pterodactyl_id'] = ptero_id

    products_local = list(products)
    info = scripts.get_server_information(server_id)
    product = scripts.convert_to_product(info)
    return render_template('admin/server.html', info=info, products=products_local, product=product)


@admin.route('/tickets')
def admin_tickets_index():
    """
    Display list of all open support tickets.
    
    Templates:
        - admin/tickets.html: Ticket management interface
        
    Database Queries:
        - Get all open tickets
        - Get ticket authors
        - Get comment counts
        
    Process:
        1. Verify admin status
        2. Fetch open tickets
        3. Sort by priority
        4. Load user information
        5. Count responses
        
    Returns:
        template: admin/tickets.html with:
            - tickets: Open ticket list
            - authors: User information
            - responses: Comment counts
            - priorities: Priority levels
            
    Related Functions:
        - get_open_tickets(): Fetches tickets
        - get_ticket_responses(): Counts comments
    """
    if 'email' not in session:
        return redirect(url_for("user.login_user"))
    if not scripts.is_admin(session['email']):
        return "YOUR NOT ADMIN BRO"
    after_request(session=session, request=request.environ, require_login=True)
    if 'pterodactyl_id' in session:
        ptero_id = session['pterodactyl_id']
    else:
        ptero_id = scripts.get_ptero_id(session['email'])
        session['pterodactyl_id'] = ptero_id

    user_id = scripts.get_id(session['email'])
    tickets_list = scripts.use_database("SELECT * FROM tickets WHERE status = 'open'", all=True)

    return render_template('admin/tickets.html', tickets=tickets_list)


@admin.route('/user/<user_id>/servers')
def admin_user_servers(user_id):
    """
    Display all servers owned by a specific user.
    
    Args:
        user_id: User's ID to view servers for
        
    Templates:
        - admin/user_servers.html: User's server list
        
    API Calls:
        - Pterodactyl: Get user's servers
        - Pterodactyl: Get resource usage
        
    Database Queries:
        - Get user information
        - Get server configurations
        
    Process:
        1. Verify admin status
        2. Get user details
        3. Fetch server list
        4. Calculate resource usage
        5. Format display data
        
    Returns:
        template: admin/user_servers.html with:
            - user_info: Account details
            - servers: Server list
            - resources: Usage statistics
            
    Related Functions:
        - get_user_servers(): Lists user's servers
        - calculate_resources(): Sums usage
    """
    if 'email' not in session:
        return redirect(url_for("user.login_user"))
    if not scripts.is_admin(session['email']):
        return "YOU'RE NOT ADMIN BRO"

    # Get user info
    query = "SELECT name, email FROM users WHERE id = %s"
    user_info = scripts.use_database(query, (user_id,))
    if not user_info:
        flash("User not found")
        return redirect(url_for('admin.users'))

    user_info = {
        'name': user_info[0],
        'email': user_info[1]
    }

    # Get user's pterodactyl ID
    query = "SELECT pterodactyl_id FROM users WHERE id = %s"
    ptero_id = scripts.use_database(query, (user_id,))[0]

    # Get user's servers
    servers = scripts.list_servers(ptero_id)

    return render_template('admin/user_servers.html', servers=servers, user_info=user_info)


@admin.route('/server/<server_id>')
def admin_manage_server(server_id):
    """
    Display admin server management page.
    
    Args:
        server_id: Server ID to manage
        
    Templates:
        - admin/manage_server.html: Server management
        
    API Calls:
        - Pterodactyl: Get server details
        - Pterodactyl: Get resource limits
        
    Database Queries:
        - Get server configuration
        - Get owner information
        - Get server history
        
    Process:
        1. Verify admin status
        2. Load server details
        3. Get current limits
        4. Load modification options
        
    Returns:
        template: admin/manage_server.html with:
            - server: Server details
            - limits: Resource limits
            - history: Server changes
            - options: Available actions
            
    Related Functions:
        - get_server_details(): Gets configuration
        - get_server_history(): Lists changes
    """
    if 'email' not in session:
        return redirect(url_for("user.login_user"))
    if not scripts.is_admin(session['email']):
        return "YOU'RE NOT ADMIN"
        
    try:
        # Get server details from Pterodactyl
        server = scripts.get_server(server_id)
        if not server:
            flash("Server not found")
            return redirect(url_for('admin.users'))
            
        return render_template('admin/manage_server.html', server=server)
        
    except Exception as e:
        print(f"Error fetching server details: {e}")
        flash("Error fetching server details. Check logs for details.")
        return redirect(url_for('admin.users'))


@admin.route('/user/delete/<user_id>', methods=['POST'])
def admin_delete_user(user_id):
    """
    Delete a user and all associated data.
    
    Args:
        user_id: User's ID to delete
        
    API Calls:
        - Pterodactyl: Delete user
        - Pterodactyl: Delete servers
        
    Database Queries:
        - Delete user record
        - Delete tickets
        - Delete comments
        - Delete credits
        
    Process:
        1. Verify admin status
        2. Delete all user's servers
        3. Remove from panel
        4. Clear tickets/comments
        5. Remove from database
        6. Log deletion
        
    Returns:
        redirect: To admin users page with:
            - success: Deletion status
            - message: Result details
            
    Related Functions:
        - delete_user_servers(): Removes servers
        - clean_user_data(): Removes records
        - log_admin_action(): Records deletion
    """
    if 'email' not in session:
        return redirect(url_for("user.login_user"))
    if not scripts.is_admin(session['email']):
        return "YOU'RE NOT ADMIN"
        
    try:
        # Get user info
        query = "SELECT pterodactyl_id, email FROM users WHERE id = %s"
        user_info = scripts.use_database(query, (user_id,))
        if not user_info:
            flash("User not found")
            return redirect(url_for('admin.users'))
            
        ptero_id, user_email = user_info
        
        # Get and delete all user's servers
        servers = scripts.list_servers(ptero_id)
        for server in servers:
            server_id = server['attributes']['id']
            scripts.delete_server(server_id)
            
        # Delete user from Pterodactyl
        scripts.delete_user(ptero_id)
        
        # Delete user's tickets and comments
        scripts.use_database("DELETE FROM ticket_comments WHERE user_id = %s", (user_id,))
        scripts.use_database("DELETE FROM tickets WHERE user_id = %s", (user_id,))
        
        # Finally delete user from database
        scripts.use_database("DELETE FROM users WHERE id = %s", (user_id,))
        
        scripts.webhook_log(f"Admin `{session['email']}` deleted user `{user_email}`")
        flash("User and all associated data deleted successfully")
        
    except Exception as e:
        print(f"Error deleting user: {e}")
        flash("Error deleting user. Check logs for details.")
        
    return redirect(url_for('admin.users'))


@admin.route('/user/toggle_suspension/<user_id>', methods=['POST'])
def admin_toggle_suspension(user_id):
    """
    Toggle user suspension status.
    
    Args:
        user_id: User's ID to suspend/unsuspend
        
    API Calls:
        - Pterodactyl: Update user status
        
    Database Queries:
        - Update user status
        - Log status change
        
    Process:
        1. Verify admin status
        2. Get current status
        3. Toggle suspension
        4. Update panel
        5. Log change
        
    Returns:
        redirect: To admin users page with:
            - success: Update status
            - message: Action result
            
    Related Functions:
        - toggle_user_status(): Updates status
        - log_status_change(): Records action
    """
    if 'email' not in session:
        return redirect(url_for("user.login_user"))
    if not scripts.is_admin(session['email']):
        return "YOU'RE NOT ADMIN"
        
    try:
        # Get current suspension status
        query = "SELECT suspended, email FROM users WHERE id = %s"
        result = scripts.use_database(query, (user_id,))
        if not result:
            flash("User not found")
            return redirect(url_for('admin.users'))
            
        current_status, user_email = result
        new_status = 0 if current_status == 1 else 1
        
        # Update suspension status
        scripts.use_database("UPDATE users SET suspended = %s WHERE id = %s", (new_status, user_id))
        
        action = "suspended" if new_status == 1 else "unsuspended"
        scripts.webhook_log(f"Admin `{session['email']}` {action} user `{user_email}`")
        flash(f"User has been {action}.")
        
    except Exception as e:
        print(f"Error toggling suspension: {e}")
        flash("Error updating user status. Check logs for details.")
        
    return redirect(url_for('admin.users'))


@admin.route('/nodes')
def nodes():
    if 'email' not in session:
        return redirect(url_for('user.login'))
    if not scripts.is_admin(session['email']):
        return redirect(url_for('home'))
    nodes = scripts.get_nodes(all=True)
    return render_template('admin/nodes.html', nodes=nodes)


@admin.route('/node/<int:node_id>')
def node(node_id):
    if 'email' not in session:
        return redirect(url_for('user.login'))
    if not scripts.is_admin(session['email']):
        return redirect(url_for('home'))
    nodes = scripts.get_nodes(all=True)
    node = next((node for node in nodes if node['node_id'] == node_id), None)
    if not node:
        flash('Node not found')
        return redirect(url_for('admin.nodes'))
    
    # Get all servers and filter by node
    all_servers = scripts.get_all_servers()
    print(f"Total servers: {len(all_servers)}")
    node_servers = [server for server in all_servers if server['attributes']['node'] == node_id]
    print(f"Servers on node {node_id}: {len(node_servers)}")
    
    return render_template('admin/node.html', node=node, servers=node_servers, all_nodes=nodes)


def do_transfers(node_servers, num_servers, target_node):
    """Background task to handle server transfers with delays"""
    transferred = 0
    for server in node_servers[:num_servers]:
        status = scripts.transfer_server(server['attributes']['id'], target_node)
        if status == 204:  # Success status code
            transferred += 1
            if transferred < num_servers:
                time.sleep(10)  # 10 second delay between transfers


@admin.route('/node/<int:node_id>/transfer', methods=['POST'])
def transfer_servers(node_id):
    if 'email' not in session:
        return redirect(url_for('user.login'))
    if not scripts.is_admin(session['email']):
        return redirect(url_for('home'))
    
    num_servers = int(request.form.get('num_servers', 0))
    target_node = int(request.form.get('target_node', 0))
    
    if num_servers <= 0 or target_node == 0:
        flash('Invalid transfer request')
        return redirect(url_for('admin.node', node_id=node_id))
    
    # Get servers on this node
    all_servers = scripts.get_all_servers()
    node_servers = [server for server in all_servers if server['attributes']['node'] == node_id]
    
    # Start transfers in background thread
    transfer_thread = threading.Thread(
        target=do_transfers,
        args=(node_servers, num_servers, target_node)
    )
    transfer_thread.start()
    
    flash(f'Started transfer of {num_servers} servers. Transfers will happen every 10 seconds.')
    return redirect(url_for('admin.node', node_id=node_id))
