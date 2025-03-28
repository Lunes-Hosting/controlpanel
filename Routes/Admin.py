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

from flask import Blueprint, render_template, request, session, redirect, url_for, flash, jsonify
from managers.authentication import login_required, admin_required
from managers.user_manager import get_ptero_id, get_name, get_id, delete_user
from managers.server_manager import get_nodes, get_eggs, get_server_information, improve_list_servers, delete_server, transfer_server, get_all_servers, list_servers, get_server
from managers.credit_manager import convert_to_product
from managers.logging import webhook_log
from managers.database_manager import DatabaseManager
from config import PTERODACTYL_URL, PTERODACTYL_ADMIN_KEY
import sys
import json
import threading
import datetime
from security import safe_requests

sys.path.append("..")

# Create a blueprint for the user routes
admin = Blueprint('admin', __name__)


@admin.route("/")
@admin_required
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
    return render_template("admin/admin.html")


@admin.route("/users")
@admin_required
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
        base_query += " WHERE (name LIKE %s OR email LIKE %s OR CAST(id AS CHAR) LIKE %s)"
        count_query += " WHERE (name LIKE %s OR email LIKE %s OR CAST(id AS CHAR) LIKE %s)"
        search_params = [f'%{search_term}%', f'%{search_term}%', f'%{search_term}%']
    
    # Add pagination
    base_query += " LIMIT %s OFFSET %s"
    
    # Prepare final parameters
    if search_term:
        # For count query, use search params
        count_params = tuple(search_params)
        # For main query, add pagination params
        query_params = tuple(search_params + [per_page, (page - 1) * per_page])
    else:
        # No search, just use pagination params
        count_params = tuple()
        query_params = (per_page, (page - 1) * per_page)
    
    # Execute count query
    total_users_result = DatabaseManager.execute_query(count_query, count_params)
    total_users = total_users_result[0] if total_users_result else 0
    
    # Execute users query
    users_from_db = DatabaseManager.execute_query(base_query, query_params, fetch_all=True)
    
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
@admin_required
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
    resp = safe_requests.get(f"{PTERODACTYL_URL}api/application/servers?per_page=100000", headers=HEADERS, timeout=60).json()
    return render_template("admin/servers.html", servers=resp['data'])


@admin.route('/user/<user_id>')
@admin_required
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
    resp = safe_requests.get(f"{PTERODACTYL_URL}api/application/users/{user_id}", headers=HEADERS, timeout=60).json()
    return resp


@admin.route('/server/<server_id>')
@admin_required
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

    if 'pterodactyl_id' in session:
        ptero_id = session['pterodactyl_id']
    else:
        ptero_id = get_ptero_id(session['email'])
        session['pterodactyl_id'] = ptero_id

    products_local = list(products)
    info = get_server_information(server_id)
    product = convert_to_product(info)
    return render_template('admin/server.html', info=info, products=products_local, product=product)

@admin.route('/delete/<server_id>')
@admin_required
def admin_delete_server(server_id):

    webhook_log(f"ADMIN {session["email"]} deleted the pterodactyl server of id {server_id}", database_log=True)
    delete_server(server_id)

    return redirect(url_for("admin.admin_servers"))


@admin.route('/tickets')
@admin_required
def admin_tickets_index():
    """
    Display list of all support tickets with filter options.
    
    Query Parameters:
        - filter: Ticket status filter (default 'open')
        
    Templates:
        - admin/tickets.html: Ticket management interface
        
    Database Queries:
        - Get tickets based on filter
        - Get ticket authors
        - Get comment counts
        
    Process:
        1. Verify admin status
        2. Apply status filter
        3. Fetch filtered tickets
        4. Sort by priority
        5. Load user information
        6. Count responses
        
    Returns:
        template: admin/tickets.html with:
            - tickets: Filtered ticket list
            - filter: Current filter status
            - authors: User information
            - responses: Comment counts
            - priorities: Priority levels
            
    Related Functions:
        - get_tickets(): Fetches tickets
        - get_ticket_responses(): Counts comments
    """

    if 'pterodactyl_id' in session:
        ptero_id = session['pterodactyl_id']
    else:
        ptero_id = get_ptero_id(session['email'])
        session['pterodactyl_id'] = ptero_id

    # Get filter parameter, default to 'open'
    ticket_filter = request.args.get('filter', 'open')
    
    # Build query based on filter
    if ticket_filter == 'all':
        query = "SELECT * FROM tickets"
        params = ()
    else:
        query = "SELECT * FROM tickets WHERE status = %s"
        params = ('open',)
    
    # Get tickets based on filter
    tickets_list = DatabaseManager.execute_query(
        query, 
        params,
        fetch_all=True
    )
    
    # Calculate ticket age
    from datetime import datetime, timedelta
    current_time = datetime.now()
    old_ticket_threshold = timedelta(days=4)
    
    tickets_with_age = []
    for ticket in tickets_list:
        ticket_data = list(ticket)
        # Check if created_at timestamp exists and is not None
        if ticket[4] and isinstance(ticket[4], datetime):
            ticket_age = current_time - ticket[4]
            ticket_data.append(ticket_age > old_ticket_threshold)
        else:
            ticket_data.append(False)
        tickets_with_age.append(ticket_data)

    return render_template('admin/tickets.html', tickets=tickets_with_age, filter=ticket_filter)


