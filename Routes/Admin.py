from flask import Blueprint, request, render_template, session, flash
import sys

sys.path.append("..")
from scripts import *
import json

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
        
    Returns:
        template: admin/admin.html
        str: Error message if not admin
    """
    if 'email' not in session:
        return redirect(url_for("user.login_user"))
    if not is_admin(session['email']):
        return "YOUR NOT ADMIN BRO"
    return render_template("admin/admin.html")


@admin.route("/users")
def users():
    """
    Display paginated list of users with efficient search.
    
    Query Parameters:
    - page: Current page number (default 1)
    - search: Optional search term
    
    Returns:
    - Paginated users list
    - Total user count
    - Current page
    """
    if 'email' not in session:
        return redirect(url_for("user.login_user"))
    if not is_admin(session['email']):
        return "YOUR NOT ADMIN BRO"
    
    # Get query parameters
    page = int(request.args.get('page', 1))
    search_term = request.args.get('search', '').strip()
    per_page = 100
    
    # Base query
    base_query = "SELECT name, credits, role, email, suspended, id, pterodactyl_id FROM users"
    count_query = "SELECT COUNT(*) FROM users"
    
    # Apply search if term exists
    if search_term:
        search_condition = " WHERE (name LIKE ? OR email LIKE ? OR id LIKE ?)"
        search_params = (f'%{search_term}%', f'%{search_term}%', f'%{search_term}%')
        base_query += search_condition
        count_query += search_condition
    else:
        search_params = ()
    
    # Add pagination
    offset = (page - 1) * per_page
    base_query += " LIMIT ? OFFSET ?"
    
    # Execute count query
    total_users = use_database(count_query, search_params)[0]
    
    # Execute users query
    users_from_db = use_database(base_query, search_params + (per_page, offset), all=True)
    
    # Process users
    full_users = []
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
    total_pages = (total_users + per_page - 1) // per_page
    
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
    
    Session Requirements:
        - email: User must be logged in
        
    Access Control:
        - User must be admin
        
    Returns:
        template: admin/servers.html with list of all servers
        str: Error message if not admin
    """
    if 'email' not in session:
        return redirect(url_for("user.login_user"))
    if not is_admin(session['email']):
        return "YOUR NOT ADMIN BRO"
    resp = requests.get(f"{PTERODACTYL_URL}api/application/servers?per_page=10000", headers=HEADERS).json()
    return render_template("admin/servers.html", servers=resp['data'])


@admin.route('/user/<user_id>')
def admin_user(user_id):
    """
    Get detailed information about a specific user.
    
    Session Requirements:
        - email: User must be logged in
        
    Access Control:
        - User must be admin
        
    Args:
        user_id: Pterodactyl user ID
        
    Returns:
        json: User information from Pterodactyl API
        str: Error message if not admin
    """
    if 'email' not in session:
        return redirect(url_for("user.login_user"))
    if not is_admin(session['email']):
        return "YOUR NOT ADMIN BRO"
    resp = requests.get(f"{PTERODACTYL_URL}api/application/users/{user_id}", headers=HEADERS).json()
    return resp


@admin.route('/server/<server_id>')
def admin_server(server_id):
    """
    Display detailed server information and management options.
    
    Session Requirements:
        - email: User must be logged in
        - pterodactyl_id: User's panel ID
        
    Access Control:
        - User must be admin
        
    Args:
        server_id: Pterodactyl server ID
        
    Returns:
        template: admin/server.html with:
            - info: Server details
            - products: Available upgrade options
            - product: Current server configuration
        str: Error message if not admin
    """
    if 'email' not in session:
        return redirect(url_for("user.login_user"))
    if not is_admin(session['email']):
        return "YOUR NOT ADMIN BRO"
    after_request(session=session, request=request.environ, require_login=True)

    if 'pterodactyl_id' in session:
        ptero_id = session['pterodactyl_id']
    else:
        ptero_id = get_ptero_id(session['email'])
        session['pterodactyl_id'] = ptero_id

    products_local = list(products)
    info = get_server_information(server_id)
    product = convert_to_product(info)
    return render_template('admin/server.html', info=info, products=products_local, product=product)


@admin.route('/tickets')
def admin_tickets_index():
    """
    Display list of all open support tickets.
    
    Session Requirements:
        - email: User must be logged in
        - pterodactyl_id: User's panel ID
        
    Access Control:
        - User must be admin
        
    Returns:
        template: admin/tickets.html with list of open tickets
        str: Error message if not admin
    """
    if 'email' not in session:
        return redirect(url_for("user.login_user"))
    if not is_admin(session['email']):
        return "YOUR NOT ADMIN BRO"
    after_request(session=session, request=request.environ, require_login=True)
    if 'pterodactyl_id' in session:
        ptero_id = session['pterodactyl_id']
    else:
        ptero_id = get_ptero_id(session['email'])
        session['pterodactyl_id'] = ptero_id

    user_id = get_id(session['email'])
    tickets_list = use_database("SELECT * FROM tickets WHERE status = 'open'", all=True)

    return render_template('admin/tickets.html', tickets=tickets_list)


