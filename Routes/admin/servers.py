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
- servers: Server configurations
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
import scripts
from scripts import HEADERS, webhook_log, admin_required
from Routes.admin import admin
from managers.database_manager import DatabaseManager
from config import PTERODACTYL_URL
from products import products
import requests
import sys
import json

sys.path.append("..")

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
    resp = requests.get(f"{PTERODACTYL_URL}api/application/servers?per_page=10000", headers=HEADERS, timeout=60).json()
    return render_template("admin/servers.html", servers=resp['data'])


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
        ptero_id = scripts.get_ptero_id(session['email'])
        session['pterodactyl_id'] = ptero_id

    products_local = list(products)
    info = scripts.get_server_information(server_id)
    product = scripts.convert_to_product(info)
    return render_template('admin/server.html', info=info, products=products_local, product=product)


@admin.route('/delete/<server_id>')
@admin_required
def admin_delete_server(server_id):
    """
    Delete a server from the panel and database.
    
    Args:
        server_id: Server ID to delete
    """
    scripts.delete_server(server_id)
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
    if 'pterodactyl_id' in session:
        ptero_id = session['pterodactyl_id']
    else:
        ptero_id = scripts.get_ptero_id(session['email'])
        session['pterodactyl_id'] = ptero_id

    # Get server details from panel
    response = requests.get(
        f"{PTERODACTYL_URL}/api/application/servers/{server_id}",
        headers=HEADERS
    )
    
    if response.status_code != 200:
        flash("Server not found", "error")
        return redirect(url_for('admin.admin_servers'))
    
    server_info = response.json()
    
    # Get server from database
    db_server = DatabaseManager.execute_query(
        "SELECT * FROM servers WHERE pterodactyl_id = %s", 
        (server_id,)
    )
    
    if not db_server:
        flash("Server not found in database", "error")
        return redirect(url_for('admin.admin_servers'))
    
    # Get owner information
    owner = DatabaseManager.execute_query(
        "SELECT * FROM users WHERE id = %s", 
        (db_server[2],)
    )
    
    # Get available products
    products_list = list(products)
    
    return render_template(
        "admin/manage_server.html", 
        server=server_info['attributes'], 
        db_server=db_server, 
        owner=owner, 
        products=products_list
    )
