"""
Admin Node Management Routes
=================

This module handles the admin node management routes for the control panel.

Templates Used:
-------------
- admin/nodes.html: Node overview list
- admin/node.html: Individual node management

Database Tables Used:
------------------
- servers: Server configurations for tracking node assignments

External Services:
---------------
- Pterodactyl Panel API: Node management and server transfers

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
import requests
import sys
import threading
import time

sys.path.append("..")

@admin.route('/nodes')
@admin_required
def nodes():
    """
    Display list of all nodes in the panel.
    
    Templates:
        - admin/nodes.html: Node overview list
        
    API Calls:
        - Pterodactyl: List all nodes
        
    Database Queries:
        - Get node utilization
        
    Process:
        1. Verify admin status
        2. Fetch all nodes from panel
        3. Calculate resource usage
        4. Count servers per node
        
    Returns:
        template: admin/nodes.html with:
            - nodes: List of all nodes
            - resources: Node resource usage
            - servers: Server count per node
            
    Related Functions:
        - get_nodes(): Fetches panel nodes
        - calculate_node_usage(): Gets utilization
    """
    nodes = scripts.get_nodes(all=True)
    return render_template('admin/nodes.html', nodes=nodes)


@admin.route('/node/<int:node_id>')
@admin_required
def node(node_id):
    """
    Display detailed node information and management options.
    
    Args:
        node_id: Pterodactyl node ID
        
    Templates:
        - admin/node.html: Node management interface
        
    API Calls:
        - Pterodactyl: Get node details
        - Pterodactyl: Get servers on node
        
    Database Queries:
        - Get node configuration
        - Get server allocations
        
    Process:
        1. Verify admin status
        2. Fetch node details
        3. Get servers on node
        4. Calculate resource utilization
        5. Format display data
        
    Returns:
        template: admin/node.html with:
            - node: Node configuration
            - servers: Servers on node
            - all_nodes: List of all nodes
            
    Related Functions:
        - get_node_info(): Gets node details
        - get_node_servers(): Lists servers on node
    """
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
    """
    Background task to handle server transfers with delays.
    
    Args:
        node_servers: List of servers to transfer
        num_servers: Number of servers to transfer
        target_node: Target node ID
        
    Process:
        1. Iterate through servers
        2. Transfer each server
        3. Add delay between transfers
        4. Log results
    """
    transferred = 0
    for server in node_servers[:num_servers]:
        status = scripts.transfer_server(server['attributes']['id'], target_node)
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
    """
    Transfer servers from one node to another.
    
    Args:
        node_id: Source node ID
        
    Form Parameters:
        - num_servers: Number of servers to transfer
        - target_node: Target node ID
        
    Process:
        1. Verify admin status
        2. Validate parameters
        3. Get servers on source node
        4. Start background transfer thread
        5. Return to node page
        
    Returns:
        redirect: To node page with:
            - success: Transfer status
            - message: Action result
            
    Related Functions:
        - do_transfers(): Background transfer task
        - transfer_server(): Transfers individual server
    """
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