@admin.route('/user/<user_id>/servers')
def admin_user_servers(user_id):
    """
    Display all servers owned by a specific user.
    
    Session Requirements:
        - email: User must be logged in
        
    Access Control:
        - User must be admin
        
    Args:
        user_id: User's ID to view servers for
        
    Returns:
        template: admin/user_servers.html with:
            - user_info: Basic user information
            - servers: List of user's servers
    """
    if 'email' not in session:
        return redirect(url_for("user.login_user"))
    if not is_admin(session['email']):
        return "YOU'RE NOT ADMIN BRO"

    # Get user info
    query = "SELECT name, email FROM users WHERE id = %s"
    user_info = use_database(query, (user_id,))
    if not user_info:
        flash("User not found")
        return redirect(url_for('admin.users'))

    user_info = {
        'name': user_info[0],
        'email': user_info[1]
    }

    # Get user's pterodactyl ID
    query = "SELECT pterodactyl_id FROM users WHERE id = %s"
    ptero_id = use_database(query, (user_id,))[0]

    # Get user's servers
    servers = list_servers(ptero_id)

    return render_template('admin/user_servers.html', servers=servers, user_info=user_info)


@admin.route('/server/<server_id>')
def admin_manage_server(server_id):
    """
    Display admin server management page.
    
    Session Requirements:
        - email: User must be logged in
        
    Access Control:
        - User must be admin
        
    Args:
        server_id: Server ID to manage
        
    Returns:
        template: admin/manage_server.html with server details
    """
    if 'email' not in session:
        return redirect(url_for("user.login_user"))
    if not is_admin(session['email']):
        return "YOU'RE NOT ADMIN"
        
    try:
        # Get server details from Pterodactyl
        server = get_server(server_id)
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
    
    Session Requirements:
        - email: User must be logged in
        
    Access Control:
        - User must be admin
        
    Args:
        user_id: User's ID to delete
        
    Process:
        1. Verify admin status
        2. Delete all user's servers
        3. Delete user from Pterodactyl
        4. Delete user's tickets and comments
        5. Delete user from database
        
    Returns:
        redirect: To admin users page with status message
    """
    if 'email' not in session:
        return redirect(url_for("user.login_user"))
    if not is_admin(session['email']):
        return "YOU'RE NOT ADMIN"
        
    try:
        # Get user info
        query = "SELECT pterodactyl_id, email FROM users WHERE id = %s"
        user_info = use_database(query, (user_id,))
        if not user_info:
            flash("User not found")
            return redirect(url_for('admin.users'))
            
        ptero_id, user_email = user_info
        
        # Get and delete all user's servers
        servers = list_servers(ptero_id)
        for server in servers:
            server_id = server['attributes']['id']
            delete_server(server_id)
            
        # Delete user from Pterodactyl
        delete_user(ptero_id)
        
        # Delete user's tickets and comments
        use_database("DELETE FROM ticket_comments WHERE user_id = %s", (user_id,))
        use_database("DELETE FROM tickets WHERE user_id = %s", (user_id,))
        
        # Finally delete user from database
        use_database("DELETE FROM users WHERE id = %s", (user_id,))
        
        webhook_log(f"Admin `{session['email']}` deleted user `{user_email}`")
        flash("User and all associated data deleted successfully")
        
    except Exception as e:
        print(f"Error deleting user: {e}")
        flash("Error deleting user. Check logs for details.")
        
    return redirect(url_for('admin.users'))


@admin.route('/user/toggle_suspension/<user_id>', methods=['POST'])
def admin_toggle_suspension(user_id):
    """
    Toggle user suspension status.
    
    Session Requirements:
        - email: User must be logged in
        
    Access Control:
        - User must be admin
        
    Args:
        user_id: User's ID to suspend/unsuspend
        
    Process:
        1. Verify admin status
        2. Toggle user's suspension status
        3. Log the action
        
    Returns:
        redirect: To admin users page with status message
    """
    if 'email' not in session:
        return redirect(url_for("user.login_user"))
    if not is_admin(session['email']):
        return "YOU'RE NOT ADMIN"
        
    try:
        # Get current suspension status
        query = "SELECT suspended, email FROM users WHERE id = %s"
        result = use_database(query, (user_id,))
        if not result:
            flash("User not found")
            return redirect(url_for('admin.users'))
            
        current_status, user_email = result
        new_status = 0 if current_status == 1 else 1
        
        # Update suspension status
        use_database("UPDATE users SET suspended = %s WHERE id = %s", (new_status, user_id))
        
        action = "suspended" if new_status == 1 else "unsuspended"
        webhook_log(f"Admin `{session['email']}` {action} user `{user_email}`")
        flash(f"User has been {action}.")
        
    except Exception as e:
        print(f"Error toggling suspension: {e}")
        flash("Error updating user status. Check logs for details.")
        
    return redirect(url_for('admin.users'))