@admin.route('/user/<user_id>/servers')
@admin_required
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
    # Get user info
    query = "SELECT name, email FROM users WHERE id = %s"
    user_info = DatabaseManager.execute_query(query, (user_id,))
    if not user_info:
        flash("User not found")
        return redirect(url_for('admin.users'))

    user_info = {
        'name': user_info[0],
        'email': user_info[1]
    }

    # Get user's pterodactyl ID
    query = "SELECT pterodactyl_id FROM users WHERE id = %s"
    ptero_id = DatabaseManager.execute_query(query, (user_id,))[0]

    # Get user's servers
    servers = improve_list_servers(ptero_id)

    return render_template('admin/user_servers.html', servers=servers, user_info=user_info)


@admin.route('/user/delete/<user_id>', methods=['POST'])
@admin_required
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

    try:
        # Get user info
        query = "SELECT pterodactyl_id, email FROM users WHERE id = %s"
        user_info = DatabaseManager.execute_query(query, (user_id,))
        if not user_info:
            flash("User not found")
            return redirect(url_for('admin.users'))
            
        ptero_id, user_email = user_info
        
        # Get and delete all user's servers
        servers = improve_list_servers(ptero_id)
        for server in servers:
            server_id = server['attributes']['id']
            delete_server(server_id)
            
        # Delete user from Pterodactyl
        delete_user(ptero_id)
        
        # Delete user's tickets and comments
        DatabaseManager.execute_query("DELETE FROM ticket_comments WHERE user_id = %s", (user_id,))
        DatabaseManager.execute_query("DELETE FROM tickets WHERE user_id = %s", (user_id,))
        
        # Finally delete user from database
        DatabaseManager.execute_query("DELETE FROM users WHERE id = %s", (user_id,))
        
        webhook_log(f"Admin `{session['email']}` deleted user `{user_email}`", 0, database_log=True)
        flash("User and all associated data deleted successfully")
        
    except Exception as e:
        print(f"Error deleting user: {e}")
        flash("Error deleting user. Check logs for details.")
        
    return redirect(url_for('admin.users'))


@admin.route('/user/toggle_suspension/<user_id>', methods=['POST'])
@admin_required
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

    try:
        # Get current suspension status
        query = "SELECT suspended, email FROM users WHERE id = %s"
        result = DatabaseManager.execute_query(query, (user_id,))
        if not result:
            flash("User not found")
            return redirect(url_for('admin.users'))
            
        current_status, user_email = result
        new_status = 0 if current_status == 1 else 1
        
        # Update suspension status
        DatabaseManager.execute_query("UPDATE users SET suspended = %s WHERE id = %s", (new_status, user_id))
        
        action = "suspended" if new_status == 1 else "unsuspended"
        webhook_log(f"Admin `{session['email']}` {action} user `{user_email}`", 0, database_log=True)
        flash(f"User has been {action}.")
        
    except Exception as e:
        print(f"Error toggling suspension: {e}")
        flash("Error updating user status. Check logs for details.")
        
    return redirect(url_for('admin.users'))


@admin.route('/nodes')
@admin_required
def nodes():
    nodes = get_nodes(all=True)
    return render_template('admin/nodes.html', nodes=nodes)


@admin.route('/node/<int:node_id>')
@admin_required
def node(node_id):

    nodes = get_nodes(all=True)
    node = next((node for node in nodes if node['node_id'] == node_id), None)
    if not node:
        flash('Node not found')
        return redirect(url_for('admin.nodes'))
    
    # Get all servers and filter by node
    all_servers = get_all_servers()
    print(f"Total servers: {len(all_servers)}")
    node_servers = [server for server in all_servers if server['attributes']['node'] == node_id]
    print(f"Servers on node {node_id}: {len(node_servers)}")
    
    return render_template('admin/node.html', node=node, servers=node_servers, all_nodes=nodes)


def do_transfers(node_servers, num_servers, target_node):
    """Background task to handle server transfers with delays"""
    transferred = 0
    for server in node_servers[:num_servers]:
        status = transfer_server(server['attributes']['id'], target_node)
        if status in [202, 204]:  # Accept both success status codes
            transferred += 1
            print(f"Successfully transferred server {server['attributes']['id']} ({transferred}/{num_servers})", 0)
            if transferred < num_servers:
                time.sleep(10)  # 10 second delay between transfers
        else:
            print(f"Failed to transfer server {server['attributes']['id']} - Status: {status}", 2)


@admin.route('/node/<int:node_id>/transfer', methods=['POST'])
@admin_required
def transfer_servers(node_id):
    num_servers = int(request.form.get('num_servers', 0))
    target_node = int(request.form.get('target_node', 0))
    
    if num_servers <= 0 or target_node == 0:
        flash('Invalid transfer request')
        return redirect(url_for('admin.node', node_id=node_id))
    
    # Get servers on this node
    all_servers = get_all_servers()
    node_servers = [server for server in all_servers if server['attributes']['node'] == node_id]
    
    # Start transfers in background thread
    transfer_thread = threading.Thread(
        target=do_transfers,
        args=(node_servers, num_servers, target_node)
    )
    transfer_thread.start()
    
    flash(f'Started transfer of {num_servers} servers. Transfers will happen every 10 seconds.')
    return redirect(url_for('admin.node', node_id=node_id))
