"""
Admin User Management Routes
=================

This module handles the admin user management routes for the control panel.

Templates Used:
-------------
- admin/users.html: User management interface
- admin/user.html: User detail view
- admin/user_servers.html: User's server list

Database Tables Used:
------------------
- users: User account management
- credits: User credit tracking

External Services:
---------------
- Pterodactyl Panel API: User management

Session Requirements:
------------------
All routes require:
- email: User's email address
- Admin status verification

Access Control:
-------------
All routes are protected by admin_required verification
"""

from flask import render_template, request, session, redirect, url_for, flash
from managers.authentication import admin_required
from managers.utils import HEADERS
from managers.logging import webhook_log
from Routes.admin import admin
from managers.user_manager import get_ptero_id
from managers.database_manager import DatabaseManager
from config import PTERODACTYL_URL
import requests
import sys
import json
from security import safe_requests

sys.path.append("..")

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


@admin.route('/user/<user_id>/servers')
@admin_required
def admin_user_servers(user_id):
    """
    Display all servers owned by a specific user.
    
    Args:
        user_id: Database user ID to view servers for
        
    Templates:
        - admin/user_servers.html: User's server list
        
    API Calls:
        - Pterodactyl: Get user's servers
        - Pterodactyl: Get resource usage
        
    Database Queries:
        - Get user information including Pterodactyl ID
        
    Process:
        1. Verify admin status
        2. Get user details from database
        3. Use Pterodactyl ID to fetch server list from Pterodactyl
        4. Format display data
        
    Returns:
        template: admin/user_servers.html with:
            - user_info: Account details
            - servers: Server list
    """
    if 'pterodactyl_id' in session:
        admin_ptero_id = session['pterodactyl_id']
    else:
        admin_ptero_id = get_ptero_id(session['email'])
        session['pterodactyl_id'] = admin_ptero_id
    
    # Get user from database using database ID
    db_user = DatabaseManager.execute_query(
        "SELECT * FROM users WHERE id = %s", 
        (user_id,)
    )
    
    if not db_user:
        return "User not found in database", 404
    
    # Get the Pterodactyl ID from the database user
    ptero_id = db_user[5]  # Assuming pterodactyl_id is at index 6
    
    if not ptero_id:
        return "User does not have a Pterodactyl ID", 404
    
    # Get user's servers from panel using Pterodactyl ID
    response = safe_requests.get(
        f"{PTERODACTYL_URL}/api/application/users/{ptero_id}?include=servers",
        headers=HEADERS, 
    timeout=60)
    
    if response.status_code != 200:
        return "Failed to get user servers", 500
    
    user_info = response.json()
    
    # Process servers from Pterodactyl API only
    servers = []
    for server in user_info['attributes']['relationships']['servers']['data']:
        server_info = server['attributes']
        
        # Create a server object that matches what the template expects
        servers.append({
            'id': server_info['id'],
            'identifier': server_info['identifier'],
            'name': server_info['name'],
            'node': server_info['node'],
            'limits': server_info['limits'],
            'db_info': None,  # No database info since we don't have a servers table
            'attributes': server_info  # Add the full attributes for template compatibility
        })
    
    return render_template(
        "admin/user_servers.html", 
        user_info=user_info['attributes'], 
        db_user=db_user, 
        servers=servers
    )


