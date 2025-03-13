"""
Admin Server Management Routes
=================

This module handles the admin server management routes for the control panel.

Templates Used:
-------------
- admin/servers.html: Server overview list
- admin/server.html: Individual server management
- admin/manage_server.html: Detailed server management

Database Tables Used:
------------------
- users: User account management

External Services:
---------------
- Pterodactyl Panel API: Server management and control

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
from managers.user_manager import get_ptero_id
from managers.server_manager import get_server_information, delete_server
from managers.credit_manager import convert_to_product
from Routes.admin import admin
from managers.database_manager import DatabaseManager
from config import PTERODACTYL_URL
from products import products
import requests
import sys
sys.path.append("..")

@admin.route("/servers")
@admin_required
def admin_servers():
    """
    Display paginated list of all servers in the panel with search functionality.
    
    Query Parameters:
        - page: Current page number (default 1)
        - search: Optional search term for server ID or name
        
    Templates:
        - admin/servers.html: Server overview list
        
    API Calls:
        - Pterodactyl: List all servers
        
    Returns:
        template: admin/servers.html with:
            - servers: Paginated server list
            - total_servers: Total server count
            - current_page: Active page number
            - search_term: Current search filter
    """
    # Get query parameters
    page = int(request.args.get('page', 1))
    search_term = request.args.get('search', '').strip().lower()  # Convert to lowercase once
    per_page = 20
    
    # Get all servers from Pterodactyl
    resp = requests.get(f"{PTERODACTYL_URL}api/application/servers?per_page=100000", headers=HEADERS, timeout=60).json()
    all_servers = resp['data']
    
    # Filter servers based on search term (server ID or name)
    filtered_servers = []
    for server in all_servers:
        server_id = str(server['attributes']['id']).lower()
        server_name = str(server['attributes']['name']).lower()
        
        # Apply search filter if search term provided
        if search_term:
            if search_term in server_id or search_term in server_name:
                filtered_servers.append(server)
        else:
            filtered_servers.append(server)
    
    # Calculate total servers and pages
    total_servers = len(filtered_servers)
    total_pages = max(1, (total_servers + per_page - 1) // per_page)
    
    # Adjust page if out of bounds
    if page > total_pages:
        page = total_pages
    
    # Paginate the filtered servers
    start_idx = (page - 1) * per_page
    end_idx = start_idx + per_page
    paginated_servers = filtered_servers[start_idx:end_idx]
    
    return render_template(
        "admin/servers.html", 
        servers=paginated_servers, 
        total_pages=total_pages, 
        current_page=page, 
        total_servers=total_servers,
        search_term=request.args.get('search', '')  # Return original search term
    )


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
    """
    Delete a server from the panel and database.
    
    Args:
        server_id: Server ID to delete
    """
    delete_server(server_id)
    return redirect(url_for('admin.admin_servers'))


@admin.route('/manage/<server_id>')
@admin_required
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
        - Get owner information
        
    Process:
        1. Verify admin status
        2. Load server details
        3. Get current limits
        4. Load modification options
        
    Returns:
        template: admin/manage_server.html with:
            - server: Server details
            - limits: Resource limits
            - options: Available actions
            
    Related Functions:
        - get_server_details(): Gets configuration
    """
    if 'pterodactyl_id' in session:
        ptero_id = session['pterodactyl_id']
    else:
        ptero_id = get_ptero_id(session['email'])
        session['pterodactyl_id'] = ptero_id

    # Get server details from panel
    response = requests.get(
        f"{PTERODACTYL_URL}/api/application/servers/{server_id}",
        headers=HEADERS, 
    timeout=60)
    
    if response.status_code != 200:
        flash("Server not found", "error")
        return redirect(url_for('admin.admin_servers'))
    
    server_info = response.json()
    
    # Get owner information directly from the server attributes
    owner_id = server_info['attributes']['user']
    owner = DatabaseManager.execute_query(
        "SELECT * FROM users WHERE pterodactyl_id = %s", 
        (owner_id,)
    )
    
    # Get available products
    products_list = list(products)
    
    # Structure the server data to match what the template expects
    server = {
        'attributes': server_info['attributes']
    }
    
    return render_template(
        "admin/manage_server.html", 
        server=server, 
        owner=owner, 
        products=products_list
    )