@admin.route('/user/<user_id>/delete', methods=['POST'])
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
    if 'pterodactyl_id' in session:
        ptero_id = session['pterodactyl_id']
    else:
        ptero_id = get_ptero_id(session['email'])
        session['pterodactyl_id'] = ptero_id
    
    # Get user from database
    db_user = DatabaseManager.execute_query(
        "SELECT * FROM users WHERE pterodactyl_id = %s", 
        (user_id,)
    )
    
    if not db_user:
        flash("User not found in database", "error")
        return redirect(url_for('admin.users'))
    
    # Get user's servers from panel
    response = safe_requests.get(
        f"{PTERODACTYL_URL}/api/application/users/{user_id}?include=servers",
        headers=HEADERS, 
    timeout=60)
    
    if response.status_code != 200:
        flash("Failed to get user servers", "error")
        return redirect(url_for('admin.users'))
    
    user_info = response.json()
    
    # Delete each server
    try:
        servers_data = user_info['attributes']['relationships']['servers']['data']
        for server in servers_data:
            server_id = server['id']
            
            # Delete server from panel
            delete_response = requests.delete(
                f"{PTERODACTYL_URL}/api/application/servers/{server_id}",
                headers=HEADERS,
                params={'force': 'true'}, 
            timeout=60)
            
            if delete_response.status_code != 204:
                flash(f"Failed to delete server {server_id}", "error")
                return redirect(url_for('admin.users'))
    except Exception as e:
        print(f"Error deleting servers: {str(e)}")
        webhook_log(f"Error deleting servers for user {user_id}: {str(e)}", 2)
        flash(f"Error deleting servers: {str(e)}", "error")
        return redirect(url_for('admin.users'))
    
    # Delete user from panel
    delete_user_response = requests.delete(
        f"{PTERODACTYL_URL}/api/application/users/{user_id}",
        headers=HEADERS, 
    timeout=60)
    
    if delete_user_response.status_code != 204:
        flash("Failed to delete user from panel", "error")
        return redirect(url_for('admin.users'))
    
    # Delete user's tickets
    DatabaseManager.execute_query(
        "DELETE FROM tickets WHERE user_id = %s", 
        (db_user[5],)
    )
    
    # Delete user's ticket comments
    DatabaseManager.execute_query(
        "DELETE FROM ticket_comments WHERE user_id = %s", 
        (db_user[5],)
    )
    
    # Delete user from database
    DatabaseManager.execute_query(
        "DELETE FROM users WHERE id = %s", 
        (db_user[5],)
    )
    
    # Log the action
    webhook_log(
        f"Admin {session['email']} deleted user {db_user[3]} (ID: {db_user[5]})",
        "admin"
    )
    
    flash(f"User {db_user[3]} has been deleted", "success")
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
    if 'pterodactyl_id' in session:
        ptero_id = session['pterodactyl_id']
    else:
        ptero_id = get_ptero_id(session['email'])
        session['pterodactyl_id'] = ptero_id
    
    # Get user from database
    db_user = DatabaseManager.execute_query(
        "SELECT * FROM users WHERE pterodactyl_id = %s", 
        (user_id,)
    )
    
    if not db_user:
        flash("User not found in database", "error")
        return redirect(url_for('admin.users'))
    
    # Get current suspension status
    current_status = db_user[9]  # Assuming suspended is at index 9
    new_status = 0 if current_status == 1 else 1
    
    # Update user in database
    DatabaseManager.execute_query(
        "UPDATE users SET suspended = %s WHERE id = %s", 
        (new_status, db_user[5])
    )
    
    # Update user in panel
    response = safe_requests.get(
        f"{PTERODACTYL_URL}/api/application/users/{user_id}",
        headers=HEADERS, 
    timeout=60)
    
    if response.status_code != 200:
        flash("Failed to get user from panel", "error")
        return redirect(url_for('admin.users'))
    
    user_info = response.json()
    
    # Prepare update data
    update_data = {
        "email": user_info['attributes']['email'],
        "username": user_info['attributes']['username'],
        "first_name": user_info['attributes']['first_name'],
        "last_name": user_info['attributes']['last_name'],
        "language": user_info['attributes']['language'],
        "root_admin": user_info['attributes']['root_admin'],
        "password": "",
    }
    
    # Set suspension status
    if new_status == 1:
        update_data["suspended"] = True
    else:
        update_data["suspended"] = False
    
    # Update user in panel
    update_response = requests.patch(
        f"{PTERODACTYL_URL}/api/application/users/{user_id}",
        headers=HEADERS,
        json=update_data, 
    timeout=60)
    
    if update_response.status_code != 200:
        flash("Failed to update user in panel", "error")
        return redirect(url_for('admin.users'))
    
    # Log the action
    action = "suspended" if new_status == 1 else "unsuspended"
    webhook_log(
        f"Admin {session['email']} {action} user {db_user[3]} (ID: {db_user[5]})",
        "admin"
    )
    
    flash(f"User {db_user[3]} has been {action}", "success")
    return redirect(url_for('admin.users'))
